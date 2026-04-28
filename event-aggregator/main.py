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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone


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
from dedup import fingerprint, is_duplicate, persisted_events, todo_fingerprint
from logs.event_log import record as log_event, record_cancellation, record_decision
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


def _resolve_gcal_id(
    title_hint: str, state: state_module.State
) -> tuple[str, str] | None:
    """
    Fuzzy-search written_events and calendar_snapshot for an event matching title_hint.
    Returns (gcal_event_id, target_calendar_id) if found, else None.
    """
    if not title_hint:
        return None
    hint_lower = title_hint.lower()

    # 1. Search events this tool created
    for gcal_id, info in state.get_written_events().items():
        existing = info.get("title", "")
        if fuzz.ratio(hint_lower, existing.lower()) > _UPDATE_FUZZY_THRESHOLD:
            logger.debug("update lookup: matched written_event %r for hint %r", existing, title_hint)
            return (
                gcal_id,
                info.get("calendar_id") or config.GCAL_WEEKEND_CALENDAR_ID,
            )

    # 2. Fall back to calendar snapshot (both calendars)
    for gcal_id, info in state.calendar_snapshot().items():
        existing = info.get("title", "")
        if fuzz.ratio(hint_lower, existing.lower()) > _UPDATE_FUZZY_THRESHOLD:
            logger.debug("update lookup: matched snapshot event %r for hint %r", existing, title_hint)
            return (
                gcal_id,
                info.get("calendar_id") or config.GCAL_PRIMARY_CALENDAR_ID,
            )

    return None


def _format_calendar_context(events: list[CalendarEvent]) -> str:
    """
    Build a compact calendar context string for injection into the Ollama prompt.
    Skips all-day events (no time component). Hard cap applied by extractor.
    """
    lines = []
    for e in events:
        if getattr(e, "is_all_day", False):
            continue
        start_str = e.start_dt.strftime("%b %-d %-I:%M%p").lower()
        end_str = e.end_dt.strftime("%-I:%M%p").lower() if e.end_dt else ""
        time_range = f"{start_str}-{end_str}" if end_str else start_str
        lines.append(f"- {time_range}: {e.title}")
    return "\n".join(lines)


def _candidate_to_proposal_item(candidate: CandidateEvent, num: int, conflicts: list[str]) -> dict:
    """Serialize a CandidateEvent into a storable proposal dict."""
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
        "gcal_calendar_id_to_update": candidate.gcal_calendar_id_to_update,
        "is_recurring": candidate.is_recurring,
        "recurrence_hint": candidate.recurrence_hint,
        "suggested_attendees": candidate.suggested_attendees or [],
        "conflicts": conflicts,
        "kind": "event",  # vs "merge" — see _candidate_to_merge_proposal_item
    }


def _candidate_to_todo_proposal_item(todo: CandidateTodo, num: int, fp: str) -> dict:
    """Build a `kind:"todo"` proposal so the user approves before the task
    lands in Todoist. On approve, create_task runs with project_id=None
    (Todoist inbox) — see Tier 4.1 for project routing roadmap."""
    return {
        "num": num,
        "status": "pending",
        "kind": "todo",
        "title": todo.title,
        "context": todo.context,
        "due_date": todo.due_date,
        "priority": todo.priority,
        "confidence": todo.confidence,
        "source": todo.source,
        "source_id": todo.source_id,
        "source_url": todo.source_url,
        "fingerprint": fp,
    }


def _candidate_to_fuzzy_proposal_item(candidate: CandidateEvent, num: int) -> dict:
    """Build a `kind:"fuzzy_event"` proposal — no specific date determinable yet.
    User responds via the dashboard to either skip or run `cli add-event` with
    an explicit date."""
    return {
        "num": num,
        "status": "pending",
        "kind": "fuzzy_event",
        "title": candidate.title,
        "event_description": candidate.event_description or candidate.title,
        "confidence": candidate.confidence,
        "category": candidate.category,
        "source": candidate.source,
        "source_id": candidate.source_id,
        "source_url": candidate.source_url,
    }


def _candidate_to_merge_proposal_item(
    candidate: CandidateEvent,
    num: int,
    matched_gcal_id: str,
    matched_calendar_id: str,
    matched_title: str,
    matched_start_iso: str,
    additions: dict,
) -> dict:
    """Build a `kind:"merge"` proposal item for primary-calendar additive merges."""
    return {
        "num": num,
        "status": "pending",
        "kind": "merge",
        "title": candidate.title,
        "matched_title": matched_title,
        "matched_start_dt": matched_start_iso,
        "target_calendar_id": matched_calendar_id,
        "gcal_event_id": matched_gcal_id,
        "additions": additions,
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
        "suggested_attendees": candidate.suggested_attendees or [],
    }


def _proposal_item_to_candidate(item: dict) -> CandidateEvent:
    """Reconstruct a CandidateEvent from a stored proposal dict."""
    from datetime import timezone as tz
    start_dt = datetime.fromisoformat(item["start_dt"])
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=tz.utc)
    end_dt = None
    if item.get("end_dt"):
        end_dt = datetime.fromisoformat(item["end_dt"])
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=tz.utc)
    return CandidateEvent(
        title=item["title"],
        start_dt=start_dt,
        end_dt=end_dt,
        location=item.get("location"),
        confidence=item["confidence"],
        source=item["source"],
        source_id=item["source_id"],
        source_url=item.get("source_url"),
        confidence_band=item.get("confidence_band", "high"),
        is_update=item.get("is_update", False),
        original_title_hint=item.get("original_title_hint"),
        gcal_event_id_to_update=item.get("gcal_event_id_to_update"),
        gcal_calendar_id_to_update=item.get("gcal_calendar_id_to_update"),
        is_cancellation=item.get("is_cancellation", False),
        is_recurring=item.get("is_recurring", False),
        recurrence_hint=item.get("recurrence_hint"),
        suggested_attendees=item.get("suggested_attendees") or [],
        category=item.get("category", "other"),
    )



def _propose_events(
    all_candidates: list[CandidateEvent],
    state: state_module.State,
    snapshot: dict,
    dry_run: bool,
    mock: bool,
) -> dict:
    """
    In proposal mode: collect candidates into a batch and store in state.
    Slack posting happens in main() after this returns.
    Returns counts dict.
    """
    counts = {
        "skipped_recurring": 0,
        "skipped_duplicate": 0,
        "proposed": 0,
    }

    batch_items: list[dict] = []
    now = datetime.now(timezone.utc)

    # Get GCal service once for conflict checks (avoid per-candidate auth overhead)
    gcal_service_for_conflicts = None
    if not dry_run and not mock:
        try:
            gcal_service_for_conflicts = gcal_writer._get_service()
        except Exception:
            pass

    # Cross-run dedup: events we've already written or proposed in the last 30 days.
    known_events = persisted_events(state, days=30)

    for candidate in all_candidates:
        if candidate.is_recurring:
            counts["skipped_recurring"] += 1
            logger.info("RECURRING skipped (propose mode): %r", candidate.title)
            if state.add_recurring_notice(
                candidate.title, candidate.source, candidate.recurrence_hint
            ):
                logger.info(
                    "Recurring notice added: %r (hint=%r)",
                    candidate.title, candidate.recurrence_hint,
                )
            continue

        # Date-uncertainty: candidate has no specific date — emit a fuzzy_event
        # proposal so the user can either provide a date manually (cli add-event)
        # or skip. Fuzzy events bypass the past-event filter and the cross-
        # calendar dedup since they have no real start_dt to compare against.
        if candidate.date_certainty == "unknown":
            num = state.next_proposal_num()
            fuzzy_item = _candidate_to_fuzzy_proposal_item(candidate, num)
            batch_items.append(fuzzy_item)
            counts["proposed"] += 1
            continue

        # Skip past events
        if candidate.start_dt < now and not candidate.is_cancellation:
            logger.info("Skipping past event: %r on %s", candidate.title, candidate.start_dt.date())
            continue

        fp = fingerprint(candidate)
        if state.has_fingerprint(fp):
            counts["skipped_duplicate"] += 1
            logger.debug("Skip duplicate proposal: %r (fingerprint match)", candidate.title)
            continue
        if is_duplicate(candidate, known_events):
            counts["skipped_duplicate"] += 1
            logger.debug("Skip duplicate proposal: %r (fuzzy + window)", candidate.title)
            continue

        # Cross-calendar match: branch into merge proposal (primary), silent
        # patch (weekend), or pure-duplicate skip.
        match = gcal_writer._find_cross_calendar_match(candidate, snapshot) if snapshot else None
        if match:
            matched_gcal_id, matched_info = match
            matched_calendar = matched_info.get("calendar_id", "") or config.GCAL_PRIMARY_CALENDAR_ID
            additions = gcal_writer._compute_merge_additions(candidate, matched_info)
            matched_title = matched_info.get("title", "")
            if not additions:
                counts["skipped_duplicate"] += 1
                logger.debug(
                    "Skip duplicate (cross-calendar, no new info): %r ↔ %r",
                    candidate.title, matched_title,
                )
                state.add_fingerprint(fp)
                continue
            if matched_calendar == config.GCAL_WEEKEND_CALENDAR_ID:
                # Silent patch + dashboard notice — no approval needed.
                if not dry_run and not mock:
                    if gcal_writer.merge_event(
                        matched_calendar, matched_gcal_id, candidate, additions
                    ):
                        keys = ", ".join(additions.keys())
                        state.add_recurring_notice(
                            f"Merged into '{matched_title}': +{keys}",
                            candidate.source,
                        )
                        record_decision("merged_silent", {
                            "kind": "merge",
                            "title": matched_title,
                            "source": candidate.source,
                            "fingerprint": fp,
                        })
                state.add_fingerprint(fp)
                counts["skipped_duplicate"] += 1  # bookkeeping: not a new proposal
                continue
            # Primary match → emit a merge proposal for approval
            num = state.next_proposal_num()
            merge_item = _candidate_to_merge_proposal_item(
                candidate,
                num,
                matched_gcal_id=matched_gcal_id,
                matched_calendar_id=matched_calendar,
                matched_title=matched_title,
                matched_start_iso=matched_info.get("start", ""),
                additions=additions,
            )
            batch_items.append(merge_item)
            state.add_fingerprint(fp)
            counts["proposed"] += 1
            continue

        # Get conflict info upfront so it shows in the proposal
        conflicts: list[str] = []
        if gcal_service_for_conflicts and not candidate.is_cancellation:
            try:
                conflicts = gcal_writer._check_conflicts(gcal_service_for_conflicts, candidate)
            except Exception:
                pass

        num = state.next_proposal_num()
        item = _candidate_to_proposal_item(candidate, num, conflicts)
        batch_items.append(item)

        # Register fingerprint immediately to prevent cross-source re-proposal
        state.add_fingerprint(fp)
        counts["proposed"] += 1

    if not batch_items:
        return counts

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M")
    batch = {
        "batch_id": now_str,
        "slack_ts": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": batch_items,
    }
    state.add_proposal_batch(batch)

    if mock:
        for item in batch_items:
            logger.info(
                "MOCK PROPOSE #%d: %r on %s (confidence=%.2f, source=%s)",
                item["num"], item["title"],
                item.get("start_dt", "")[:10],
                item["confidence"], item["source"],
            )

    return counts


def _auto_create_events(
    all_candidates: list[CandidateEvent],
    state: state_module.State,
    snapshot: dict,
    dry_run: bool,
    mock: bool,
    get_thread,
) -> dict:
    """
    In auto mode: write events to GCal immediately (original behavior).
    Returns counts dict.
    """
    counts = {
        "created": 0,
        "updated": 0,
        "cancelled": 0,
        "skipped_low_confidence": 0,
        "skipped_recurring": 0,
        "skipped_duplicate": 0,
    }
    pending_actions: list[dict] = []
    known_events = persisted_events(state, days=30)

    for candidate in all_candidates:

        if candidate.is_recurring:
            counts["skipped_recurring"] += 1
            logger.info("RECURRING skipped: %r hint=%r (source=%s)", candidate.title, candidate.recurrence_hint, candidate.source)
            if not dry_run and not mock:
                state.add_recurring_notice(
                    candidate.title, candidate.source, candidate.recurrence_hint
                )
                pending_actions.append({
                    "action": "skipped_recurring",
                    "title": candidate.title,
                    "start_dt": candidate.start_dt,
                    "source": candidate.source,
                    "category": candidate.category,
                })
            continue

        if candidate.is_cancellation and candidate.gcal_event_id_to_update:
            target_cal = candidate.gcal_calendar_id_to_update or config.GCAL_WEEKEND_CALENDAR_ID
            # Auto mode never deletes from PRIMARY without approval — that's
            # a destructive write to a calendar we treat as read-mostly.
            if target_cal != config.GCAL_WEEKEND_CALENDAR_ID:
                logger.info(
                    "Skipping auto-cancel for %r: matched event lives on %s, not weekend",
                    candidate.original_title_hint or candidate.title, target_cal,
                )
                continue
            deleted = gcal_writer.delete_event(target_cal, candidate.gcal_event_id_to_update, dry_run=dry_run)
            if deleted:
                counts["cancelled"] += 1
                record_cancellation(
                    gcal_id=candidate.gcal_event_id_to_update,
                    title=candidate.original_title_hint or candidate.title,
                    source=candidate.source,
                )
                logger.info("%scancelled: %r (gcal_id=%s, source=%s)", "DRY RUN " if dry_run else "", candidate.original_title_hint or candidate.title, candidate.gcal_event_id_to_update, candidate.source)
                if not dry_run and not mock:
                    pending_actions.append({
                        "action": "cancelled",
                        "title": candidate.original_title_hint or candidate.title,
                        "start_dt": None,
                        "source": candidate.source,
                    })
            continue

        if candidate.gcal_event_id_to_update:
            target_cal = candidate.gcal_calendar_id_to_update or config.GCAL_WEEKEND_CALENDAR_ID
            if target_cal != config.GCAL_WEEKEND_CALENDAR_ID:
                logger.info(
                    "Skipping auto-update for %r: matched event lives on %s, not weekend",
                    candidate.title, target_cal,
                )
                continue
            written, conflicts = gcal_writer.update_event(target_cal, candidate.gcal_event_id_to_update, candidate, dry_run=dry_run)
            if written:
                counts["updated"] += 1
                log_event(written, action="updated", conflicts=conflicts)
                state.add_written_event(
                    gcal_id=written.gcal_event_id,
                    title=candidate.title,
                    start_iso=candidate.start_dt.isoformat(),
                    fingerprint=written.fingerprint,
                    is_tentative=(candidate.confidence_band == "medium"),
                    calendar_id=target_cal,
                )
                logger.info("%supdated: %r on %s (confidence=%.2f, source=%s)", "DRY RUN " if dry_run else "", candidate.title, candidate.start_dt.date(), candidate.confidence, candidate.source)
                if not dry_run and not mock:
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
            elif dry_run:
                logger.info("DRY RUN: would update %r on %s (confidence=%.2f, source=%s)", candidate.title, candidate.start_dt.date(), candidate.confidence, candidate.source)
            continue

        fp = fingerprint(candidate)
        if state.has_fingerprint(fp):
            counts["skipped_duplicate"] += 1
            logger.debug("skip duplicate: %r (fingerprint match)", candidate.title)
            continue
        if is_duplicate(candidate, known_events):
            counts["skipped_duplicate"] += 1
            logger.debug("skip duplicate: %r (fuzzy + window)", candidate.title)
            continue

        outcome = gcal_writer.write_event(candidate, dry_run=dry_run, snapshot=snapshot)
        if isinstance(outcome, gcal_writer.Inserted):
            written, conflicts = outcome.written, outcome.conflicts
            counts["created"] += 1
            state.add_fingerprint(fp)
            log_event(written, action="created", conflicts=conflicts)
            state.add_written_event(
                gcal_id=written.gcal_event_id,
                title=candidate.title,
                start_iso=candidate.start_dt.isoformat(),
                fingerprint=written.fingerprint,
                is_tentative=(candidate.confidence_band == "medium"),
                calendar_id=config.GCAL_WEEKEND_CALENDAR_ID,
            )
            logger.info("%screated: %r on %s (confidence=%.2f, band=%s, source=%s)", "DRY RUN " if dry_run else "", candidate.title, candidate.start_dt.date(), candidate.confidence, candidate.confidence_band, candidate.source)
            if not dry_run and not mock:
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
        elif isinstance(outcome, gcal_writer.Merged):
            # Silent merge into weekend — surface as a notice.
            counts["created"] += 0  # not a creation; bookkeeping stays accurate
            keys = ", ".join(outcome.additions.keys())
            logger.info(
                "merged into %r on weekend (gcal_id=%s): added %s",
                outcome.matched_title, outcome.gcal_event_id, keys,
            )
            state.add_recurring_notice(
                f"Merged into '{outcome.matched_title}': +{keys}",
                candidate.source,
            )
        elif isinstance(outcome, gcal_writer.MergeRequired):
            # Auto mode skips merge-into-primary because primary patches
            # require approval. Log so the user can see it in run logs.
            logger.info(
                "Auto-mode skip: %r matches primary event %r (would propose if in propose mode)",
                candidate.title, outcome.matched_title,
            )
        elif isinstance(outcome, gcal_writer.Skipped):
            if dry_run and outcome.reason == "dry_run":
                logger.info("DRY RUN: %r on %s (confidence=%.2f, band=%s, source=%s)", candidate.title, candidate.start_dt.date(), candidate.confidence, candidate.confidence_band, candidate.source)
            else:
                counts["skipped_duplicate"] += 1

    if pending_actions and not dry_run and not mock:
        t = get_thread()
        if t:
            slack_notifier.post_event_batch(t, pending_actions)

    return counts


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

    if not args.mock:
        from analyzers import image_analyzer
        if not image_analyzer.check_local_vision_available():
            logger.warning(
                "Local vision model '%s' not found in Ollama — "
                "image/PDF intake via the dispatcher will fail until you run "
                "`ollama pull %s`. This project no longer has a cloud fallback.",
                config.LOCAL_VISION_MODEL,
                config.LOCAL_VISION_MODEL,
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

    run_start = datetime.now(timezone.utc)
    today_str = run_start.strftime("%Y-%m-%d")

    # Slack day-thread (used in auto mode)
    thread_ts: str | None = None

    def _get_thread() -> str | None:
        nonlocal thread_ts
        if not args.dry_run and not args.mock and thread_ts is None:
            thread_ts = slack_notifier.get_or_create_day_thread(state)
        return thread_ts

    # ── Pre-Phase: Expire stale proposals ────────────────────────────────────
    expired_items: list[dict] = []
    if config.EVENT_APPROVAL_MODE == "propose" and not args.mock:
        expired_items = state.expire_old_proposals(hours=config.PROPOSAL_EXPIRY_HOURS)
        for item in expired_items:
            fp = item.get("fingerprint")
            if fp:
                state.remove_proposal_fingerprint(fp)
            record_decision("expired", item)
        if expired_items:
            logger.info("%d proposal(s) expired", len(expired_items))

    # ── Phase 1: Fetch calendar context for Ollama prompt injection ──────────
    calendar_context = ""
    if not args.mock and not args.dry_run:
        try:
            creds = google_auth.get_credentials(
                scopes=["https://www.googleapis.com/auth/calendar.events"],
                token_path=config.GCAL_TOKEN_JSON,
                credentials_path=config.GMAIL_CREDENTIALS_JSON,
                keyring_key="gcal_token",
            )
            gcal_service = build("calendar", "v3", credentials=creds)
            upcoming = calendar_analyzer.fetch_upcoming(
                gcal_service, weeks=config.CALENDAR_CONTEXT_WEEKS
            )
            calendar_context = _format_calendar_context(upcoming)
            logger.debug("Calendar context: %d upcoming events (%d chars)", len(upcoming), len(calendar_context))
        except Exception as exc:
            logger.debug("Could not fetch calendar context: %s", exc)

    # ── Phase 2: Collect messages from all selected sources ──────────────────
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
        msgs, status = connector.fetch(since=since, mock=args.mock)
        logger.info("  → %d message(s) [status=%s]", len(msgs), status.code.value)
        all_messages.extend(msgs)

    # ── Phase 3: Extract candidate events and todos ──────────────────────────
    all_candidates: list[CandidateEvent] = []
    all_todos: list[CandidateTodo] = []
    extraction_ran = False
    ollama_state_changed = False
    if extractor.check_ollama_available() or args.mock:
        extraction_ran = True
        if not args.mock and state.mark_ollama_up():
            logger.info("Ollama is reachable again — clearing down state")
            ollama_state_changed = True
        for msg in all_messages:
            if state.is_seen(msg.source, msg.id):
                continue
            events, todos = extractor.extract(msg, calendar_context=calendar_context)
            all_candidates.extend(events)
            all_todos.extend(todos)
            state.mark_seen(msg.source, msg.id)
    else:
        logger.warning("Skipping extraction — Ollama unavailable")
        # Surface to the dashboard. Count how many fresh messages we couldn't process
        # so the user knows what's piling up.
        unseen = sum(
            1 for msg in all_messages if not state.is_seen(msg.source, msg.id)
        )
        was_down = bool(state.ollama_health().get("down_since"))
        state.mark_ollama_down(skipped=unseen)
        if not was_down:
            ollama_state_changed = True

    # If Ollama health flipped this run, force a dashboard render so the user
    # sees the alert (or its clearance) immediately — even if no proposals fire.
    if (
        ollama_state_changed
        and config.EVENT_APPROVAL_MODE == "propose"
        and not args.dry_run
        and not args.mock
    ):
        today_str = run_start.strftime("%Y-%m-%d")
        all_items = state.get_all_proposal_items_for_dashboard(today_str)
        slack_notifier.post_or_update_dashboard(all_items, state)

    logger.info("Extraction complete: %d candidate event(s) total", len(all_candidates))

    # ── Phase 4: Resolve update/cancel gcal IDs ──────────────────────────────
    for candidate in all_candidates:
        if (candidate.is_update or candidate.is_cancellation) and candidate.original_title_hint:
            if candidate.confidence >= _UPDATE_CANCEL_MIN_CONFIDENCE:
                resolved = _resolve_gcal_id(candidate.original_title_hint, state)
                if resolved:
                    candidate.gcal_event_id_to_update, candidate.gcal_calendar_id_to_update = resolved
                else:
                    logger.debug(
                        "update/cancel: no match found for hint %r — treating as new event",
                        candidate.original_title_hint,
                    )

    snapshot = state.calendar_snapshot()

    # ── Phase 5: Branch by approval mode ────────────────────────────────────
    propose_counts: dict = {}
    auto_counts: dict = {}

    if config.EVENT_APPROVAL_MODE == "propose":
        propose_counts = _propose_events(
            all_candidates, state, snapshot,
            dry_run=args.dry_run, mock=args.mock,
        )
        proposed = propose_counts.get("proposed", 0)
        skipped_recurring = propose_counts.get("skipped_recurring", 0)
        skipped_duplicate = propose_counts.get("skipped_duplicate", 0)
        logger.info(
            "Propose mode: %d proposed, %d recurring skipped, %d duplicates skipped%s",
            proposed, skipped_recurring, skipped_duplicate,
            " [DRY RUN]" if args.dry_run else "",
        )
        # Post/update the live dashboard whenever there's something to show
        if not args.mock and not args.dry_run:
            all_dashboard_items = state.get_all_proposal_items_for_dashboard(today_str)
            dashboard_exists = state.get_proposal_dashboard_ts(today_str) is not None
            if all_dashboard_items or (dashboard_exists and expired_items):
                slack_notifier.post_or_update_dashboard(all_dashboard_items, state)
    else:
        auto_counts = _auto_create_events(
            all_candidates, state, snapshot,
            dry_run=args.dry_run, mock=args.mock, get_thread=_get_thread,
        )
        logger.info(
            "Auto mode: %d created, %d updated, %d cancelled, %d recurring skipped, %d duplicates skipped%s",
            auto_counts.get("created", 0), auto_counts.get("updated", 0),
            auto_counts.get("cancelled", 0), auto_counts.get("skipped_recurring", 0),
            auto_counts.get("skipped_duplicate", 0),
            " [DRY RUN]" if args.dry_run else "",
        )

    # ── Phase 6: Process todo items ──────────────────────────────────────────
    todos_created = 0
    if config.TODOIST_API_TOKEN and all_todos:
        if config.EVENT_APPROVAL_MODE == "propose":
            # Tier 4.1: route todos through the proposal flow rather than
            # auto-creating. Each pending todo becomes a kind:"todo" item on
            # the dashboard with [Add to Todoist] / [Skip] buttons.
            todo_batch_items: list[dict] = []
            for todo in all_todos:
                fp = todo_fingerprint(todo)
                if state.has_todo_fingerprint(fp):
                    logger.debug("skip duplicate todo (fingerprint match): %r", todo.title)
                    continue
                num = state.next_proposal_num()
                todo_batch_items.append(_candidate_to_todo_proposal_item(todo, num, fp))
                # Reserve the fingerprint so the same todo doesn't re-propose
                # if the worker re-extracts the same source message later.
                state.add_todo_fingerprint(fp)
            if todo_batch_items:
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M")
                state.add_proposal_batch({
                    "batch_id": now_str + "_todos",
                    "slack_ts": None,
                    "created_at": now_str,
                    "items": todo_batch_items,
                })
        else:
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

    # ── Phase 7: file intake retired — dispatcher handles #ian-image-intake ─
    # The launchd loop no longer scans Slack for uploads. Files arriving in
    # #ian-image-intake are processed by the dispatcher (long-running Socket
    # Mode) which invokes `main.py ingest-image --file <path>` for events.
    # Legacy staging is still flushed to NAS opportunistically here.
    files_processed = 0
    if not args.dry_run and not args.mock:
        try:
            from writers import file_writer
            flushed = file_writer.flush_pending_staged(dry_run=False)
            if flushed:
                logger.info("Flushed %d previously staged file(s) to NAS", len(flushed))
        except Exception as exc:
            logger.warning("Error flushing staged files: %s", exc)

    # ── Phase 8: Post run summary (auto mode only — propose mode uses the
    # dashboard footer as the summary; Tier 3.1 dropped the per-run thread
    # post in propose mode to keep the channel quiet). ────────────────────────
    if not args.dry_run and not args.mock and config.EVENT_APPROVAL_MODE != "propose":
        t = _get_thread()
        total_actions = auto_counts.get("created", 0) + auto_counts.get("updated", 0) + auto_counts.get("cancelled", 0)
        if t and (total_actions > 0 or auto_counts.get("skipped_recurring", 0) > 0
                  or auto_counts.get("skipped_low_confidence", 0) > 0 or todos_created > 0
                  or files_processed > 0):
            slack_notifier.post_run_summary(
                thread_ts=t,
                created=auto_counts.get("created", 0),
                updated=auto_counts.get("updated", 0),
                cancelled=auto_counts.get("cancelled", 0),
                skipped_low_confidence=auto_counts.get("skipped_low_confidence", 0),
                skipped_recurring=auto_counts.get("skipped_recurring", 0),
                skipped_duplicate=auto_counts.get("skipped_duplicate", 0),
                todos_created=todos_created,
                files_processed=files_processed,
            )

    # ── Update last_run timestamps (only if extraction actually ran) ──────────
    if extraction_ran:
        for source in sources:
            state.set_last_run(source, run_start)
    else:
        logger.debug("Skipping last_run update — Ollama was unavailable")

    state_module.save(state)

    # ── Send digests ──────────────────────────────────────────────────────────
    if not args.dry_run and not args.mock:
        _send_digests(state, dry_run=False)
        state_module.save(state)

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
            scopes=["https://www.googleapis.com/auth/calendar.events"],
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

    pending_proposals = len(state.get_pending_proposals())

    if should_daily:
        logger.info("sending daily digest (%d new, %d updated, %d removed)",
                    len(new_events), len(updated_events), len(removed_events))
        digest_module.send_daily_digest(
            analysis, new_events, updated_events, removed_events, state,
            pending_proposals=pending_proposals,
        )
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


_SUBCOMMANDS = {
    "classify", "ingest-image", "enqueue-image",
    "approve", "reject",
    "add-event", "status", "query",
    "config", "undo-last", "changes", "forget", "swap",
    "bump-dashboard",
}


def fetch_only() -> int:
    """
    Fetch-only mode (Tier 2.4): poll connectors, enqueue messages into
    state.text_queue, advance last_run watermarks, and record per-connector
    health into state.connector_health (Tier 2 — Intake Audit). No LLM calls.

    Designed to run on a cron / launchd timer. The worker (`main.py worker`)
    consumes the queue separately.

    Watermark advancement policy:
      - ok: advance (happy path)
      - unsupported_os / no_credentials: advance (terminal-by-design — no
        catch-up is possible / meaningful, keep window bounded)
      - all other non-OK codes (auth_error, permission_denied, network_error,
        schema_error, unknown_error): do NOT advance — caller will catch up
        on the missed window when the issue is resolved.

    Floor: `since` is clamped to (now - 14 days) so a long-broken source
    doesn't query an arbitrarily wide window after a fix.
    """
    state = state_module.load()
    sources = list(_CONNECTOR_REGISTRY.keys())
    config.validate_for_sources([s for s in sources if s in {"gmail", "gcal", "slack"}])

    enqueued = 0
    run_start = datetime.now(timezone.utc)
    seen_connectors: set = set()
    PER_SOURCE_TIMEOUT_SEC = 60
    SINCE_FLOOR = run_start - timedelta(days=14)
    ADVANCE_ON_STATUS = {"ok", "unsupported_os", "no_credentials"}

    for source in sources:
        cls = _CONNECTOR_REGISTRY.get(source)
        if cls is None or cls in seen_connectors:
            continue
        seen_connectors.add(cls)
        # Sibling sources share the same connector class (e.g. messenger +
        # instagram both point at NotificationCenterConnector). Record health
        # and advance watermarks for ALL siblings on each fetch — otherwise
        # the second sibling appears "missing" on the dashboard.
        sibling_sources = [s for s, c in _CONNECTOR_REGISTRY.items() if c is cls]
        since = max(state.last_run(source), SINCE_FLOOR)

        def _do_fetch(connector_cls=cls, _since=since):
            return connector_cls().fetch(since=_since, mock=False)

        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                msgs, status = ex.submit(_do_fetch).result(timeout=PER_SOURCE_TIMEOUT_SEC)
        except FutureTimeout:
            logger.warning(
                "fetch-only: %s timed out after %ds — skipping",
                source, PER_SOURCE_TIMEOUT_SEC,
            )
            for sib in sibling_sources:
                state.record_connector_status(sib, "network_error", "fetch timeout", run_start)
            continue
        except Exception as exc:
            # Connectors should never raise — but if one slips through, treat as unknown.
            logger.warning(
                "fetch-only: %s connector raised (should not happen): %s",
                source, type(exc).__name__,
            )
            for sib in sibling_sources:
                state.record_connector_status(
                    sib, "unknown_error", type(exc).__name__, run_start,
                )
            continue

        # Record health for every outcome (even OK), for all sibling sources.
        for sib in sibling_sources:
            state.record_connector_status(sib, status.code.value, status.message, run_start)

        for msg in msgs:
            # NotificationCenterConnector returns msg.source ∈ {"messenger",
            # "instagram"} — use msg.source for last_run / dedup, not the
            # registry key.
            if state.is_seen(msg.source, msg.id):
                continue
            state.enqueue_text_job(
                source=msg.source,
                msg_id=msg.id,
                body_text=msg.body_text,
                metadata=msg.metadata,
                timestamp_iso=msg.timestamp.isoformat(),
            )
            enqueued += 1

        if status.code.value in ADVANCE_ON_STATUS:
            for sib in sibling_sources:
                state.set_last_run(sib, run_start)

    state.prune()
    state_module.save(state)
    logger.info("fetch-only: enqueued %d new message(s); text_queue depth=%d",
                enqueued, state.text_queue_depth())
    return 0


if __name__ == "__main__":
    # Route to subcommands first; everything else falls through to the
    # existing full-run pipeline (backwards-compat with the legacy LaunchAgent).
    if len(sys.argv) > 1:
        first = sys.argv[1]
        if first == "fetch-only":
            sys.exit(fetch_only())
        if first == "worker":
            import worker
            sys.exit(worker.run_worker())
        if first in _SUBCOMMANDS:
            import cli
            sys.exit(cli.main())
    sys.exit(main())
