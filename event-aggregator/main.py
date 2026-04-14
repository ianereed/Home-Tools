"""
Local Event Aggregator — main entry point.

Usage:
  python main.py                          # full run, all sources
  python main.py --mock                   # use synthetic data only (safe for demos)
  python main.py --dry-run                # extract + dedup but don't write to calendar
  python main.py --source gmail,slack     # run specific sources only
  python main.py --digest-only            # skip extraction; just send digest
  python main.py --mock --dry-run         # Phase 1 test (no external calls except Ollama)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from googleapiclient.discovery import build
from thefuzz import fuzz

import config
import extractor
import state as state_module
from analyzers import calendar_analyzer
from analyzers.calendar_analyzer import CalendarEvent
from connectors import google_auth
from connectors.discord_conn import DiscordConnector
from connectors.gmail import GmailConnector
from connectors.google_calendar import GoogleCalendarConnector
from connectors.imessage import IMessageConnector
from connectors.notifications import NotificationCenterConnector
from connectors.slack import SlackConnector
from connectors.whatsapp import WhatsAppConnector
from dedup import fingerprint, is_duplicate, todo_fingerprint
from logs.event_log import record as log_event, record_cancellation
from models import CandidateEvent, CandidateTodo
from notifiers import digest as digest_module
from notifiers import slack_notifier
from writers import google_calendar as gcal_writer
from writers import todoist_writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Registry: source name → connector class
_CONNECTOR_REGISTRY = {
    "gmail": GmailConnector,
    "gcal": GoogleCalendarConnector,
    "slack": SlackConnector,
    "imessage": IMessageConnector,
    "whatsapp": WhatsAppConnector,
    "discord": DiscordConnector,
    "messenger": NotificationCenterConnector,
    "instagram": NotificationCenterConnector,
}

_ALL_SOURCES = [
    "gmail", "gcal", "slack", "imessage", "whatsapp", "discord", "messenger", "instagram"
]

# Minimum confidence for acting on update/cancel signals (higher bar than creation)
_UPDATE_CANCEL_MIN_CONFIDENCE = 0.75
_UPDATE_FUZZY_THRESHOLD = 75


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Event Aggregator")
    parser.add_argument("--mock", action="store_true", help="Use synthetic test data only")
    parser.add_argument("--dry-run", action="store_true", help="Extract but do not write to GCal")
    parser.add_argument("--source", default="", help="Comma-separated sources to run (default: all)")
    parser.add_argument("--digest-only", action="store_true", help="Send digests, skip extraction")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return parser.parse_args()


def _resolve_gcal_id(title_hint: str, state: state_module.State) -> str | None:
    """
    Fuzzy-search written_events and calendar_snapshot for an event matching title_hint.
    Returns the gcal_event_id if found, else None.
    """
    if not title_hint:
        return None
    hint_lower = title_hint.lower()

    # 1. Search events this tool created
    for gcal_id, info in state.get_written_events().items():
        existing = info.get("title", "")
        if fuzz.ratio(hint_lower, existing.lower()) > _UPDATE_FUZZY_THRESHOLD:
            logger.debug("update lookup: matched written_event %r for hint %r", existing, title_hint)
            return gcal_id

    # 2. Fall back to calendar snapshot (all calendars)
    for gcal_id, info in state.calendar_snapshot().items():
        existing = info.get("title", "")
        if fuzz.ratio(hint_lower, existing.lower()) > _UPDATE_FUZZY_THRESHOLD:
            logger.debug("update lookup: matched snapshot event %r for hint %r", existing, title_hint)
            return gcal_id

    return None


def main() -> int:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.mock and not extractor.check_ollama_available():
        logger.warning(
            "Ollama is not running at %s — event extraction will be skipped. "
            "Start Ollama or use --mock for testing.",
            config.OLLAMA_BASE_URL,
        )

    sources = [s.strip() for s in args.source.split(",") if s.strip()] if args.source else _ALL_SOURCES

    if not args.mock:
        try:
            config.validate_for_sources(sources)
        except EnvironmentError as exc:
            logger.error("%s", exc)
            return 1

    state = state_module.load()

    if args.digest_only:
        _send_digests(state, args.dry_run)
        state_module.save(state)
        return 0

    # Capture run start time before fetching — used as last_run so that
    # messages arriving during the long Ollama extraction phase are not
    # silently dropped (their timestamps fall between fetch and end-of-run).
    run_start = datetime.now(timezone.utc)

    # ── Collect messages from all selected sources ───────────────────────────
    all_messages = []
    seen_connectors: set[type] = set()

    for source in sources:
        connector_cls = _CONNECTOR_REGISTRY.get(source)
        if connector_cls is None:
            logger.warning("Unknown source: %s — skipping", source)
            continue
        if connector_cls in seen_connectors:
            continue
        seen_connectors.add(connector_cls)

        connector = connector_cls()
        since = state.last_run(source)
        logger.info("Fetching %s since %s (mock=%s)", source, since.date(), args.mock)
        msgs = connector.fetch(since=since, mock=args.mock)
        logger.info("  → %d message(s)", len(msgs))
        all_messages.extend(msgs)

    # ── Extract candidate events and todos ───────────────────────────────────
    all_candidates: list[CandidateEvent] = []
    all_todos: list[CandidateTodo] = []
    if extractor.check_ollama_available() or args.mock:
        for msg in all_messages:
            if state.is_seen(msg.source, msg.id):
                continue
            events, todos = extractor.extract(msg)
            all_candidates.extend(events)
            all_todos.extend(todos)
            state.mark_seen(msg.source, msg.id)
    else:
        logger.warning("Skipping extraction — Ollama unavailable")

    logger.info("Extraction complete: %d candidate event(s) total", len(all_candidates))

    # ── Resolve update/cancel gcal IDs ───────────────────────────────────────
    for candidate in all_candidates:
        if (candidate.is_update or candidate.is_cancellation) and candidate.original_title_hint:
            if candidate.confidence >= _UPDATE_CANCEL_MIN_CONFIDENCE:
                gcal_id = _resolve_gcal_id(candidate.original_title_hint, state)
                if gcal_id:
                    candidate.gcal_event_id_to_update = gcal_id
                else:
                    logger.debug(
                        "update/cancel: no match found for hint %r — treating as new event",
                        candidate.original_title_hint,
                    )

    # ── Snapshot for cross-calendar dedup ───────────────────────────────────
    snapshot = state.calendar_snapshot()

    # ── Run counters for summary ──────────────────────────────────────────────
    counts = {
        "created": 0,
        "updated": 0,
        "cancelled": 0,
        "skipped_low_confidence": 0,
        "skipped_recurring": 0,
        "skipped_duplicate": 0,
    }

    # Collect event actions for a single batched Slack post at end of run
    pending_actions: list[dict] = []

    # Slack thread for this run (only created if something happens)
    thread_ts: str | None = None

    def _get_thread() -> str | None:
        nonlocal thread_ts
        if not args.dry_run and not args.mock and thread_ts is None:
            thread_ts = slack_notifier.get_or_create_day_thread(state)
        return thread_ts

    # ── Process each candidate ───────────────────────────────────────────────
    for candidate in all_candidates:

        # Recurring events — log and skip (don't create duplicates)
        if candidate.is_recurring:
            counts["skipped_recurring"] += 1
            logger.info(
                "RECURRING skipped: %r hint=%r (source=%s)",
                candidate.title, candidate.recurrence_hint, candidate.source,
            )
            if not args.dry_run and not args.mock:
                pending_actions.append({
                    "action": "skipped_recurring",
                    "title": candidate.title,
                    "start_dt": candidate.start_dt,
                    "source": candidate.source,
                    "category": candidate.category,
                })
            continue

        # Cancellation path
        if candidate.is_cancellation and candidate.gcal_event_id_to_update:
            deleted = gcal_writer.delete_event(candidate.gcal_event_id_to_update, dry_run=args.dry_run)
            if deleted:
                counts["cancelled"] += 1
                record_cancellation(
                    gcal_id=candidate.gcal_event_id_to_update,
                    title=candidate.original_title_hint or candidate.title,
                    source=candidate.source,
                )
                logger.info(
                    "%scancelled: %r (gcal_id=%s, source=%s)",
                    "DRY RUN " if args.dry_run else "",
                    candidate.original_title_hint or candidate.title,
                    candidate.gcal_event_id_to_update,
                    candidate.source,
                )
                if not args.dry_run and not args.mock:
                    pending_actions.append({
                        "action": "cancelled",
                        "title": candidate.original_title_hint or candidate.title,
                        "start_dt": None,
                        "source": candidate.source,
                    })
            continue

        # Update path
        if candidate.gcal_event_id_to_update:
            written, conflicts = gcal_writer.update_event(
                candidate.gcal_event_id_to_update, candidate, dry_run=args.dry_run
            )
            if written:
                counts["updated"] += 1
                log_event(written, action="updated", conflicts=conflicts)
                # Update written_events with new time
                state.add_written_event(
                    gcal_id=written.gcal_event_id,
                    title=candidate.title,
                    start_iso=candidate.start_dt.isoformat(),
                    fingerprint=written.fingerprint,
                    is_tentative=(candidate.confidence_band == "medium"),
                )
                logger.info(
                    "%supdated: %r on %s (confidence=%.2f, source=%s)",
                    "DRY RUN " if args.dry_run else "",
                    candidate.title, candidate.start_dt.date(),
                    candidate.confidence, candidate.source,
                )
                if not args.dry_run and not args.mock:
                    pending_actions.append({
                        "action": "updated",
                        "title": candidate.title,
                        "start_dt": candidate.start_dt,
                        "source": candidate.source,
                        "category": candidate.category,
                        "confidence_band": candidate.confidence_band,
                        "suggested_attendees": candidate.suggested_attendees or None,
                        "conflicts": conflicts or None,
                        "original_title": candidate.original_title_hint,
                    })
            elif args.dry_run:
                logger.info(
                    "DRY RUN: would update %r on %s (confidence=%.2f, source=%s)",
                    candidate.title, candidate.start_dt.date(),
                    candidate.confidence, candidate.source,
                )
            continue

        # Standard create path — fingerprint dedup
        fp = fingerprint(candidate)
        if state.has_fingerprint(fp):
            counts["skipped_duplicate"] += 1
            logger.debug("skip duplicate: %r (fingerprint match)", candidate.title)
            continue

        written, conflicts = gcal_writer.write_event(
            candidate, dry_run=args.dry_run, snapshot=snapshot
        )
        if written:
            counts["created"] += 1
            state.add_fingerprint(fp)
            log_event(written, action="created", conflicts=conflicts)
            state.add_written_event(
                gcal_id=written.gcal_event_id,
                title=candidate.title,
                start_iso=candidate.start_dt.isoformat(),
                fingerprint=written.fingerprint,
                is_tentative=(candidate.confidence_band == "medium"),
            )
            logger.info(
                "%screated: %r on %s (confidence=%.2f, band=%s, source=%s)",
                "DRY RUN " if args.dry_run else "",
                candidate.title, candidate.start_dt.date(),
                candidate.confidence, candidate.confidence_band, candidate.source,
            )
            if not args.dry_run and not args.mock:
                pending_actions.append({
                    "action": "created",
                    "title": candidate.title,
                    "start_dt": candidate.start_dt,
                    "source": candidate.source,
                    "category": candidate.category,
                    "confidence_band": candidate.confidence_band,
                    "suggested_attendees": candidate.suggested_attendees or None,
                    "conflicts": conflicts or None,
                })
        elif args.dry_run:
            logger.info(
                "DRY RUN: %r on %s (confidence=%.2f, band=%s, source=%s)",
                candidate.title, candidate.start_dt.date(),
                candidate.confidence, candidate.confidence_band, candidate.source,
            )
        else:
            # write_event returned None without dry_run = deduped by cross-calendar check
            counts["skipped_duplicate"] += 1

    total_actions = counts["created"] + counts["updated"] + counts["cancelled"]
    logger.info(
        "Run complete: %d created, %d updated, %d cancelled, %d recurring skipped, %d duplicates skipped%s",
        counts["created"], counts["updated"], counts["cancelled"],
        counts["skipped_recurring"], counts["skipped_duplicate"],
        " [DRY RUN]" if args.dry_run else "",
    )

    # ── Post batched event actions to Slack ──────────────────────────────────
    if pending_actions and not args.dry_run and not args.mock:
        t = _get_thread()
        if t:
            slack_notifier.post_event_batch(t, pending_actions)

    # ── Process todo items ───────────────────────────────────────────────────
    todos_created = 0
    if config.TODOIST_API_TOKEN and all_todos:
        project_id = todoist_writer.get_or_create_project(
            config.TODOIST_API_TOKEN, config.TODOIST_PROJECT_NAME, state
        )
        if project_id:
            for todo in all_todos:
                fp = todo_fingerprint(todo)
                if state.has_todo_fingerprint(fp):
                    logger.debug("skip duplicate todo: %r", todo.title)
                    continue
                ok = todoist_writer.create_task(
                    config.TODOIST_API_TOKEN, project_id, todo, dry_run=args.dry_run
                )
                if ok:
                    todos_created += 1
                    state.add_todo_fingerprint(fp)
                    logger.info(
                        "%stodo: %r (source=%s, priority=%s, confidence=%.2f)",
                        "DRY RUN " if args.dry_run else "",
                        todo.title, todo.source, todo.priority, todo.confidence,
                    )
                    if not args.dry_run and not args.mock:
                        t = _get_thread()
                        if t:
                            slack_notifier.post_todo_action(
                                thread_ts=t,
                                title=todo.title,
                                source=todo.source,
                                context=todo.context,
                                due_date=todo.due_date,
                                priority=todo.priority,
                            )
    elif all_todos and not config.TODOIST_API_TOKEN:
        logger.debug("todoist: %d todo(s) extracted but TODOIST_API_TOKEN not set — skipping", len(all_todos))

    # ── Post run summary to Slack thread ─────────────────────────────────────
    if not args.dry_run and not args.mock:
        t = _get_thread()
        # Only post summary if something happened this run
        if t and (total_actions > 0 or counts["skipped_recurring"] > 0
                  or counts["skipped_low_confidence"] > 0 or todos_created > 0):
            slack_notifier.post_run_summary(
                thread_ts=t,
                created=counts["created"],
                updated=counts["updated"],
                cancelled=counts["cancelled"],
                skipped_low_confidence=counts["skipped_low_confidence"],
                skipped_recurring=counts["skipped_recurring"],
                skipped_duplicate=counts["skipped_duplicate"],
                todos_created=todos_created,
            )

    # ── Update last_run timestamps ───────────────────────────────────────────
    # Use run_start (not now) so messages that arrived during extraction
    # are caught by the next run rather than falling through the gap.
    for source in sources:
        state.set_last_run(source, run_start)

    state_module.save(state)

    # ── Send digests ─────────────────────────────────────────────────────────
    if not args.dry_run and not args.mock:
        _send_digests(state, dry_run=False)
        state_module.save(state)  # persist last_digest_* and calendar snapshot

    return 0


def _send_digests(state: state_module.State, dry_run: bool = False) -> None:
    """
    Send daily and/or weekly digests if they're due.

    Daily digest:  sent once per day after DIGEST_DAILY_HOUR (local time).
    Weekly digest: sent once per week on DIGEST_WEEKLY_DOW (local time).
    """
    if dry_run:
        return

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()

    should_daily = (
        now_local.hour >= config.DIGEST_DAILY_HOUR
        and (
            state.last_digest_daily() is None
            or state.last_digest_daily().date() < now_utc.date()
        )
    )
    should_weekly = (
        now_local.weekday() == config.DIGEST_WEEKLY_DOW
        and (
            state.last_digest_weekly() is None
            or state.last_digest_weekly().date() < now_utc.date()
        )
    )

    if not should_daily and not should_weekly:
        logger.debug("no digest due (daily=%s, weekly=%s)", should_daily, should_weekly)
        return

    try:
        creds = google_auth.get_credentials(
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
            token_path=config.GCAL_TOKEN_JSON,
            credentials_path=config.GMAIL_CREDENTIALS_JSON,
            keyring_key="gcal_token",
        )
        service = build("calendar", "v3", credentials=creds)
        current_events = calendar_analyzer.fetch_year_ahead(service)
    except Exception as exc:
        logger.warning("_send_digests: failed to fetch calendar — %s", exc)
        return

    analysis = calendar_analyzer.analyze(current_events)
    new_events, updated_events, removed_events = _diff_calendar(
        current_events, state.calendar_snapshot()
    )

    if should_daily:
        logger.info("sending daily digest (%d new, %d updated, %d removed)",
                    len(new_events), len(updated_events), len(removed_events))
        digest_module.send_daily_digest(analysis, new_events, updated_events, removed_events, state)
        state.set_last_digest_daily()

    if should_weekly:
        logger.info("sending weekly digest (%d new, %d updated)",
                    len(new_events), len(updated_events))
        digest_module.send_weekly_digest(analysis, new_events, updated_events, state)
        state.set_last_digest_weekly()

    state.update_calendar_snapshot(current_events)


def _diff_calendar(
    current: list,
    snapshot: dict,
) -> tuple[list, list, list]:
    """
    Compare current year-ahead events against the last-run snapshot.

    Returns (new_events, updated_events, removed_events) as CalendarEvent lists.
    """
    current_by_id = {e.gcal_id: e for e in current}
    new_events = []
    updated_events = []

    for e in current:
        if e.gcal_id not in snapshot:
            new_events.append(e)
        else:
            prev = snapshot[e.gcal_id]
            if e.title != prev.get("title") or e.start_dt.isoformat() != prev.get("start"):
                updated_events.append(e)

    removed_events = []
    for gcal_id, prev in snapshot.items():
        if gcal_id not in current_by_id:
            try:
                removed_events.append(CalendarEvent(
                    gcal_id=gcal_id,
                    title=prev["title"],
                    start_dt=datetime.fromisoformat(prev["start"]),
                    end_dt=datetime.fromisoformat(prev["end"]),
                    location=prev.get("location"),
                    source_description=prev.get("source_description", ""),
                ))
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("skipping malformed snapshot entry %s: %s", gcal_id, exc)

    return new_events, updated_events, removed_events


if __name__ == "__main__":
    sys.exit(main())
