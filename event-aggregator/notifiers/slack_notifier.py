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


def post_file_result(
    thread_ts: str,
    analysis: Any,
    nas_path: str | None,
    events_created: int,
    events_proposed: int = 0,
    auto_processed: bool = False,
    page_count: int = 1,
) -> bool:
    """Post a file intake result as a reply to the original upload message.

    Replies in the file upload's own thread (not the day thread).
    PHI-safe: uses summary only, not full extracted text.
    """
    if not config.SLACK_BOT_TOKEN or not thread_ts:
        return False

    from models import FileAnalysisResult
    assert isinstance(analysis, FileAnalysisResult)

    category_display = analysis.primary_category
    if analysis.subcategory:
        category_display += f" / {analysis.subcategory}"

    doc_label = f"{page_count}-page document" if page_count > 1 else "document"
    lines = [f":file_folder: *Filed {doc_label} to {category_display}*"]

    if auto_processed:
        lines.append(":hourglass: _Auto-processed after 12 hours — reply 'done' to process sooner next time_")

    if nas_path:
        lines.append(f"NAS: `{nas_path}`")
    else:
        lines.append(":hourglass_flowing_sand: Staged locally — will sync to NAS on next run")

    if events_proposed:
        lines.append(f":clipboard: {events_proposed} calendar event(s) proposed for approval — check day thread")
    elif events_created:
        lines.append(f":calendar: {events_created} calendar event(s) created")

    lines.append(f"_{analysis.summary}_")

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
        logger.warning("slack notifier: post_file_result failed: %s", exc)
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
    files_processed: int = 0,
    proposed: int = 0,
    pending_proposals: int = 0,
) -> bool:
    """
    Post a run summary as a reply to the day thread.
    Only posts if at least one action occurred — no noise from empty runs.
    """
    if not config.SLACK_BOT_TOKEN or not thread_ts:
        return False

    total_actions = created + updated + cancelled
    if (total_actions == 0 and skipped_low_confidence == 0
            and skipped_recurring == 0 and todos_created == 0
            and files_processed == 0 and proposed == 0):
        return True  # nothing to report

    parts = []
    if proposed:
        parts.append(f"{proposed} event{'s' if proposed != 1 else ''} proposed for approval")
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
    if files_processed:
        parts.append(f"{files_processed} file(s) filed")

    if not parts:
        return True

    summary = "Run complete: " + ", ".join(parts)
    if pending_proposals:
        summary += f"\n:clipboard: {pending_proposals} proposal{'s' if pending_proposals != 1 else ''} awaiting approval — reply `approve` to create all"

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


def post_proposals(thread_ts: str, items: list[dict]) -> str | None:
    """
    Post a numbered list of event proposals as a reply in the day thread.

    Each item is a proposal dict from state.pending_proposals[*].items[*].
    Returns the Slack message ts of the posted message (needed to check replies),
    or None on failure.
    """
    if not config.SLACK_BOT_TOKEN or not thread_ts or not items:
        return None

    lines = [f":clipboard: *{len(items)} event proposal{'s' if len(items) != 1 else ''}* — reply to approve/reject\n"]

    for item in items:
        num = item["num"]
        title = item["title"]
        confidence_band = item.get("confidence_band", "high")
        category = item.get("category", "other")
        source = item.get("source", "")
        is_update = item.get("is_update", False)
        is_cancellation = item.get("is_cancellation", False)
        conflicts = item.get("conflicts") or []
        attendees = item.get("suggested_attendees") or []
        source_url = item.get("source_url")

        # Parse start time for display
        start_str = "unknown time"
        try:
            start_dt = datetime.fromisoformat(item["start_dt"])
            start_str = start_dt.strftime("%b %-d %-I:%M%p").lower()
        except (KeyError, ValueError, TypeError):
            pass

        # Choose icon and label
        if is_cancellation:
            icon = ":wastebasket:"
            label = "[cancel]"
        elif is_update:
            icon = ":pencil2:"
            label = "[update]"
        else:
            icon = ":calendar:"
            label = ""

        title_display = f"[?] {title}" if confidence_band == "medium" else title

        if is_cancellation:
            event_line = f"{icon} `#{num}` {label} *{title_display}*"
        elif is_update:
            original = item.get("original_title_hint") or title
            event_line = f"{icon} `#{num}` {label} *{original}* → *{title_display}* | {start_str}"
        else:
            event_line = f"{icon} `#{num}` *{title_display}* | {start_str} | `{category}` | `{source}`"

        if source_url:
            event_line += f" | <{source_url}|source>"

        lines.append(event_line)

        # Attendees (skip "calendar" noise names)
        attendee_parts = []
        for a in attendees[:5]:
            name = a.get("name", "")
            email = a.get("email")
            if "calendar" in name.lower():
                continue
            if email:
                attendee_parts.append(f"{name} <{email}>" if name else email)
            elif name:
                attendee_parts.append(name)
        if attendee_parts:
            lines.append(f"    :busts_in_silhouette: {', '.join(attendee_parts)}")

        # Conflicts
        if conflicts:
            conflict_str = ", ".join(f"'{c}'" for c in conflicts[:3])
            lines.append(f"    :warning: Conflict: {conflict_str}")

    lines.append("")
    lines.append("Reply: `approve` (all) · `approve 1,3` · `reject 2` · `reject all`")

    text = "\n".join(lines)

    try:
        client = _client()
        result = client.chat_postMessage(
            channel=config.SLACK_NOTIFY_CHANNEL,
            thread_ts=thread_ts,
            text=text,
        )
        if result.get("ok"):
            return result["ts"]
        logger.warning("slack notifier: post_proposals failed: %s", result.get("error"))
        return None
    except Exception as exc:
        logger.warning("slack notifier: post_proposals error: %s", exc)
        return None


def check_proposal_replies(day_thread_ts: str, proposal_ts: str) -> dict:
    """
    Check the day thread for approval/rejection replies posted after proposal_ts.

    Parses replies that start with "approve" or "reject" (case-insensitive).
    Returns:
      {
        "approve_all": bool,
        "approve_nums": list[int],
        "reject_all": bool,
        "reject_nums": list[int],
      }
    """
    result = {"approve_all": False, "approve_nums": [], "reject_all": False, "reject_nums": []}

    if not config.SLACK_BOT_TOKEN:
        return result

    try:
        client = _client()
        replies = client.conversations_replies(
            channel=config.SLACK_NOTIFY_CHANNEL,
            ts=day_thread_ts,
            oldest=proposal_ts,
        )
        for msg in replies.get("messages", []):
            if msg.get("ts") == proposal_ts:
                continue  # skip the proposal message itself
            text = msg.get("text", "").strip().lower()
            if not text:
                continue

            if text.startswith("approve"):
                rest = text[len("approve"):].strip(" ,:")
                if not rest or rest in ("all", "everything"):
                    result["approve_all"] = True
                else:
                    nums = _parse_nums(rest)
                    result["approve_nums"].extend(nums)

            elif text.startswith("reject"):
                rest = text[len("reject"):].strip(" ,:")
                if not rest or rest in ("all", "everything"):
                    result["reject_all"] = True
                else:
                    nums = _parse_nums(rest)
                    result["reject_nums"].extend(nums)

    except Exception as exc:
        logger.warning("slack notifier: check_proposal_replies error: %s", exc)

    return result


def _parse_nums(text: str) -> list[int]:
    """Extract integers from a comma/space-separated string like '1,3,5' or '1 3 5'."""
    import re
    return [int(m) for m in re.findall(r'\d+', text)]


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
