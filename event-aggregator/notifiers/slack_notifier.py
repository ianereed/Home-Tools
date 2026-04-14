"""
Slack notifier — posts to ian-event-aggregator channel with daily threading.

One Slack thread per calendar day. All event actions (created, updated, cancelled)
and the run summary are posted as replies to that day's thread.
The thread opener is only created when the first action of the day occurs — no
empty threads from runs that found nothing.

Thread ts is persisted in state.json so replies stay in the same thread all day.
"""
from __future__ import annotations

import fcntl
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import state as state_module

import config

logger = logging.getLogger(__name__)


def _client():
    from slack_sdk import WebClient
    return WebClient(token=config.SLACK_BOT_TOKEN)


def get_or_create_day_thread(state: "state_module.State") -> str | None:
    """
    Return the Slack thread_ts for today's thread in ian-event-aggregator.
    Creates a new top-level post if today has no thread yet.
    Returns None if Slack is not configured or the post fails.
    """
    if not config.SLACK_BOT_TOKEN:
        return None

    today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    thread_ts, thread_date = state.get_day_thread()

    if thread_ts and thread_date == today_str:
        return thread_ts

    # Use a file lock so two overlapping runs don't create duplicate day threads
    import state as _state
    lock_path = _state.STATE_PATH.parent / ".slack_thread.lock"
    try:
        with lock_path.open("a") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            # Re-read from disk after acquiring lock — another process may have
            # already created today's thread while we were waiting
            fresh = _state.load()
            thread_ts, thread_date = fresh.get_day_thread()
            if thread_ts and thread_date == today_str:
                state.set_day_thread(thread_ts, today_str)
                return thread_ts

            # New day — create a fresh thread opener
            client = _client()
            date_display = datetime.now(tz=timezone.utc).strftime("%B %-d, %Y")
            result = client.chat_postMessage(
                channel=config.SLACK_NOTIFY_CHANNEL,
                text=f"Event Aggregator — {date_display}",
            )
            if result.get("ok"):
                ts = result["ts"]
                state.set_day_thread(ts, today_str)
                _state.save(state)  # flush to disk inside the lock before releasing
                return ts
            logger.warning("slack notifier: failed to create day thread: %s", result.get("error"))
            return None
    except Exception as exc:
        logger.warning("slack notifier: could not create day thread: %s", exc)
        return None


def post_event_action(
    thread_ts: str,
    action: str,
    title: str,
    start_dt: datetime | None,
    source: str,
    category: str = "other",
    confidence_band: str = "high",
    suggested_attendees: list[dict] | None = None,
    conflicts: list[str] | None = None,
    original_title: str | None = None,
) -> bool:
    """
    Post a single event action as a reply to the day thread.

    action: "created", "updated", "cancelled", "skipped_recurring"
    Returns True on success.
    """
    if not config.SLACK_BOT_TOKEN or not thread_ts:
        return False

    action_icon = {
        "created": ":calendar:",
        "updated": ":pencil2:",
        "cancelled": ":wastebasket:",
        "skipped_recurring": ":repeat:",
    }.get(action, ":calendar:")

    action_label = {
        "created": "created?" if confidence_band == "medium" else "created",
        "updated": "updated",
        "cancelled": "cancelled",
        "skipped_recurring": "recurring — skipped",
    }.get(action, action)

    start_str = start_dt.strftime("%b %-d %-I:%M%p").lower() if start_dt else "unknown time"

    if action == "updated" and original_title and original_title != title:
        event_line = f"*{original_title}* → *{title}* | {start_str}"
    elif action == "cancelled":
        event_line = f"*{title}*"
    else:
        event_line = f"*{title}* | {start_str} | `{category}` | `{source}`"

    lines = [f"{action_icon} [{action_label}] {event_line}"]

    if suggested_attendees:
        attendee_parts = []
        for a in suggested_attendees[:5]:
            name = a.get("name", "")
            email = a.get("email")
            if email:
                attendee_parts.append(f"{name} <{email}>" if name else email)
            elif name:
                attendee_parts.append(name)
        if attendee_parts:
            lines.append(f"  :busts_in_silhouette: Suggested invitees: {', '.join(attendee_parts)}")

    if conflicts:
        conflict_str = ", ".join(f"'{c}'" for c in conflicts[:3])
        lines.append(f"  :warning: Conflict: {conflict_str}")

    text = "\n".join(lines)

    try:
        client = _client()
        result = client.chat_postMessage(
            channel=config.SLACK_NOTIFY_CHANNEL,
            thread_ts=thread_ts,
            text=text,
        )
        return bool(result.get("ok"))
    except Exception as exc:
        logger.warning("slack notifier: post_event_action failed: %s", exc)
        return False


def post_event_batch(
    thread_ts: str,
    actions: list[dict],
) -> bool:
    """
    Post all event actions from a run as a single batched message.

    Each item in actions is a dict with keys:
      action, title, start_dt, source, category (opt), confidence_band (opt),
      suggested_attendees (opt), conflicts (opt), original_title (opt)
    """
    if not config.SLACK_BOT_TOKEN or not thread_ts or not actions:
        return False

    lines: list[str] = []
    reported_conflicts: set[str] = set()  # suppress repeats across the batch
    for a in actions:
        action = a["action"]
        title = a["title"]
        start_dt: datetime | None = a.get("start_dt")
        source = a.get("source", "")
        category = a.get("category", "other")
        confidence_band = a.get("confidence_band", "high")
        suggested_attendees: list[dict] | None = a.get("suggested_attendees")
        conflicts: list[str] | None = a.get("conflicts")
        original_title: str | None = a.get("original_title")

        action_icon = {
            "created": ":calendar:",
            "updated": ":pencil2:",
            "cancelled": ":wastebasket:",
            "skipped_recurring": ":repeat:",
        }.get(action, ":calendar:")

        action_label = {
            "created": "created?" if confidence_band == "medium" else "created",
            "updated": "updated",
            "cancelled": "cancelled",
            "skipped_recurring": "recurring — skipped",
        }.get(action, action)

        start_str = start_dt.strftime("%b %-d %-I:%M%p").lower() if start_dt else "unknown time"

        if action == "updated" and original_title and original_title != title:
            event_line = f"*{original_title}* → *{title}* | {start_str}"
        elif action == "cancelled":
            event_line = f"*{title}*"
        else:
            event_line = f"*{title}* | {start_str} | `{category}` | `{source}`"

        line = f"{action_icon} [{action_label}] {event_line}"

        if suggested_attendees:
            attendee_parts = []
            for att in suggested_attendees[:5]:
                name = att.get("name", "")
                email = att.get("email")
                # Skip calendar subscription names (not real people)
                if "calendar" in name.lower():
                    continue
                if email:
                    attendee_parts.append(f"{name} <{email}>" if name else email)
                elif name:
                    attendee_parts.append(name)
            if attendee_parts:
                line += f"\n  :busts_in_silhouette: Suggested invitees: {', '.join(attendee_parts)}"

        if conflicts:
            # Only surface each conflicting event once per batch
            new_conflicts = [c for c in conflicts if c not in reported_conflicts]
            if new_conflicts:
                conflict_str = ", ".join(f"'{c}'" for c in new_conflicts[:3])
                line += f"\n  :warning: Conflict: {conflict_str}"
                reported_conflicts.update(new_conflicts)

        lines.append(line)

    text = "\n".join(lines)
    try:
        client = _client()
        result = client.chat_postMessage(
            channel=config.SLACK_NOTIFY_CHANNEL,
            thread_ts=thread_ts,
            text=text,
        )
        return bool(result.get("ok"))
    except Exception as exc:
        logger.warning("slack notifier: post_event_batch failed: %s", exc)
        return False


def post_todo_action(
    thread_ts: str,
    title: str,
    source: str,
    context: str | None,
    due_date: str | None,
    priority: str = "normal",
) -> bool:
    """Post a todo item creation notification as a reply to the day thread."""
    if not config.SLACK_BOT_TOKEN or not thread_ts:
        return False

    priority_icon = {
        "urgent": ":red_circle:",
        "high": ":large_orange_circle:",
    }.get(priority, ":white_circle:")

    lines = [f"{priority_icon} [todo] *{title}*"]
    if context:
        lines.append(f"  :speech_balloon: {context}")
    if due_date:
        lines.append(f"  :calendar: Due: {due_date}")
    lines.append(f"  `{source}`")

    text = "\n".join(lines)

    try:
        client = _client()
        result = client.chat_postMessage(
            channel=config.SLACK_NOTIFY_CHANNEL,
            thread_ts=thread_ts,
            text=text,
        )
        return bool(result.get("ok"))
    except Exception as exc:
        logger.warning("slack notifier: post_todo_action failed: %s", exc)
        return False


def post_run_summary(
    thread_ts: str,
    created: int,
    updated: int,
    cancelled: int,
    skipped_low_confidence: int,
    skipped_recurring: int,
    skipped_duplicate: int,
    todos_created: int = 0,
) -> bool:
    """
    Post a run summary as a reply to the day thread.
    Only posts if at least one action occurred — no noise from empty runs.
    """
    if not config.SLACK_BOT_TOKEN or not thread_ts:
        return False

    total_actions = created + updated + cancelled
    if (total_actions == 0 and skipped_low_confidence == 0
            and skipped_recurring == 0 and todos_created == 0):
        return True  # nothing to report

    parts = []
    if created:
        parts.append(f"{created} created")
    if updated:
        parts.append(f"{updated} updated")
    if cancelled:
        parts.append(f"{cancelled} cancelled")
    if todos_created:
        parts.append(f"{todos_created} todo(s) added")
    if skipped_low_confidence:
        parts.append(f"{skipped_low_confidence} skipped (low confidence)")
    if skipped_recurring:
        parts.append(f"{skipped_recurring} recurring (skipped)")
    if skipped_duplicate:
        parts.append(f"{skipped_duplicate} duplicate (skipped)")

    if not parts:
        return True

    summary = "Run complete: " + ", ".join(parts)

    try:
        client = _client()
        result = client.chat_postMessage(
            channel=config.SLACK_NOTIFY_CHANNEL,
            thread_ts=thread_ts,
            text=f":white_check_mark: {summary}",
        )
        return bool(result.get("ok"))
    except Exception as exc:
        logger.warning("slack notifier: post_run_summary failed: %s", exc)
        return False


def post_to_thread(thread_ts: str, text: str) -> bool:
    """Generic helper: post any text as a reply to the day thread."""
    if not config.SLACK_BOT_TOKEN or not thread_ts:
        return False
    try:
        client = _client()
        result = client.chat_postMessage(
            channel=config.SLACK_NOTIFY_CHANNEL,
            thread_ts=thread_ts,
            text=text,
        )
        return bool(result.get("ok"))
    except Exception as exc:
        logger.warning("slack notifier: post_to_thread failed: %s", exc)
        return False
