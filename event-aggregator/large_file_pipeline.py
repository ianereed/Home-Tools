"""Large-file ingest pipeline — escalation path for nas-intake.

Invoked when nas-intake's small-file path (`main.py ingest-image`) has timed
out N consecutive times on the same source. This module reuses every primitive
from the small-file path but adds:

  - **Per-page persistence**: each rendered page PNG and each per-page vision
    JSON result are written to `<staging>/pages/page_NNN.{png,json}`. On a
    retry, pages with an existing `.json` result are skipped — this is what
    makes large multi-page docs survive across watcher invocations.

  - **Windowed calendar consolidation**: the small-file path silently truncates
    merged OCR text to 4000 chars before calling the qwen3 text model
    (image_analyzer.py:189). Here we slide a window over the full text and
    call calendar detection per window, then dedup events by fingerprint.

  - **Heartbeat**: every progress event (phase boundary, page start, page
    complete, window start) writes `<staging>/heartbeat.json`. The parent
    watchdog (nas-intake/processor.py) reads this to distinguish "still
    working on a hard page" from "wedged on a hung HTTP call".

  - **Per-file diagnostic log**: `<LARGE_FILE_LOG_DIR>/<sha12>.log`. Per-page
    timings and Ollama state are captured for forensics on wedged docs.

There is NO subprocess timeout from this module's perspective. The parent
nas-intake watchdog is responsible for kill-on-hangup.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config
from analyzers import image_analyzer
from models import FileAnalysisResult
from writers import file_writer

logger = logging.getLogger(__name__)


# ── tunables ───────────────────────────────────────────────────────────

# Approx safe ceiling for a single qwen3:14b consolidation call. Defaults to
# 12K chars to leave headroom for the prompt overhead inside a 16K-token ctx
# (rule of thumb: 1 token ≈ 4 chars; 16K tokens ≈ 64K chars; we use ~25% of
# that to leave room for the prompt + safety margin since text-with-numbers
# tokenizes worse than 1 token per 4 chars).
DEFAULT_WINDOW_CHARS = 12000
DEFAULT_WINDOW_OVERLAP = 1500


# ── heartbeat & diagnostic logging ─────────────────────────────────────

@dataclass
class Heartbeat:
    path: Path
    sha12: str
    phase: str = "init"
    page_done: int = 0
    page_total: int = 0
    started_at: str = ""

    def beat(self, *, phase: str | None = None, page_done: int | None = None,
             page_total: int | None = None, current_op: str = "") -> None:
        if phase is not None:
            self.phase = phase
        if page_done is not None:
            self.page_done = page_done
        if page_total is not None:
            self.page_total = page_total
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sha12": self.sha12,
            "phase": self.phase,
            "page_done": self.page_done,
            "page_total": self.page_total,
            "current_op": current_op,
            "started_at": self.started_at,
        }
        try:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            # Don't crash the pipeline because the heartbeat dir went away —
            # the watchdog will kill us if it really matters.
            logger.warning("heartbeat write failed: %s", exc)


def _open_diag_log(sha12: str) -> logging.Logger:
    """Return a per-file logger writing to <LARGE_FILE_LOG_DIR>/<sha12>.log.
    Falls back silently to a stderr-only logger if the dir can't be made.
    """
    log_dir = Path(getattr(config, "LARGE_FILE_LOG_DIR",
                           Path.home() / "Library" / "Logs" / "home-tools-nas-intake-large"))
    diag = logging.getLogger(f"large_file.{sha12}")
    diag.setLevel(logging.DEBUG)
    # Idempotent — repeat invocations on the same sha share a logger.
    if diag.handlers:
        return diag
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / f"{sha12}.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        diag.addHandler(fh)
    except OSError as exc:
        logger.warning("could not open diag log dir %s: %s", log_dir, exc)
    diag.propagate = False
    return diag


# ── phase A — rasterize with persistence ───────────────────────────────

def _rasterize_with_cache(
    file_bytes: bytes, filename: str, mimetype: str, pages_dir: Path,
    hb: Heartbeat, dpi: int = 200,
) -> list[Path]:
    """Render each page to <pages_dir>/page_NNN.png; skip pages already on disk.
    Returns the ordered list of PNG paths.

    Updates `hb` after each page so the parent watchdog can distinguish
    "rasterizing a 500-page PDF" from "process is wedged".
    """
    pages_dir.mkdir(parents=True, exist_ok=True)

    if mimetype != "application/pdf":
        ext = Path(filename).suffix or ".png"
        target = pages_dir / f"page_001{ext}"
        if not target.exists():
            target.write_bytes(file_bytes)
        hb.beat(phase="rasterize", page_done=1, page_total=1, current_op="image cached")
        return [target]

    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(file_bytes)
    total = len(pdf)
    hb.beat(phase="rasterize", page_done=0, page_total=total)
    scale = dpi / 72.0
    paths: list[Path] = []
    for i in range(total):
        target = pages_dir / f"page_{i+1:03d}.png"
        if not target.exists():
            page = pdf[i]
            pil = page.render(scale=scale).to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            target.write_bytes(buf.getvalue())
        paths.append(target)
        hb.beat(page_done=i + 1, current_op=f"rasterize {i+1}/{total}")
    return paths


# ── phase B — per-page vision with persistence ─────────────────────────

def _analyze_pages_with_cache(
    page_paths: list[Path], stem: str, hb: Heartbeat, diag: logging.Logger,
) -> tuple[list[dict], list[str]]:
    """Run vision per page, caching JSON results next to the PNGs.
    Returns (page_dicts, filenames) in the same shape _merge_page_results expects.

    A page whose vision call fails permanently (after the analyzer's own 3
    retries) is skipped with a debug log — same posture as
    image_analyzer._analyze_local.
    """
    page_dicts: list[dict] = []
    filenames: list[str] = []
    total = len(page_paths)
    for i, png_path in enumerate(page_paths, 1):
        json_path = png_path.with_suffix(".json")
        page_filename = f"{stem}-p{i}.png"
        hb.beat(phase="vision_per_page", page_done=i - 1, page_total=total,
                current_op=f"page {i}/{total} start")

        if json_path.exists():
            try:
                cached = json.loads(json_path.read_text(encoding="utf-8"))
                page_dicts.append(cached)
                filenames.append(page_filename)
                diag.info("page %d/%d: cache hit", i, total)
                hb.beat(page_done=i, current_op=f"page {i}/{total} cached")
                continue
            except (json.JSONDecodeError, OSError) as exc:
                diag.warning("page %d/%d: cache unreadable, re-running: %s", i, total, exc)
                json_path.unlink(missing_ok=True)

        t0 = time.monotonic()
        diag.info("page %d/%d: vision call start", i, total)
        try:
            file_bytes = png_path.read_bytes()
        except OSError as exc:
            diag.warning("page %d/%d: cannot read PNG %s: %s", i, total, png_path, exc)
            continue
        result = image_analyzer._analyze_page_local(
            file_bytes, page_filename, "image/png",
        )
        elapsed = time.monotonic() - t0
        if result is None:
            diag.warning("page %d/%d: vision returned None after %.1fs", i, total, elapsed)
            hb.beat(page_done=i, current_op=f"page {i}/{total} failed (skipped)")
            continue
        try:
            json_path.write_text(json.dumps(result), encoding="utf-8")
        except OSError as exc:
            diag.warning("page %d/%d: failed to persist json: %s", i, total, exc)
        page_dicts.append(result)
        filenames.append(page_filename)
        diag.info("page %d/%d: vision ok in %.1fs", i, total, elapsed)
        hb.beat(page_done=i, current_op=f"page {i}/{total} ok ({elapsed:.0f}s)")

    return page_dicts, filenames


# ── phase D — windowed calendar consolidation ──────────────────────────

def _split_text_into_windows(
    text: str, window_chars: int = DEFAULT_WINDOW_CHARS,
    overlap: int = DEFAULT_WINDOW_OVERLAP,
) -> list[str]:
    """Slide a window over `text`. Each window is up to `window_chars` long
    with `overlap` chars of trailing context shared with the next window.

    Returns at least one window (possibly the whole text if it's short).
    Windows are split at paragraph boundaries when possible, falling back to
    raw character split. Overlap helps the LLM recover events whose context
    straddles a boundary.
    """
    if len(text) <= window_chars:
        return [text]
    if overlap >= window_chars:
        overlap = window_chars // 4

    step = window_chars - overlap
    windows: list[str] = []
    pos = 0
    while pos < len(text):
        end = min(pos + window_chars, len(text))
        windows.append(text[pos:end])
        if end == len(text):
            break
        pos += step
    return windows


def _consolidate_calendar_items(
    structured_text: str, hb: Heartbeat, diag: logging.Logger,
) -> list:
    """Run calendar detection over the (possibly long) merged text. For text
    that fits in one window, this is identical to the small-file path. For
    longer text, it slides a window and dedup's events by fingerprint.
    """
    if not structured_text or not structured_text.strip():
        return []

    windows = _split_text_into_windows(structured_text)
    diag.info("consolidation: %d window(s) over %d chars",
              len(windows), len(structured_text))

    # Single window? Reuse the small-file helper directly so behavior is
    # bit-identical for short docs.
    if len(windows) == 1:
        hb.beat(phase="consolidate", current_op="single window")
        return image_analyzer._detect_calendar_items_local(structured_text)

    # Lazy import to keep this module's load light if the small-file path is
    # all that runs.
    from dedup import fingerprint as _fingerprint

    seen_fps: set = set()
    all_items: list = []
    for i, win in enumerate(windows, 1):
        hb.beat(phase="consolidate", current_op=f"window {i}/{len(windows)}")
        diag.info("consolidation: window %d/%d (%d chars)", i, len(windows), len(win))
        items = image_analyzer._detect_calendar_items_local(win)
        for it in items:
            fp = _fingerprint(it)
            if fp in seen_fps:
                continue
            seen_fps.add(fp)
            all_items.append(it)
    diag.info("consolidation: %d unique calendar item(s)", len(all_items))
    return all_items


# ── phase E — staging via file_writer (reload pages from disk) ─────────

def _staging_dir(staging_id: str) -> Path:
    return Path(config.LOCAL_STAGING_DIR).expanduser() / staging_id


def _load_pages_for_staging(page_paths: list[Path], stem: str) -> list[tuple[bytes, str, str]]:
    """Read PNG bytes back from disk in the (bytes, filename, mimetype)
    shape that file_writer.stage_document_locally expects."""
    out: list[tuple[bytes, str, str]] = []
    for i, p in enumerate(page_paths, 1):
        try:
            out.append((p.read_bytes(), f"{stem}-p{i}.png", "image/png"))
        except OSError as exc:
            logger.warning("could not re-read %s for staging: %s", p, exc)
    return out


# ── public entry point ────────────────────────────────────────────────

def process_large_file(file_path: Path) -> int:
    """Run the large-file pipeline on `file_path`.

    Returns 0 on success (staging dir + _metadata.json written and ready for
    nas-intake to pick up), non-zero on failure.

    Side effects:
      - Writes `<staging>/pages/page_NNN.{png,json}` (resumable).
      - Writes `<staging>/heartbeat.json` (parent watchdog reads this).
      - Writes `<staging>/_metadata.json` + page files at the staging root
        (so processor.py:_find_staged_dir + _copy_staged_to_dest work
        unchanged).
      - Writes `<LARGE_FILE_LOG_DIR>/<sha12>.log` (forensic log).

    Does NOT clean up `pages/` or `heartbeat.json` — that's the caller's job
    (nas-intake/processor.py decides between cleanup-on-success and
    keep-for-diagnosis-on-wedge).
    """
    if not file_path.exists():
        print(f"file not found: {file_path}", flush=True)
        return 2

    file_bytes = file_path.read_bytes()
    sha12 = hashlib.sha256(file_bytes).hexdigest()[:12]
    staging_id = f"local_{sha12}"
    staging_dir = _staging_dir(staging_id)
    staging_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = staging_dir / "pages"
    hb = Heartbeat(
        path=staging_dir / "heartbeat.json",
        sha12=sha12,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    diag = _open_diag_log(sha12)

    diag.info(
        "=== large-file pipeline start: %s (%.1f MB)",
        file_path.name, len(file_bytes) / (1024 * 1024),
    )

    # Phase A — rasterize
    hb.beat(phase="rasterize", current_op="rendering pages")
    mimetype = _mimetype_for_ext(file_path.suffix.lower())
    try:
        t0 = time.monotonic()
        page_paths = _rasterize_with_cache(
            file_bytes, file_path.name, mimetype, pages_dir, hb,
        )
        diag.info("rasterize: %d page(s) in %.1fs", len(page_paths), time.monotonic() - t0)
    except RuntimeError as exc:
        diag.error("rasterize failed: %s", exc)
        print(f"rasterize failed: {exc}", flush=True)
        return 2

    if not page_paths:
        diag.error("rasterize returned 0 pages")
        print("rasterize returned 0 pages", flush=True)
        return 2

    # Phase B — per-page vision
    hb.beat(phase="vision_per_page", page_done=0, page_total=len(page_paths))
    stem = file_path.stem
    page_dicts, filenames = _analyze_pages_with_cache(page_paths, stem, hb, diag)
    if not page_dicts:
        diag.error("no pages analyzed successfully out of %d", len(page_paths))
        print(f"no pages analyzed (0/{len(page_paths)})", flush=True)
        return 2
    diag.info("vision: %d/%d pages succeeded", len(page_dicts), len(page_paths))

    # Phase C — stitch
    hb.beat(phase="stitch", current_op="merging page results")
    merged = image_analyzer._merge_page_results(page_dicts, filenames)
    if merged is None:
        diag.error("merge returned None")
        print("merge returned None", flush=True)
        return 2

    # Phase D — windowed calendar consolidation
    calendar_items = _consolidate_calendar_items(merged.structured_text, hb, diag)
    merged.calendar_items = calendar_items

    # Apply the same low-confidence demotion as ingest_local_file does
    # (image_pipeline.py:88-90): keep filing in Documents/ if vision wasn't
    # confident.
    if merged.confidence < config.IMAGE_CONFIDENCE_MIN:
        merged.primary_category = "Documents"
        merged.subcategory = None

    merged.file_id = staging_id
    merged.original_filename = file_path.name

    # Phase E — final staging (reuses file_writer; identical output shape to
    # the small-file path, so nas-intake's _find_staged_dir + _copy_staged_to_dest
    # work without changes).
    hb.beat(phase="stage", current_op="writing _metadata.json + extraction")
    pages_for_staging = _load_pages_for_staging(page_paths, stem)
    if not pages_for_staging:
        diag.error("could not reload any pages from disk for staging")
        print("staging failed: no pages re-readable", flush=True)
        return 2
    staging_path = file_writer.stage_document_locally(merged, pages_for_staging, staging_id)

    # Phase F — propose extracted calendar events (mirrors the small-file
    # path at image_pipeline.py:106-138, factored into _post_proposals).
    events_proposed = 0
    try:
        events_proposed = _post_proposals(merged, diag)
    except Exception as exc:
        # A proposal failure must not fail the filing step. The file is
        # already staged; nas-intake's caller will still file it.
        diag.warning("proposal flow raised: %s: %s", type(exc).__name__, exc)

    hb.beat(phase="done", page_done=len(page_paths), current_op="ready for filing")
    diag.info("=== large-file pipeline done: staged at %s | %d event(s) proposed",
              staging_path, events_proposed)

    print(
        f":white_check_mark: {file_path.name} → "
        f"{merged.primary_category}"
        + (f"/{merged.subcategory}" if merged.subcategory else "")
        + f" (staged: {staging_path})"
        + (f" | {events_proposed} event(s) proposed" if events_proposed else ""),
        flush=True,
    )
    return 0


def _post_proposals(merged: FileAnalysisResult, diag: logging.Logger) -> int:
    """Post any extracted calendar events to the Slack proposal dashboard.
    Returns the count of events newly proposed (i.e. not already in state via
    fingerprint dedup). Returns 0 if EVENT_APPROVAL_MODE != 'propose'.

    Mirrors image_pipeline.ingest_local_file lines 106-138 — kept inline here
    so the small-file path stays bit-identical and we don't introduce a
    cross-file refactor in this PR.
    """
    if config.EVENT_APPROVAL_MODE != "propose":
        return 0
    if not merged.calendar_items:
        return 0

    import state as state_module
    from dedup import fingerprint
    from notifiers import slack_notifier

    state = state_module.load()
    now = datetime.now(timezone.utc)
    batch_items: list = []
    for candidate in merged.calendar_items:
        if candidate.start_dt < now:
            continue
        fp = fingerprint(candidate)
        if state.has_fingerprint(fp):
            continue
        num = state.next_proposal_num()
        batch_items.append(_candidate_to_proposal_item(candidate, num, []))
        state.add_fingerprint(fp)

    if not batch_items:
        with state_module.locked():
            state_module.save(state)
        return 0

    batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M:%S_nas")
    batch = {
        "batch_id": batch_id,
        "slack_ts": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": batch_items,
    }
    state.add_proposal_batch(batch)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_items = state.get_all_proposal_items_for_dashboard(today_str)
    posted_ts = slack_notifier.post_or_update_dashboard(all_items, state)
    if posted_ts:
        state.set_proposal_slack_ts(batch_id, posted_ts)
    with state_module.locked():
        state_module.save(state)
    diag.info("proposals: posted %d new calendar item(s) (batch %s)",
              len(batch_items), batch_id)
    return len(batch_items)


def _candidate_to_proposal_item(candidate, num: int, conflicts: list[str]) -> dict:
    """Verbatim from image_pipeline.py:31-55 — kept local so this module
    doesn't introduce a cross-import that could change ingest_local_file's
    behavior."""
    from dedup import fingerprint
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


# ── helpers ───────────────────────────────────────────────────────────

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
