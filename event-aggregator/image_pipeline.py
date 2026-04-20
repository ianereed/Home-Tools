"""
Image/PDF intake pipeline — orchestrator.

Detects image and PDF uploads in the Slack notify channel, analyzes them via
Gemini vision, stages locally, copies to NAS, creates calendar events, and
replies in the original Slack thread.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import config
import state as state_module
from analyzers import image_analyzer
from connectors.slack import SlackConnector
from dedup import fingerprint
from notifiers import slack_notifier
from writers import file_writer
from writers import google_calendar as gcal_writer

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


def process_slack_files(
    state: state_module.State,
    dry_run: bool = False,
    mock: bool = False,
) -> dict:
    """Process image/PDF uploads from the Slack notify channel.

    Returns a summary dict with counts of actions taken.
    """
    result = {
        "processed": 0,
        "calendar_created": 0,
        "calendar_proposed": 0,
        "nas_written": 0,
        "staged_pending": 0,
        "flushed": 0,
        "errors": 0,
    }

    # ── Flush any backlog from prior NAS-offline runs ───────────────────────
    try:
        flushed = file_writer.flush_pending_staged(dry_run=dry_run)
        result["flushed"] = len(flushed)
        if flushed:
            logger.info("Flushed %d previously staged file(s) to NAS", len(flushed))
    except Exception as exc:
        logger.warning("Error flushing staged files: %s", exc)

    # ── Fetch file messages from Slack ──────────────────────────────────────
    connector = SlackConnector()
    since = state.last_run("slack_file")
    logger.info("Fetching Slack files since %s (mock=%s)", since.date(), mock)
    messages = connector.fetch_files(since=since, mock=mock)
    logger.info("  → %d file message(s)", len(messages))

    if not messages:
        return result

    snapshot = state.calendar_snapshot()

    for msg in messages:
        files = msg.metadata.get("files", [])
        msg_ts = msg.metadata.get("msg_ts", "")
        is_thread = msg.metadata.get("is_thread_collection", False)
        auto_processed = msg.metadata.get("auto_processed", False)

        # Skip if all files in this message are already processed
        unprocessed = [f for f in files if not state.is_file_processed(f["id"])]
        if not unprocessed:
            logger.debug("All files in message %s already processed", msg_ts)
            continue

        try:
            if len(files) > 1 or is_thread:
                # Multi-page document (multi-file message or thread collection)
                _process_document(
                    files=files,
                    accompanying_text=msg.body_text,
                    msg_ts=msg_ts,
                    staging_id=msg.metadata.get("thread_id") or f"doc_{msg_ts.replace('.', '_')}",
                    auto_processed=auto_processed,
                    state=state,
                    snapshot=snapshot,
                    connector=connector,
                    dry_run=dry_run,
                    mock=mock,
                    result=result,
                )
            else:
                # Single file — existing behavior
                _process_single_file(
                    file_info=files[0],
                    accompanying_text=msg.body_text,
                    msg_ts=msg_ts,
                    state=state,
                    snapshot=snapshot,
                    connector=connector,
                    dry_run=dry_run,
                    mock=mock,
                    result=result,
                )
        except Exception as exc:
            logger.warning("Error processing message %s: %s", msg_ts, exc)
            result["errors"] += 1

    return result


def _process_document(
    files: list[dict],
    accompanying_text: str,
    msg_ts: str,
    staging_id: str,
    auto_processed: bool,
    state: state_module.State,
    snapshot: dict,
    connector,
    dry_run: bool,
    mock: bool,
    result: dict,
) -> None:
    """Process multiple files as a single multi-page document."""
    n = len(files)
    logger.info("Processing %d-page document (staging_id=%s)", n, staging_id)

    # ── Download all pages ──────────────────────────────────────────────────
    pages: list[tuple[bytes, str, str]] = []
    for file_info in files:
        filename = file_info.get("name", "unknown")
        mimetype = file_info.get("mimetype", "")
        if mock:
            pages.append((b"MOCK_FILE_CONTENT_FOR_TESTING", filename, mimetype))
        else:
            url = file_info.get("url_private_download", "")
            if not url:
                logger.warning("No download URL for file %s — skipping page", file_info["id"])
                continue
            file_bytes = connector.download_file(url)
            pages.append((file_bytes, filename, mimetype))

    if not pages:
        logger.warning("No pages downloaded for document %s", staging_id)
        result["errors"] += 1
        return

    # ── Analyze via Gemini (multi-image call) ───────────────────────────────
    analysis = image_analyzer.analyze_document(
        pages=pages,
        accompanying_text=accompanying_text,
        mock=mock,
    )
    if analysis is None:
        logger.warning("Document analysis returned None for %s — skipping", staging_id)
        result["errors"] += 1
        return

    # Use staging_id as the document identifier
    analysis.file_id = staging_id
    analysis.source_slack_ts = msg_ts
    analysis.original_filename = f"{n}-page document"

    if analysis.confidence < config.IMAGE_CONFIDENCE_MIN:
        logger.info(
            "Low confidence (%.2f) for document %s — routing to Documents/Unsorted",
            analysis.confidence, staging_id,
        )
        analysis.primary_category = "Documents"
        analysis.subcategory = None

    # ── Stage locally ───────────────────────────────────────────────────────
    staging_path = file_writer.stage_document_locally(analysis, pages, staging_id)

    # ── Copy to NAS ─────────────────────────────────────────────────────────
    nas_path = file_writer.copy_to_nas(staging_path, analysis, dry_run=dry_run)
    if nas_path:
        if not dry_run:
            file_writer.purge_staging(staging_path)
        result["nas_written"] += 1
    else:
        result["staged_pending"] += 1

    # ── Create or propose calendar events ──────────────────────────────────
    events_created = 0
    events_proposed = 0
    now = datetime.now(timezone.utc)
    if config.EVENT_APPROVAL_MODE == "propose" and not dry_run and not mock:
        batch_items = []
        for candidate in analysis.calendar_items:
            if candidate.start_dt < now:
                logger.info("Skipping past calendar event from document: %r on %s", candidate.title, candidate.start_dt.date())
                continue
            fp = fingerprint(candidate)
            if state.has_fingerprint(fp):
                logger.debug("Skipping duplicate calendar event from document: %r", candidate.title)
                continue
            num = state.next_proposal_num()
            item = _candidate_to_proposal_item(candidate, num, [])
            batch_items.append(item)
            state.add_fingerprint(fp)
        if batch_items:
            import datetime as _dt
            batch_id = _dt.datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M:%S_img")
            batch = {
                "batch_id": batch_id,
                "slack_ts": None,
                "created_at": _dt.datetime.now(timezone.utc).isoformat(),
                "items": batch_items,
            }
            state.add_proposal_batch(batch)
            day_thread_ts = slack_notifier.get_or_create_day_thread(state)
            if day_thread_ts:
                posted_ts = slack_notifier.post_proposals(day_thread_ts, batch_items)
                if posted_ts:
                    state.set_proposal_slack_ts(batch_id, posted_ts)
            events_proposed = len(batch_items)
            result["calendar_proposed"] += events_proposed
    else:
        for candidate in analysis.calendar_items:
            if candidate.start_dt < now:
                logger.info("Skipping past calendar event from document: %r on %s", candidate.title, candidate.start_dt.date())
                continue
            fp = fingerprint(candidate)
            if state.has_fingerprint(fp):
                logger.debug("Skipping duplicate calendar event from document: %r", candidate.title)
                continue
            written, conflicts = gcal_writer.write_event(candidate, dry_run=dry_run, snapshot=snapshot)
            if written:
                events_created += 1
                state.add_fingerprint(fp)
                state.add_written_event(
                    gcal_id=written.gcal_event_id,
                    title=candidate.title,
                    start_iso=candidate.start_dt.isoformat(),
                    fingerprint=written.fingerprint,
                )
        result["calendar_created"] += events_created

    # ── Reply in Slack thread ───────────────────────────────────────────────
    if not dry_run and not mock and msg_ts:
        slack_notifier.post_file_result(
            thread_ts=msg_ts,
            analysis=analysis,
            nas_path=nas_path,
            events_created=events_created,
            events_proposed=events_proposed,
            auto_processed=auto_processed,
            page_count=n,
        )

    # ── Mark all file IDs as processed ─────────────────────────────────────
    for file_info in files:
        state.mark_file_processed(file_info["id"], {
            "filename": file_info.get("name"),
            "document_id": staging_id,
            "category": f"{analysis.primary_category}/{analysis.subcategory or ''}",
            "nas_path": nas_path or staging_path,
            "calendar_events": events_created,
        })

    result["processed"] += 1
    logger.info(
        "%sProcessed %d-page document → %s (%d calendar events created, %d proposed)",
        "DRY RUN " if dry_run else "",
        n,
        nas_path or f"staged at {staging_path}",
        events_created,
        events_proposed,
    )


def _process_single_file(
    file_info: dict,
    accompanying_text: str,
    msg_ts: str,
    state: state_module.State,
    snapshot: dict,
    connector: SlackConnector,
    dry_run: bool,
    mock: bool,
    result: dict,
) -> None:
    """Process a single file upload: download → analyze → stage → NAS → calendar → Slack reply."""
    file_id = file_info["id"]
    filename = file_info.get("name", "unknown")
    mimetype = file_info.get("mimetype", "")

    logger.info("Processing file %s: %s (%s)", file_id, filename, mimetype)

    # ── Download ────────────────────────────────────────────────────────────
    if mock:
        # Synthetic bytes for mock mode
        file_bytes = b"MOCK_FILE_CONTENT_FOR_TESTING"
    else:
        url = file_info.get("url_private_download", "")
        if not url:
            logger.warning("No download URL for file %s", file_id)
            return
        file_bytes = connector.download_file(url)
        logger.debug("Downloaded %s (%d bytes)", filename, len(file_bytes))

    # ── Analyze via Gemini ──────────────────────────────────────────────────
    analysis = image_analyzer.analyze_file(
        file_bytes=file_bytes,
        filename=filename,
        mimetype=mimetype,
        accompanying_text=accompanying_text,
        mock=mock,
    )
    if analysis is None:
        logger.warning("Analysis returned None for %s — skipping", filename)
        result["errors"] += 1
        return

    # Fill in fields the analyzer doesn't know
    analysis.file_id = file_id
    analysis.source_slack_ts = msg_ts

    # ── Handle low confidence ───────────────────────────────────────────────
    if analysis.confidence < config.IMAGE_CONFIDENCE_MIN:
        logger.info(
            "Low confidence (%.2f) for %s — routing to Documents/Unsorted",
            analysis.confidence, filename,
        )
        analysis.primary_category = "Documents"
        analysis.subcategory = None

    # ── Determine file extension ────────────────────────────────────────────
    ext = Path(filename).suffix or _ext_from_mimetype(mimetype)

    # ── Stage locally ───────────────────────────────────────────────────────
    staging_path = file_writer.stage_locally(analysis, file_bytes, ext)

    # ── Copy to NAS ─────────────────────────────────────────────────────────
    nas_path = file_writer.copy_to_nas(staging_path, analysis, dry_run=dry_run)
    if nas_path:
        if not dry_run:
            file_writer.purge_staging(staging_path)
        result["nas_written"] += 1
    else:
        result["staged_pending"] += 1

    # ── Create or propose calendar events ──────────────────────────────────
    events_created = 0
    events_proposed = 0
    now = datetime.now(timezone.utc)
    if config.EVENT_APPROVAL_MODE == "propose" and not dry_run and not mock:
        batch_items = []
        for candidate in analysis.calendar_items:
            if candidate.start_dt < now:
                logger.info("Skipping past calendar event from file: %r on %s", candidate.title, candidate.start_dt.date())
                continue
            fp = fingerprint(candidate)
            if state.has_fingerprint(fp):
                logger.debug("Skipping duplicate calendar event from file: %r", candidate.title)
                continue
            num = state.next_proposal_num()
            item = _candidate_to_proposal_item(candidate, num, [])
            batch_items.append(item)
            state.add_fingerprint(fp)
            logger.info("Proposing calendar event from file: %r on %s", candidate.title, candidate.start_dt.date())
        if batch_items:
            import datetime as _dt
            batch_id = _dt.datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M:%S_img")
            batch = {
                "batch_id": batch_id,
                "slack_ts": None,
                "created_at": _dt.datetime.now(timezone.utc).isoformat(),
                "items": batch_items,
            }
            state.add_proposal_batch(batch)
            day_thread_ts = slack_notifier.get_or_create_day_thread(state)
            if day_thread_ts:
                posted_ts = slack_notifier.post_proposals(day_thread_ts, batch_items)
                if posted_ts:
                    state.set_proposal_slack_ts(batch_id, posted_ts)
            events_proposed = len(batch_items)
            result["calendar_proposed"] += events_proposed
    else:
        for candidate in analysis.calendar_items:
            if candidate.start_dt < now:
                logger.info("Skipping past calendar event from file: %r on %s", candidate.title, candidate.start_dt.date())
                continue
            fp = fingerprint(candidate)
            if state.has_fingerprint(fp):
                logger.debug("Skipping duplicate calendar event from file: %r", candidate.title)
                continue
            written, conflicts = gcal_writer.write_event(
                candidate, dry_run=dry_run, snapshot=snapshot,
            )
            if written:
                events_created += 1
                state.add_fingerprint(fp)
                state.add_written_event(
                    gcal_id=written.gcal_event_id,
                    title=candidate.title,
                    start_iso=candidate.start_dt.isoformat(),
                    fingerprint=written.fingerprint,
                )
                logger.info(
                    "%sCalendar event from file: %r on %s",
                    "DRY RUN " if dry_run else "",
                    candidate.title, candidate.start_dt.date(),
                )
        result["calendar_created"] += events_created

    # ── Reply in Slack thread ───────────────────────────────────────────────
    if not dry_run and not mock and msg_ts:
        slack_notifier.post_file_result(
            thread_ts=msg_ts,
            analysis=analysis,
            nas_path=nas_path,
            events_created=events_created,
            events_proposed=events_proposed,
        )

    # ── Mark processed ──────────────────────────────────────────────────────
    state.mark_file_processed(file_id, {
        "filename": filename,
        "category": f"{analysis.primary_category}/{analysis.subcategory or ''}",
        "nas_path": nas_path or staging_path,
        "calendar_events": events_created,
    })

    result["processed"] += 1
    logger.info(
        "%sProcessed %s → %s (%d calendar events created, %d proposed)",
        "DRY RUN " if dry_run else "",
        filename,
        nas_path or f"staged at {staging_path}",
        events_created,
        events_proposed,
    )


def _ext_from_mimetype(mimetype: str) -> str:
    """Derive a file extension from MIME type."""
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "image/tiff": ".tiff",
        "application/pdf": ".pdf",
    }
    return mapping.get(mimetype, ".bin")
