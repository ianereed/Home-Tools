"""
Digest builder and scheduler.

Daily digest  → changes (new/updated/deleted) in the next 14 days
Weekly digest → changes in the 14–365 day window

Both digests are posted as replies to the ian-event-aggregator day thread.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from analyzers.calendar_analyzer import CalendarAnalysis, CalendarEvent, Conflict
from notifiers import slack_notifier

if TYPE_CHECKING:
    import state as state_module

logger = logging.getLogger(__name__)

_SHORT_WINDOW_DAYS = 14
_LONG_WINDOW_DAYS = 365


def _conflict_fp(c: Conflict) -> str:
    """Stable fingerprint for a conflict pair, independent of ordering."""
    return "||".join(sorted([c.event_a.gcal_id, c.event_b.gcal_id]))


def send_daily_digest(
    analysis: CalendarAnalysis,
    new_events: list[CalendarEvent],
    updated_events: list[CalendarEvent],
    removed_events: list[CalendarEvent],
    state: "state_module.State",
    pending_proposals: int = 0,
) -> bool:
    """Send daily digest covering changes in the next 14 days."""
    now = datetime.now(tz=timezone.utc)
    cutoff = now + timedelta(days=_SHORT_WINDOW_DAYS)

    upcoming_new = [e for e in new_events if e.start_dt <= cutoff]
    upcoming_updated = [e for e in updated_events if e.start_dt <= cutoff]
    upcoming_removed = [e for e in removed_events if e.start_dt <= cutoff]
    near_conflicts = [
        c for c in analysis.conflicts
        if c.event_a.start_dt <= cutoff or c.event_b.start_dt <= cutoff
    ]
    # Only warn about conflict pairs not already reported
    new_conflicts = [c for c in near_conflicts if not state.is_conflict_warned(_conflict_fp(c))]

    if not (upcoming_new or upcoming_updated or upcoming_removed or new_conflicts or pending_proposals):
        logger.debug("daily digest: no changes in next 14 days — skipping")
        return True

    thread_ts = slack_notifier.get_or_create_day_thread(state)
    if not thread_ts:
        return False

    text = _build_digest_text(
        title=f":calendar: Daily Digest — Next 14 Days ({now.strftime('%b %d')})",
        new_events=upcoming_new,
        updated_events=upcoming_updated,
        removed_events=upcoming_removed,
        conflicts=new_conflicts,
        pending_proposals=pending_proposals,
    )
    ok = slack_notifier.post_to_thread(thread_ts, text)
    if ok and new_conflicts:
        state.mark_conflicts_warned([_conflict_fp(c) for c in new_conflicts])
    return ok


def send_weekly_digest(
    analysis: CalendarAnalysis,
    new_events: list[CalendarEvent],
    updated_events: list[CalendarEvent],
    state: "state_module.State",
) -> bool:
    """Send weekly digest covering changes in the 14–365 day window."""
    now = datetime.now(tz=timezone.utc)
    near_cutoff = now + timedelta(days=_SHORT_WINDOW_DAYS)
    far_cutoff = now + timedelta(days=_LONG_WINDOW_DAYS)

    far_new = [e for e in new_events if near_cutoff < e.start_dt <= far_cutoff]
    far_updated = [e for e in updated_events if near_cutoff < e.start_dt <= far_cutoff]
    far_conflicts = [
        c for c in analysis.conflicts
        if near_cutoff < c.event_a.start_dt <= far_cutoff
    ]
    # Only warn about conflict pairs not already reported
    new_far_conflicts = [c for c in far_conflicts if not state.is_conflict_warned(_conflict_fp(c))]

    if not (far_new or far_updated or new_far_conflicts):
        logger.debug("weekly digest: no changes beyond 14 days — skipping")
        return True

    thread_ts = slack_notifier.get_or_create_day_thread(state)
    if not thread_ts:
        return False

    text = _build_digest_text(
        title=f":telescope: Weekly Digest — 14 Days to 1 Year ({now.strftime('%b %d')})",
        new_events=far_new,
        updated_events=far_updated,
        removed_events=[],
        conflicts=new_far_conflicts,
    )
    ok = slack_notifier.post_to_thread(thread_ts, text)
    if ok and new_far_conflicts:
        state.mark_conflicts_warned([_conflict_fp(c) for c in new_far_conflicts])
    return ok


def _build_digest_text(
    title: str,
    new_events: list[CalendarEvent],
    updated_events: list[CalendarEvent],
    removed_events: list[CalendarEvent],
    conflicts: list[Conflict],
    pending_proposals: int = 0,
) -> str:
    lines = [f"*{title}*"]

    if pending_proposals:
        lines.append(
            f"\n:clipboard: *{pending_proposals} event proposal{'s' if pending_proposals != 1 else ''} "
            f"awaiting approval* — reply `approve` in the day thread to create all"
        )

    def _event_line(e: CalendarEvent, prefix: str = "") -> str:
        date_str = e.start_dt.strftime("%b %d %H:%M")
        source = ""
        if "via event-aggregator | source:" in (e.source_description or ""):
            try:
                source = e.source_description.split("source:")[1].strip().rstrip("]")
                source = f"  `{source}`"
            except IndexError:
                pass
        loc = f"  📍 {e.location}" if e.location else ""
        return f"{prefix}*{e.title}* — {date_str}{loc}{source}"

    if new_events:
        lines.append(f"\n:new: *New ({len(new_events)})*")
        lines.extend(_event_line(e, "• ") for e in new_events[:20])

    if updated_events:
        lines.append(f"\n:pencil2: *Updated ({len(updated_events)})*")
        lines.extend(_event_line(e, "• ") for e in updated_events[:10])

    if removed_events:
        lines.append(f"\n:wastebasket: *Removed ({len(removed_events)})*")
        lines.extend(_event_line(e, "• ") for e in removed_events[:10])

    if conflicts:
        lines.append("\n:rotating_light: *Scheduling Conflicts*")

        # Group overlaps by the anchor event (event_a always starts first / is the
        # longer event). This collapses "multi-day event spans N others" from N
        # identical-looking lines into one compact line.
        overlap_groups: dict[str, tuple] = {}
        travel_risks: list[Conflict] = []
        for c in conflicts:
            if c.conflict_type == "overlap":
                if c.event_a.gcal_id not in overlap_groups:
                    overlap_groups[c.event_a.gcal_id] = (c.event_a, [])
                overlap_groups[c.event_a.gcal_id][1].append(c.event_b)
            else:
                travel_risks.append(c)

        for anchor, others in overlap_groups.values():
            if len(others) == 1:
                lines.append(f":red_circle: *Overlap*: {anchor.title} / {others[0].title}")
            else:
                listed = ", ".join(o.title for o in others[:6])
                more = f" (+{len(others) - 6} more)" if len(others) > 6 else ""
                lines.append(f":red_circle: *Overlap*: *{anchor.title}* / {listed}{more}")

        for c in travel_risks[:5]:
            lines.append(
                f":warning: *Travel risk* ({c.gap_minutes:.0f} min gap): "
                f"{c.event_a.title} → {c.event_b.title}"
            )

    return "\n".join(lines)[:3000]
