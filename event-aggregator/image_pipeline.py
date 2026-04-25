"""
Image/PDF intake pipeline — orchestrator.

As of 2026-04-24, this module only provides `ingest_local_file()`, invoked by
the `main.py ingest-image --file <path>` CLI (called by the dispatcher when an
Events-classified file arrives in #ian-image-intake). The previous
Slack-channel-scanning path was retired; see `connectors/slack.py` (no longer
has fetch_files) and `Home-Tools/dispatcher/`.

Classification, OCR, and calendar extraction run locally (qwen2.5vl:7b via
Ollama); no cloud models are involved.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import config
import state as state_module
from analyzers import image_analyzer
from dedup import fingerprint
from notifiers import slack_notifier
from writers import file_writer

logger = logging.getLogger(__name__)


def _candidate_to_proposal_item(candidate, num: int, conflicts: list[str]) -> dict:
    """Serialize a CandidateEvent into a storable proposal dict (mirrors main.py)."""
    return {
        "num": num,
        "status": "pending",
        "title": candidate.title,
        "start_dt": candidate.start_dt.isoformat(),
        "end_dt": candidate.end_dt.isoformat() if candidate.end_dt else None,
        "location": candidate.location,
        "confidence": candidate.confidence,
        "confidence_band": candidate.confidence_band,
        "category": candidate.category,
        "source": candidate.source,
        "source_id": candidate.source_id,
        "source_url": candidate.source_url,
        "fingerprint": fingerprint(candidate),
        "is_update": candidate.is_update,
        "is_cancellation": candidate.is_cancellation,
        "original_title_hint": candidate.original_title_hint,
        "gcal_event_id_to_update": candidate.gcal_event_id_to_update,
        "is_recurring": candidate.is_recurring,
        "recurrence_hint": candidate.recurrence_hint,
        "suggested_attendees": candidate.suggested_attendees or [],
        "conflicts": conflicts,
    }


def ingest_local_file(
    file_path: Path,
    state: state_module.State,
    dry_run: bool = False,
    mock: bool = False,
) -> str:
    """Run the full intake pipeline on a file sitting on local disk.

    Classifies, rasterizes PDFs, stages, NAS-copies, and proposes calendar
    events. Returns a one-line human-readable summary.
    """
    filename = file_path.name
    file_bytes = file_path.read_bytes()
    mimetype = _mimetype_for_ext(file_path.suffix.lower())

    pages = image_analyzer.rasterize_to_pages(file_bytes, filename, mimetype)

    analysis = image_analyzer.analyze_document(
        pages=pages,
        accompanying_text="",
        mock=mock,
    )
    if analysis is None:
        return f":x: analysis returned None for {filename}"

    sha = hashlib.sha256(file_bytes).hexdigest()[:12]
    staging_id = f"local_{sha}"
    analysis.file_id = staging_id
    analysis.original_filename = filename

    if analysis.confidence < config.IMAGE_CONFIDENCE_MIN:
        analysis.primary_category = "Documents"
        analysis.subcategory = None

    staging_path = file_writer.stage_document_locally(analysis, pages, staging_id)
    nas_path = file_writer.copy_to_nas(staging_path, analysis, dry_run=dry_run)
    if nas_path and not dry_run:
        file_writer.purge_staging(staging_path)

    events_proposed = 0
    now = datetime.now(timezone.utc)

    if config.EVENT_APPROVAL_MODE == "propose" and not dry_run and not mock:
        batch_items = []
        for candidate in analysis.calendar_items:
            if candidate.start_dt < now:
                continue
            fp = fingerprint(candidate)
            if state.has_fingerprint(fp):
                continue
            num = state.next_proposal_num()
            batch_items.append(_candidate_to_proposal_item(candidate, num, []))
            state.add_fingerprint(fp)
        if batch_items:
            batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M:%S_img")
            batch = {
                "batch_id": batch_id,
                "slack_ts": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "items": batch_items,
            }
            state.add_proposal_batch(batch)
            day_thread_ts = slack_notifier.get_or_create_day_thread(state)
            if day_thread_ts:
                posted_ts = slack_notifier.post_proposals(day_thread_ts, batch_items)
                if posted_ts:
                    state.set_proposal_slack_ts(batch_id, posted_ts)
            events_proposed = len(batch_items)

    return (
        f":white_check_mark: {filename} → "
        f"{analysis.primary_category}"
        + (f"/{analysis.subcategory}" if analysis.subcategory else "")
        + (f" (NAS: {nas_path})" if nas_path else f" (staged: {staging_path})")
        + (f" | {events_proposed} event(s) proposed" if events_proposed else "")
    )


def _mimetype_for_ext(ext: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")
