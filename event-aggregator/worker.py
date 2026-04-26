"""
Long-running worker that drains the text + OCR queues sequentially.

Why a separate worker?
- Per-job context is now generous (16k tokens) — extraction can take 30–60s
  on the mini, so an inline run blocks the fetch loop.
- The two LLMs (qwen3:14b + qwen2.5vl:7b) cannot both be hot at once on
  a 24 GB machine; the worker owns model-swap orchestration so only one
  is loaded at a time.
- Persistent queues in state.json mean a process restart doesn't lose
  pending jobs.

Job lifecycle:
  fetch-only:       polls connectors → enqueue_text_job() → advance last_run
  dispatcher / cli: enqueue_ocr_job() when an image arrives in #ian-image-intake
  worker (this):    pops jobs FIFO; runs extraction or OCR; persists results

Model-swap protocol (per loop iteration):
  ocr_queue empty + text non-empty   → run text job
  ocr non-empty   + text empty       → unload text model, run OCR, reload text
  ocr non-empty   + text non-empty   → finish current text jobs first
                                       (default = wait; matches user policy
                                        "default to waiting if no response")
                                       — a Slack swap-decision proposal can
                                       flip this to "interrupt" via the
                                       state.swap_decisions registry.
"""
from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import datetime, timezone

import requests

import config
import state as state_module

logger = logging.getLogger(__name__)

_IDLE_SLEEP_SECONDS = 30  # how long to sleep when both queues are empty
_TICK_SLEEP_SECONDS = 1   # short pause between successive jobs (avoid tight loop)
_SWAP_DECISION_TIMEOUT_MIN = 5  # auto-resolve a pending swap decision to "wait" after this


# ── Signal handling — graceful shutdown ───────────────────────────────────────

_shutdown_requested = False


def _request_shutdown(signum, frame):
    global _shutdown_requested
    if not _shutdown_requested:
        logger.info("worker: received signal %s — shutting down after current job", signum)
    _shutdown_requested = True


# ── Ollama lifecycle (load / unload by setting keep_alive) ────────────────────

def _ollama_unload(model: str) -> None:
    """Best-effort unload — set keep_alive=0 so Ollama frees the model now."""
    try:
        requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "keep_alive": 0, "prompt": "", "stream": False},
            timeout=10,
        )
        logger.info("worker: unloaded model %s", model)
    except Exception as exc:
        logger.debug("worker: unload of %s failed (best effort): %s", model, exc)


def _ollama_warmup(model: str, num_ctx: int, keep_alive: str = "-1") -> None:
    """Best-effort warmup — issue an empty generate so the model is resident."""
    try:
        requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": keep_alive,
                "options": {"num_ctx": num_ctx},
            },
            timeout=120,
        )
        logger.info("worker: warmed model %s (ctx=%d)", model, num_ctx)
    except Exception as exc:
        logger.debug("worker: warmup of %s failed (best effort): %s", model, exc)


# ── Job runners ───────────────────────────────────────────────────────────────

def _run_text_job(state, job: dict) -> None:
    """Run extraction for one queued message and persist results."""
    import extractor
    from analyzers import calendar_analyzer
    from connectors import google_auth
    from googleapiclient.discovery import build
    from models import RawMessage, CandidateEvent

    source = job["source"]
    msg_id = job["id"]

    # Skip if extraction already happened (re-running over a partially-drained queue)
    if state.is_seen(source, msg_id):
        logger.debug("worker: %s/%s already seen — skipping", source, msg_id)
        return

    try:
        ts = datetime.fromisoformat(job["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, KeyError):
        ts = datetime.now(timezone.utc)

    msg = RawMessage(
        id=msg_id,
        source=source,
        timestamp=ts,
        body_text=job.get("body_text", ""),
        metadata=job.get("metadata") or {},
    )

    # Pre-classifier: skip the 16k-ctx call on obvious non-event noise.
    # "maybe" falls through; "no" short-circuits.
    verdict, reason = extractor.pre_classify(msg)
    logger.info(
        "worker: pre-classify %s/%s → %s (%s)",
        source, msg_id, verdict, reason,
    )
    if verdict == "no":
        state.mark_seen(source, msg_id)
        return

    # Refresh calendar context per-job — the upcoming-events list may have
    # changed between when we enqueued and when we extract.
    calendar_context = ""
    try:
        creds = google_auth.get_credentials(
            scopes=["https://www.googleapis.com/auth/calendar.events"],
            token_path=config.GCAL_TOKEN_JSON,
            credentials_path=config.GMAIL_CREDENTIALS_JSON,
            keyring_key="gcal_token",
        )
        svc = build("calendar", "v3", credentials=creds)
        upcoming = calendar_analyzer.fetch_upcoming(svc, weeks=config.CALENDAR_CONTEXT_WEEKS)
        # Lightweight inline format — same shape as main._format_calendar_context
        lines = []
        for e in upcoming:
            if getattr(e, "is_all_day", False):
                continue
            start = e.start_dt.strftime("%b %-d %-I:%M%p").lower()
            lines.append(f"- {start}: {e.title}")
        calendar_context = "\n".join(lines)
    except Exception as exc:
        logger.debug("worker: calendar context fetch failed: %s", exc)

    events, todos = extractor.extract(msg, calendar_context=calendar_context)
    logger.info(
        "worker: extracted %s/%s → %d event(s), %d todo(s)",
        source, msg_id, len(events), len(todos),
    )

    # Hand off to the existing main.py paths via direct function calls.
    # We avoid duplicating the proposal/auto branching here.
    import main as main_module
    # _propose_events / _auto_create_events expect lists; pass single message's worth.
    snapshot = state.calendar_snapshot()
    if config.EVENT_APPROVAL_MODE == "propose":
        main_module._propose_events(events, state, snapshot, dry_run=False, mock=False)
        # Refresh dashboard after each job so the user sees updates immediately.
        if events:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            from notifiers import slack_notifier
            all_items = state.get_all_proposal_items_for_dashboard(today_str)
            slack_notifier.post_or_update_dashboard(all_items, state)
    else:
        # Auto mode — needs a get_thread callback for Slack threading.
        from notifiers import slack_notifier as _sn
        thread_ts = None
        def _get_thread() -> str | None:
            nonlocal thread_ts
            if thread_ts is None:
                thread_ts = _sn.get_or_create_day_thread(state)
            return thread_ts
        main_module._auto_create_events(events, state, snapshot, dry_run=False, mock=False, get_thread=_get_thread)

    # Todos
    if todos:
        from dedup import todo_fingerprint
        from writers import todoist_writer
        for todo in todos:
            if todo.confidence < config.TODOIST_TODO_MIN_CONFIDENCE:
                continue
            fp = todo_fingerprint(todo)
            if state.has_todo_fingerprint(fp):
                continue
            ok = todoist_writer.create_task(
                title=todo.title,
                context=todo.context,
                due_date=todo.due_date,
                priority=todo.priority,
                project_id=state.get_todoist_project_id(),
                set_project_id=lambda pid: state.set_todoist_project_id(pid),
            )
            if ok:
                state.add_todo_fingerprint(fp)

    state.mark_seen(source, msg_id)


def _run_ocr_job(state, job: dict) -> None:
    """Run OCR / image analysis on a queued file path."""
    from pathlib import Path
    import cli  # reuse the existing single-file pipeline
    file_path = Path(job["file_path"])
    if not file_path.exists():
        logger.warning("worker: OCR file not found, dropping: %s", file_path)
        return
    logger.info("worker: OCR job → %s", file_path)
    try:
        cli._cmd_ingest_image(file_path)
    except Exception as exc:
        logger.warning("worker: OCR job failed for %s: %s", file_path, exc)


# ── Swap decision helpers ─────────────────────────────────────────────────────

def _expire_stale_swap_decisions(state) -> None:
    """Auto-resolve any swap decision older than the timeout to 'wait'."""
    from datetime import timedelta
    bucket = state._data.get("swap_decisions", {})
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_SWAP_DECISION_TIMEOUT_MIN)
    for decision_id, info in list(bucket.items()):
        if info.get("decision") != "pending":
            continue
        try:
            created = datetime.fromisoformat(info.get("created_at", ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if created < cutoff:
            info["decision"] = "wait"
            info["resolved_at"] = datetime.now(timezone.utc).isoformat()
            info["auto_resolved"] = True
            logger.info("worker: swap decision %s auto-resolved to 'wait' (timeout)", decision_id)


def _has_pending_interrupt(state) -> bool:
    """Return True if any swap decision is set to 'interrupt'."""
    bucket = state._data.get("swap_decisions", {})
    return any(info.get("decision") == "interrupt" for info in bucket.values())


def _consume_interrupt(state) -> None:
    """Mark all pending interrupts as consumed so they don't re-fire."""
    bucket = state._data.get("swap_decisions", {})
    for info in bucket.values():
        if info.get("decision") == "interrupt":
            info["decision"] = "consumed"


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_worker() -> int:
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    logger.info(
        "worker: starting — text model=%s ctx=%d, vision=%s ctx=%d",
        config.OLLAMA_MODEL, config.OLLAMA_NUM_CTX_TEXT,
        config.LOCAL_VISION_MODEL, config.OLLAMA_NUM_CTX_VISION,
    )

    # Warm the primary text model so the first job doesn't pay the load latency.
    _ollama_warmup(
        config.OLLAMA_MODEL,
        config.OLLAMA_NUM_CTX_TEXT,
        keep_alive=config.OLLAMA_KEEP_ALIVE_TEXT,
    )

    while not _shutdown_requested:
        state = state_module.load()
        _expire_stale_swap_decisions(state)
        state_module.save(state)

        text_depth = state.text_queue_depth()
        ocr_depth = state.ocr_queue_depth()

        state.update_worker_status(
            text_queue=text_depth,
            ocr_queue=ocr_depth,
            current_model=config.OLLAMA_MODEL,
        )
        state_module.save(state)

        # Decide what to run.
        run_ocr = False
        run_text = False
        if ocr_depth > 0 and text_depth == 0:
            run_ocr = True
        elif ocr_depth > 0 and _has_pending_interrupt(state):
            run_ocr = True
            _consume_interrupt(state)
            state_module.save(state)
        elif text_depth > 0:
            run_text = True
        elif ocr_depth > 0:
            # Shouldn't reach here (covered above) but be safe.
            run_ocr = True
        else:
            # Both queues empty.
            time.sleep(_IDLE_SLEEP_SECONDS)
            continue

        try:
            if run_ocr:
                # Swap: unload text model, run vision, reload text.
                _ollama_unload(config.OLLAMA_MODEL)
                state.update_worker_status(current_model=config.LOCAL_VISION_MODEL)
                state_module.save(state)
                job = state.pop_ocr_job()
                state_module.save(state)
                if job:
                    _run_ocr_job(state, job)
                # Reload primary so next text job is hot.
                _ollama_unload(config.LOCAL_VISION_MODEL)
                _ollama_warmup(
                    config.OLLAMA_MODEL,
                    config.OLLAMA_NUM_CTX_TEXT,
                    keep_alive=config.OLLAMA_KEEP_ALIVE_TEXT,
                )
                state.update_worker_status(current_model=config.OLLAMA_MODEL)
                state_module.save(state)
            elif run_text:
                job = state.pop_text_job()
                state_module.save(state)
                if job:
                    _run_text_job(state, job)
        except Exception as exc:
            logger.exception("worker: job failed: %s", exc)

        state_module.save(state)
        time.sleep(_TICK_SLEEP_SECONDS)

    logger.info("worker: clean shutdown")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    sys.exit(run_worker())
