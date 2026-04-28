"""
Slack notifier — posts to ian-event-aggregator channel.

Proposal mode: one persistent Block Kit "dashboard" message per day, updated
in-place as proposals arrive and as the user approves/rejects via buttons.

Auto mode: still uses daily thread (post_event_batch, post_run_summary).
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
        summary += f"\n:clipboard: {pending_proposals} proposal{'s' if pending_proposals != 1 else ''} awaiting approval — tap Approve in the dashboard above"

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


def _build_swap_decision_blocks(decisions: dict) -> list[dict]:
    """Render pending swap decisions (OCR job waiting on text queue) with
    [Wait]/[Interrupt] buttons. Decisions is a dict {decision_id: info}."""
    out: list[dict] = []
    for decision_id, info in decisions.items():
        if info.get("decision") != "pending":
            continue
        ocr_path = info.get("ocr_path", "(unknown)")
        depth = info.get("text_queue_depth_at_request", 0)
        # Approximate ETA: 45s/job is a reasonable guess for qwen3:14b @ 16k.
        eta_min = max(1, round(depth * 45 / 60))
        from pathlib import Path
        name = Path(ocr_path).name or ocr_path
        out.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":arrows_counterclockwise: *OCR job ready: `{name}`*\n"
                    f"Text queue: {depth} message(s), ~{eta_min} min."
                ),
            },
        })
        out.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Reply `swap wait` · `swap interrupt`"}],
        })
        out.append({"type": "divider"})
    return out


def build_dashboard_blocks(
    items: list[dict],
    today_str: str,
    ollama_health: dict | None = None,
    recurring_notices: list[dict] | None = None,
    worker_status: dict | None = None,
    swap_decisions: dict | None = None,
) -> list[dict]:
    """
    Build Slack Block Kit blocks for the live proposal dashboard.

    items: proposal item dicts (any status — pending, approved, rejected, expired)
    today_str: YYYY-MM-DD date string for the header
    ollama_health: optional dict from `state.ollama_health()`. When `down_since`
                   is set, an Errors block is added near the top of the dashboard.
    recurring_notices: optional list from `state.recurring_notices()`. Rendered
                       as a low-key "Notices" block — recurring events the LLM
                       saw but the tool intentionally won't auto-create.
    """
    blocks: list[dict] = []

    # Header
    try:
        d = datetime.strptime(today_str, "%Y-%m-%d")
        day_display = d.strftime("%a %b %-d")
    except Exception:
        day_display = today_str
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"Event proposals · {day_display}", "emoji": True},
    })

    pending = [i for i in items if i["status"] == "pending"]
    actioned = [i for i in items if i["status"] in ("approved", "rejected", "expired")]

    # ── Swap decisions (rendered above proposals — they're time-sensitive) ──
    if swap_decisions:
        blocks.extend(_build_swap_decision_blocks(swap_decisions))

    if not pending and not actioned and not (swap_decisions and any(
        d.get("decision") == "pending" for d in swap_decisions.values()
    )):
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_No proposals yet today._"},
        })

    # ── Decisions (pending) — top priority ───────────────────────────────────
    # Cap visible items to keep the message under Slack's block limits and
    # surface the "what needs attention" content first. If too many pending,
    # show oldest-first up to MAX_PENDING_DISPLAY and append a count line.
    MAX_PENDING_DISPLAY = 25
    sorted_pending = sorted(pending, key=lambda x: x.get("num", 0))
    for item in sorted_pending[:MAX_PENDING_DISPLAY]:
        blocks.extend(_build_pending_blocks(item))
    if len(sorted_pending) > MAX_PENDING_DISPLAY:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"_…and {len(sorted_pending) - MAX_PENDING_DISPLAY} more pending — use `cli status --pending` for the full list._",
            },
        })
        blocks.append({"type": "divider"})

    # ── Notices (low-key recurring-event hints) ──────────────────────────────
    if recurring_notices:
        notice_lines = [":arrows_counterclockwise: *Possibly recurring (handle manually if needed):*"]
        for n in recurring_notices[:5]:
            hint = f" — _{n['recurrence_hint']}_" if n.get("recurrence_hint") else ""
            notice_lines.append(f"  • {n.get('title', '(untitled)')} _(via {n.get('source', '')})_{hint}")
        if len(recurring_notices) > 5:
            notice_lines.append(f"  …and {len(recurring_notices) - 5} more")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(notice_lines)},
        })
        blocks.append({"type": "divider"})

    # ── Today's actions (collapsed) ──────────────────────────────────────────
    # Roll up by status to a single summary line when more than 3, else show
    # them individually. Keeps the dashboard from growing unbounded as the
    # day fills up with approves/rejects.
    if actioned:
        sorted_actioned = sorted(actioned, key=lambda x: x.get("num", 0))
        if len(sorted_actioned) <= 3:
            for item in sorted_actioned:
                blocks.append(_build_actioned_block(item))
        else:
            counts: dict = {"approved": 0, "rejected": 0, "expired": 0}
            for it in sorted_actioned:
                counts[it.get("status", "")] = counts.get(it.get("status", ""), 0) + 1
            parts = []
            if counts["approved"]:
                parts.append(f":white_check_mark: {counts['approved']} added")
            if counts["rejected"]:
                parts.append(f":x: {counts['rejected']} skipped")
            if counts["expired"]:
                parts.append(f":hourglass_flowing_sand: {counts['expired']} expired")
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "_Today: " + " · ".join(parts) + "_"}],
            })

    # ── Errors (Ollama down) — surfaced near the bottom but still prominent ─
    if ollama_health and ollama_health.get("down_since"):
        down_since_local = ollama_health["down_since"]
        try:
            from zoneinfo import ZoneInfo
            ds = datetime.fromisoformat(down_since_local)
            ds_local = ds.astimezone(ZoneInfo(config.USER_TIMEZONE))
            down_since_local = ds_local.strftime("%-I:%M%p").lower()
        except Exception:
            pass
        skipped = ollama_health.get("skipped_count", 0)
        skip_part = f" · {skipped} message(s) skipped" if skipped else ""
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":rotating_light: *Ollama unreachable* (since {down_since_local})"
                    f"{skip_part}\nExtraction is paused until it comes back."
                ),
            },
        })

    # Footer
    try:
        from zoneinfo import ZoneInfo
        now_local = datetime.now(tz=ZoneInfo(config.USER_TIMEZONE))
    except Exception:
        now_local = datetime.now()
    updated_str = now_local.strftime("%-I:%M%p").lower()

    footer_parts = [f"{len(pending)} pending", f"last run {updated_str}"]
    if worker_status:
        text_q = worker_status.get("text_queue", 0)
        ocr_q = worker_status.get("ocr_queue", 0)
        if text_q or ocr_q:
            queue_str = f"queue: {text_q} text"
            if ocr_q:
                queue_str += f" · {ocr_q} ocr"
            footer_parts.append(queue_str)
        job = worker_status.get("job_in_flight")
        if job:
            kind = job.get("kind", "")
            label = job.get("source", job.get("file", "")) if kind == "text" else job.get("file", "")
            started = job.get("started_at", "")
            age_str = ""
            if started:
                try:
                    from datetime import timezone as _tz
                    delta = datetime.now(tz=_tz.utc) - datetime.fromisoformat(started)
                    mins = int(delta.total_seconds() // 60)
                    age_str = f" {mins}m" if mins else ""
                except Exception:
                    pass
            footer_parts.append(f"working: {kind} {label}{age_str}")
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "_" + " · ".join(footer_parts) + "_"}],
    })

    return blocks


def _build_pending_blocks(item: dict) -> list[dict]:
    """Block Kit blocks for a single pending proposal item."""
    blocks: list[dict] = []
    num = item["num"]
    title = item["title"]
    confidence_band = item.get("confidence_band", "high")
    category = item.get("category", "other")
    source = item.get("source", "")
    is_update = item.get("is_update", False)
    is_cancellation = item.get("is_cancellation", False)
    kind = item.get("kind", "event")

    start_str = "unknown time"
    try:
        start_dt = datetime.fromisoformat(item["start_dt"])
        start_str = start_dt.strftime("%a %b %-d · %-I:%M%p").lower()
    except (KeyError, ValueError, TypeError):
        pass

    if kind == "todo":
        priority = item.get("priority", "normal")
        priority_icon = {
            "urgent": ":rotating_light:",
            "high": ":exclamation:",
            "normal": ":memo:",
            "low": ":small_blue_diamond:",
        }.get(priority, ":memo:")
        due = item.get("due_date")
        due_part = f" · due {due}" if due else ""
        source_display = f"<{item['source_url']}|{source}>" if item.get("source_url") else source
        main_text = (
            f"{priority_icon} *{title}*{due_part}\n"
            f"_{item.get('context') or 'no context'}_\n"
            f"from {source_display}"
        )
    elif kind == "fuzzy_event":
        # No specific date determinable — ask user for one (or skip).
        description = item.get("event_description", title)
        source_display = f"<{item['source_url']}|{source}>" if item.get("source_url") else source
        main_text = (
            f":thinking_face: *{title}*\n"
            f"_{description}_\n"
            f"Date/time unknown · from {source_display}\n"
            "Reply to this message with a date/time, or use "
            "`cli add-event --text \"...\"` for full control."
        )
    elif kind == "merge":
        # Additive merge proposal — patch a primary-calendar event with new info.
        matched_title = item.get("matched_title", "(matched event)")
        additions = item.get("additions") or {}
        addition_keys = ", ".join(additions.keys()) or "no fields"
        source_display = f"<{item['source_url']}|{source}>" if item.get("source_url") else source
        main_text = (
            f":heavy_plus_sign: *Merge into '{matched_title}'*\n"
            f"Add: {addition_keys} · from {source_display}"
        )
    elif is_cancellation:
        main_text = f":wastebasket: *{title}* _(cancel)_"
    elif is_update:
        original = item.get("original_title_hint") or title
        main_text = f":pencil2: *{original}* → *{title}*\n{start_str} _(update)_"
    else:
        conf_note = "  _[?]_" if confidence_band == "medium" else ""
        source_display = f"<{item['source_url']}|{source}>" if item.get("source_url") else source
        main_text = f":calendar: *{title}*{conf_note}\n{start_str} · from {source_display}"

    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": main_text}})

    # For merge proposals, surface the actual fields being added so the
    # user can decide without clicking through to the email/source.
    if kind == "merge":
        diff_lines = []
        additions = item.get("additions") or {}
        if additions.get("location"):
            diff_lines.append(f":pushpin: location: _{additions['location']}_")
        if additions.get("attendees"):
            atts = additions["attendees"][:4]
            att_parts = []
            for a in atts:
                name = a.get("name", "")
                email = a.get("email", "")
                att_parts.append(f"{name} <{email}>" if name and email else name or email or "")
            att_str = ", ".join(p for p in att_parts if p)
            if att_str:
                diff_lines.append(f":busts_in_silhouette: attendees: _+{att_str}_")
        if diff_lines:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": p} for p in diff_lines],
            })

    context_parts: list[str] = []
    for c in (item.get("conflicts") or [])[:3]:
        context_parts.append(f":warning: Conflicts: _{c}_")
        break  # one conflict line is enough

    valid_att = [
        a for a in (item.get("suggested_attendees") or [])[:4]
        if "calendar" not in (a.get("name") or "").lower()
    ]
    if valid_att:
        att_parts = []
        for a in valid_att:
            name = a.get("name", "")
            email = a.get("email")
            att_parts.append(f"{name} <{email}>" if name and email else name or email or "")
        att_str = ", ".join(p for p in att_parts if p)
        if att_str:
            context_parts.append(f":busts_in_silhouette: {att_str}")

    if context_parts:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": p} for p in context_parts],
        })

    if kind == "fuzzy_event":
        primary_label = "Already handled"  # marks the fuzzy item as resolved without writing
    elif kind == "merge":
        primary_label = "Merge"
    elif kind == "todo":
        primary_label = "Add to Todoist"
    else:
        primary_label = "Add to calendar"
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"Reply `approve {num}` · `reject {num}`"}],
    })

    blocks.append({"type": "divider"})
    return blocks


def _build_actioned_block(item: dict) -> dict:
    """Compact context block for an approved/rejected/expired proposal."""
    title = item["title"]
    status = item["status"]

    start_str = ""
    try:
        start_dt = datetime.fromisoformat(item["start_dt"])
        start_str = f" · {start_dt.strftime('%b %-d %-I:%M%p').lower()}"
    except Exception:
        pass

    if status == "approved":
        return {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f":white_check_mark: *{title}*{start_str} _added_"},
        ]}
    elif status == "rejected":
        return {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f":x: ~{title}~{start_str} _dismissed_"},
        ]}
    else:  # expired
        return {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f":hourglass: ~{title}~{start_str} _expired_"},
        ]}


def post_or_update_dashboard(
    items: list[dict],
    state: "state_module.State",
    force_repost: bool = False,
) -> str | None:
    """
    Post or update the live proposal dashboard for today.

    Creates a new top-level channel message on first call for the day.
    On subsequent calls, edits the existing message in-place.
    Pass force_repost=True to always delete-and-repost (e.g. after an
    approve/reject so the dashboard stays at the bottom of the channel).
    Returns the dashboard message ts, or None on failure.
    """
    if not config.SLACK_BOT_TOKEN:
        return None

    import state as _state_mod
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    blocks = build_dashboard_blocks(
        items,
        today,
        ollama_health=state.ollama_health(),
        recurring_notices=state.recurring_notices(),
        worker_status=state.worker_status(),
        swap_decisions=state._data.get("swap_decisions") or {},
    )
    pending_count = sum(1 for i in items if i["status"] == "pending")
    fallback_text = f"Event proposals: {pending_count} pending"

    dashboard_ts = state.get_proposal_dashboard_ts(today)
    dashboard_channel = state.get_proposal_dashboard_channel(today) or config.SLACK_NOTIFY_CHANNEL
    repost_threshold = getattr(config, "DASHBOARD_REPOST_AFTER_N", 20)
    buried = state.dashboard_buried_count(today)
    should_repost = force_repost or (dashboard_ts is not None and buried >= repost_threshold)

    try:
        from slack_sdk.errors import SlackApiError
        client = _client()
        # Try chat.update first if we have a stored ts and aren't force-reposting.
        # On `channel_not_found` / `message_not_found` (user deleted the dashboard
        # message manually, or workspace state changed), fall through to a fresh
        # chat.postMessage instead of failing the whole call.
        if dashboard_ts and not should_repost:
            try:
                result = client.chat_update(
                    channel=dashboard_channel,
                    ts=dashboard_ts,
                    blocks=blocks,
                    text=fallback_text,
                )
                if result.get("ok"):
                    return dashboard_ts
                logger.warning(
                    "slack notifier: dashboard update failed (channel=%s, ts=%s): %s",
                    dashboard_channel, dashboard_ts, result.get("error"),
                )
                return None
            except SlackApiError as exc:
                err_code = (exc.response or {}).get("error", "")
                if err_code in {"channel_not_found", "message_not_found"}:
                    logger.info(
                        "dashboard: stored ts %s in %s is stale (%s) — posting fresh",
                        dashboard_ts, dashboard_channel, err_code,
                    )
                    # Fall through to the post-fresh branch by clearing the ts.
                    dashboard_ts = None
                else:
                    raise

        # Either no dashboard yet today, it's been buried beyond the threshold,
        # or the stored ts was stale — post fresh (and if we had one, try to
        # delete the old).
        old_ts = dashboard_ts if should_repost and dashboard_ts else None
        result = client.chat_postMessage(
            channel=config.SLACK_NOTIFY_CHANNEL,
            blocks=blocks,
            text=fallback_text,
        )
        if not result.get("ok"):
            logger.warning(
                "slack notifier: dashboard post failed (channel=%s): %s",
                config.SLACK_NOTIFY_CHANNEL, result.get("error"),
            )
            return None
        new_ts = result["ts"]
        new_channel = result.get("channel")
        state.set_proposal_dashboard_ts(today, new_ts, channel=new_channel)
        state.reset_dashboard_buried(today)
        if old_ts:
            try:
                client.chat_delete(channel=dashboard_channel, ts=old_ts)
                logger.info(
                    "dashboard: reposted (was buried by %d msgs); old ts %s deleted",
                    buried, old_ts,
                )
            except Exception as exc:
                logger.debug("dashboard: failed to delete old ts %s: %s", old_ts, exc)
        _state_mod.save(state)
        return new_ts
    except Exception as exc:
        logger.warning(
            "slack notifier: post_or_update_dashboard failed (channel=%s, ts=%s): %s",
            dashboard_channel, dashboard_ts, exc,
        )
        return None


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
