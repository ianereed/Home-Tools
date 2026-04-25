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

import requests

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
    parser.add_argument("--force", action="store_true", help="Run all phases regardless of idle state")
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


def _unload_ollama_models() -> None:
    """Explicitly unload all Ollama models to free RAM immediately."""
    for model in [config.OLLAMA_MODEL, config.LOCAL_VISION_MODEL]:
        if not model:
            continue
        try:
            requests.post(
                f"{config.OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "keep_alive": 0},
                timeout=10,
            )
            logger.debug("Unloaded Ollama model: %s", model)
        except Exception:
            pass  # best-effort — model may not be loaded


def _format_calendar_context(events: list[CalendarEvent]) -> str:
    """
    Build a compact calendar context string for injection into the Ollama prompt.
    Skips all-day events (no time component). Hard cap applied by extractor.
    """
    lines = []
    for e in events:
        # Skip all-day events (start_dt is midnight UTC with no time significance)
        try:
            if e.start_dt.hour == 0 and e.start_dt.minute == 0 and e.start_dt.second == 0:
                # Heuristic: all-day events stored as UTC midnight
                continue
        except AttributeError:
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
        "is_recurring": candidate.is_recurring,
        "recurrence_hint": candidate.recurrence_hint,
        "suggested_attendees": candidate.suggested_attendees or [],
        "conflicts": conflicts,
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
        is_cancellation=item.get("is_cancellation", False),
        is_recurring=item.get("is_recurring", False),
        recurrence_hint=item.get("recurrence_hint"),
        suggested_attendees=item.get("suggested_attendees") or [],
        category=item.get("category", "other"),
    )


def _process_pending_approvals(
    state: state_module.State,
    dry_run: bool,
    thread_ts: str | None,
) -> tuple[int, int, int]:
    """
    Check pending proposals for approval/rejection replies and act on them.

    Returns (approved_count, rejected_count, expired_count).
    """
    approved = 0
    rejected = 0
    snapshot = state.calendar_snapshot()

    # 1. Expire old proposals
    expired_items = state.expire_old_proposals(hours=config.PROPOSAL_EXPIRY_HOURS)
    for item in expired_items:
        fp = item.get("fingerprint")
        if fp:
            state.remove_proposal_fingerprint(fp)
        logger.info("Proposal #%d expired: %r", item["num"], item["title"])
    expired = len(expired_items)

    if expired and thread_ts and not dry_run:
        titles = ", ".join(f"#{i['num']} {i['title']}" for i in expired_items[:5])
        slack_notifier.post_to_thread(
            thread_ts,
            f":hourglass: {expired} proposal{'s' if expired != 1 else ''} expired (no response after "
            f"{config.PROPOSAL_EXPIRY_HOURS}h): {titles}",
        )

    # 2. Check for approvals in each pending batch
    day_thread_ts, _ = state.get_day_thread()
    if not day_thread_ts:
        return approved, rejected, expired

    for batch in state.get_pending_proposals():
        batch_slack_ts = batch.get("slack_ts")
        if not batch_slack_ts:
            continue

        try:
            replies = slack_notifier.check_proposal_replies(day_thread_ts, batch_slack_ts)
        except Exception as exc:
            logger.warning("Error checking proposal replies for batch %s: %s", batch.get("batch_id"), exc)
            continue

        approve_all = replies["approve_all"]
        reject_all = replies["reject_all"]
        approve_nums = set(replies["approve_nums"])
        reject_nums = set(replies["reject_nums"])

        # Process rejections first so approvals win if both specified for same #
        for item in batch.get("items", []):
            num = item["num"]
            if item["status"] != "pending":
                continue

            should_reject = reject_all or num in reject_nums
            should_approve = approve_all or num in approve_nums

            if should_reject and not should_approve:
                rejected_item = state.reject_proposal(num)
                if rejected_item:
                    fp = rejected_item.get("fingerprint")
                    if fp:
                        state.remove_proposal_fingerprint(fp)
                    rejected += 1
                    logger.info("Proposal #%d rejected: %r", num, item["title"])

            elif should_approve:
                approved_item = state.approve_proposal(num)
                if not approved_item:
                    continue

                candidate = _proposal_item_to_candidate(approved_item)
                now = datetime.now(timezone.utc)

                # Skip if event is now in the past
                if candidate.start_dt < now:
                    logger.info("Proposal #%d skipped — event time has passed: %r", num, candidate.title)
                    if thread_ts and not dry_run:
                        slack_notifier.post_to_thread(
                            thread_ts,
                            f":warning: Proposal #{num} skipped — event time has already passed: *{candidate.title}*",
                        )
                    continue

                # Execute the appropriate GCal action
                action_taken = None
                new_conflicts: list[str] = []

                if candidate.is_cancellation and candidate.gcal_event_id_to_update:
                    deleted = gcal_writer.delete_event(
                        candidate.gcal_event_id_to_update, dry_run=dry_run
                    )
                    if deleted:
                        record_cancellation(
                            gcal_id=candidate.gcal_event_id_to_update,
                            title=candidate.original_title_hint or candidate.title,
                            source=candidate.source,
                        )
                        action_taken = "cancelled"

                elif candidate.gcal_event_id_to_update:
                    written, new_conflicts = gcal_writer.update_event(
                        candidate.gcal_event_id_to_update, candidate, dry_run=dry_run
                    )
                    if written:
                        state.add_written_event(
                            gcal_id=written.gcal_event_id,
                            title=candidate.title,
                            start_iso=candidate.start_dt.isoformat(),
                            fingerprint=written.fingerprint,
                            is_tentative=(candidate.confidence_band == "medium"),
                        )
                        log_event(written, action="updated", conflicts=new_conflicts)
                        action_taken = "updated"

                else:
                    written, new_conflicts = gcal_writer.write_event(
                        candidate, dry_run=dry_run, snapshot=snapshot
                    )
                    if written:
                        state.add_fingerprint(written.fingerprint)
                        state.add_written_event(
                            gcal_id=written.gcal_event_id,
                            title=candidate.title,
                            start_iso=candidate.start_dt.isoformat(),
                            fingerprint=written.fingerprint,
                            is_tentative=(candidate.confidence_band == "medium"),
                        )
                        log_event(written, action="created", conflicts=new_conflicts)
                        action_taken = "created"

                if action_taken:
                    approved += 1
                    logger.info(
                        "%sProposal #%d %s: %r on %s",
                        "DRY RUN " if dry_run else "",
                        num, action_taken, candidate.title,
                        candidate.start_dt.date() if not candidate.is_cancellation else "n/a",
                    )
                    if thread_ts and not dry_run:
                        action_icon = {
                            "created": ":white_check_mark:",
                            "updated": ":pencil2:",
                            "cancelled": ":wastebasket:",
                        }.get(action_taken, ":white_check_mark:")
                        start_str = ""
                        if not candidate.is_cancellation:
                            try:
                                start_str = f" | {candidate.start_dt.strftime('%b %-d %-I:%M%p').lower()}"
                            except Exception:
                                pass
                        conflict_note = ""
                        if new_conflicts:
                            conflict_note = f"\n  :warning: New conflict: {', '.join(new_conflicts[:3])}"
                        slack_notifier.post_to_thread(
                            thread_ts,
                            f"{action_icon} #{num} {action_taken}: *{candidate.title}*{start_str}{conflict_note}",
                        )

    return approved, rejected, expired


def _propose_events(
    all_candidates: list[CandidateEvent],
    state: state_module.State,
    snapshot: dict,
    dry_run: bool,
    mock: bool,
    get_thread,
) -> dict:
    """
    In proposal mode: collect candidates into a batch, post to Slack, store in state.
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

    for candidate in all_candidates:
        if candidate.is_recurring:
            counts["skipped_recurring"] += 1
            logger.info("RECURRING skipped (propose mode): %r", candidate.title)
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

    # Build the batch
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M")
    batch = {
        "batch_id": now_str,
        "slack_ts": None,  # filled in after posting
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": batch_items,
    }
    state.add_proposal_batch(batch)

    # Post to Slack
    if not mock:
        thread_ts = get_thread()
        if thread_ts:
            posted_ts = slack_notifier.post_proposals(thread_ts, batch_items)
            if posted_ts:
                state.set_proposal_slack_ts(now_str, posted_ts)
                logger.info(
                    "Posted %d proposal(s) to Slack (batch %s, ts=%s)",
                    len(batch_items), now_str, posted_ts,
                )
            else:
                logger.warning("Failed to post proposals to Slack")
    else:
        # Log proposals for mock/dry-run mode
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

    for candidate in all_candidates:

        if candidate.is_recurring:
            counts["skipped_recurring"] += 1
            logger.info("RECURRING skipped: %r hint=%r (source=%s)", candidate.title, candidate.recurrence_hint, candidate.source)
            if not dry_run and not mock:
                pending_actions.append({
                    "action": "skipped_recurring",
                    "title": candidate.title,
                    "start_dt": candidate.start_dt,
                    "source": candidate.source,
                    "category": candidate.category,
                })
            continue

        if candidate.is_cancellation and candidate.gcal_event_id_to_update:
            deleted = gcal_writer.delete_event(candidate.gcal_event_id_to_update, dry_run=dry_run)
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
            written, conflicts = gcal_writer.update_event(candidate.gcal_event_id_to_update, candidate, dry_run=dry_run)
            if written:
                counts["updated"] += 1
                log_event(written, action="updated", conflicts=conflicts)
                state.add_written_event(
                    gcal_id=written.gcal_event_id,
                    title=candidate.title,
                    start_iso=candidate.start_dt.isoformat(),
                    fingerprint=written.fingerprint,
                    is_tentative=(candidate.confidence_band == "medium"),
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

        written, conflicts = gcal_writer.write_event(candidate, dry_run=dry_run, snapshot=snapshot)
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
        elif dry_run:
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

    # Slack thread (lazily created on first action)
    thread_ts: str | None = None

    def _get_thread() -> str | None:
        nonlocal thread_ts
        if not args.dry_run and not args.mock and thread_ts is None:
            thread_ts = slack_notifier.get_or_create_day_thread(state)
        return thread_ts

    # ── Phase 0: Process pending approvals ──────────────────────────────────
    if config.EVENT_APPROVAL_MODE == "propose" and not args.mock:
        t = _get_thread()
        approved, rejected, expired = _process_pending_approvals(
            state, dry_run=args.dry_run, thread_ts=t
        )
        if approved or rejected or expired:
            logger.info(
                "Approvals: %d approved, %d rejected, %d expired",
                approved, rejected, expired,
            )

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
        msgs = connector.fetch(since=since, mock=args.mock)
        logger.info("  → %d message(s)", len(msgs))
        all_messages.extend(msgs)

    # ── Time-window check: only run heavy phases overnight ───────────────────
    from zoneinfo import ZoneInfo
    extraction_ran = False
    if args.mock or args.force:
        heavy_phases_allowed = True
    else:
        local_hour = datetime.now(ZoneInfo(config.USER_TIMEZONE)).hour
        start = config.OLLAMA_ACTIVE_HOUR_START
        end = config.OLLAMA_ACTIVE_HOUR_END
        if start <= end:
            heavy_phases_allowed = start <= local_hour < end
        else:  # wrap-around window (e.g. 22 → 6)
            heavy_phases_allowed = local_hour >= start or local_hour < end
        if not heavy_phases_allowed:
            logger.info(
                "Outside Ollama active window (hour=%d, window=%d-%d) — "
                "deferring extraction and image analysis until next overnight run",
                local_hour, start, end,
            )

    # ── Phase 3: Extract candidate events and todos ──────────────────────────
    all_candidates: list[CandidateEvent] = []
    all_todos: list[CandidateTodo] = []
    if not heavy_phases_allowed:
        logger.debug("Skipping extraction — outside Ollama active window")
    elif extractor.check_ollama_available() or args.mock:
        extraction_ran = True
        for msg in all_messages:
            if state.is_seen(msg.source, msg.id):
                continue
            events, todos = extractor.extract(msg, calendar_context=calendar_context)
            all_candidates.extend(events)
            all_todos.extend(todos)
            state.mark_seen(msg.source, msg.id)
    else:
        logger.warning("Skipping extraction — Ollama unavailable")

    logger.info("Extraction complete: %d candidate event(s) total", len(all_candidates))

    # ── Phase 4: Resolve update/cancel gcal IDs ──────────────────────────────
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

    snapshot = state.calendar_snapshot()

    # ── Phase 5: Branch by approval mode ────────────────────────────────────
    propose_counts: dict = {}
    auto_counts: dict = {}

    if config.EVENT_APPROVAL_MODE == "propose":
        propose_counts = _propose_events(
            all_candidates, state, snapshot,
            dry_run=args.dry_run, mock=args.mock, get_thread=_get_thread,
        )
        proposed = propose_counts.get("proposed", 0)
        skipped_recurring = propose_counts.get("skipped_recurring", 0)
        skipped_duplicate = propose_counts.get("skipped_duplicate", 0)
        logger.info(
            "Propose mode: %d proposed, %d recurring skipped, %d duplicates skipped%s",
            proposed, skipped_recurring, skipped_duplicate,
            " [DRY RUN]" if args.dry_run else "",
        )
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
    if heavy_phases_allowed and not args.dry_run and not args.mock:
        try:
            from writers import file_writer
            flushed = file_writer.flush_pending_staged(dry_run=False)
            if flushed:
                logger.info("Flushed %d previously staged file(s) to NAS", len(flushed))
        except Exception as exc:
            logger.warning("Error flushing staged files: %s", exc)

    # ── Unload models after heavy phases ───────────────────────────────────
    if heavy_phases_allowed and not args.mock:
        _unload_ollama_models()

    # ── Phase 8: Post run summary to Slack thread ────────────────────────────
    if not args.dry_run and not args.mock:
        t = _get_thread()
        pending_proposal_count = len(state.get_pending_proposals())
        # In propose mode, report proposals in the summary; in auto mode, use auto counts
        if config.EVENT_APPROVAL_MODE == "propose":
            proposed = propose_counts.get("proposed", 0)
            skipped_recurring = propose_counts.get("skipped_recurring", 0)
            skipped_duplicate = propose_counts.get("skipped_duplicate", 0)
            if t and (proposed or skipped_recurring or skipped_duplicate or todos_created or files_processed):
                slack_notifier.post_run_summary(
                    thread_ts=t,
                    created=0,
                    updated=0,
                    cancelled=0,
                    skipped_low_confidence=0,
                    skipped_recurring=skipped_recurring,
                    skipped_duplicate=skipped_duplicate,
                    todos_created=todos_created,
                    files_processed=files_processed,
                    proposed=proposed,
                    pending_proposals=pending_proposal_count,
                )
        else:
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
        logger.debug("Skipping last_run update — extraction was deferred")

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
    "classify", "ingest-image", "approve", "reject",
    "add-event", "status", "query",
}


if __name__ == "__main__":
    # Route to the CLI module when the first argv is a known subcommand.
    # Everything else (flags, or no args) falls through to the existing
    # full-run pipeline — preserves backward compat with the LaunchAgent.
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        import cli
        sys.exit(cli.main())
    sys.exit(main())
