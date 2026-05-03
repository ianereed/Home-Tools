"""Phase 12.7 — drains state.ocr_queue into huey vision tasks; manages swap-decision UX.

Runs every minute. Responsibilities:
1. Drain state.json ocr_queue → schedule event_aggregator_vision tasks
2. If both text and vision tasks are pending: post/refresh the Slack swap-decision
3. Expire stale swap decisions (auto-resolve to "wait" after timeout)
4. Handle "interrupt" decision: log it (queue reordering not supported in SqliteHuey,
   so interrupt is informational — vision tasks run when text drains naturally)
"""
from __future__ import annotations

import importlib.util
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from huey import crontab

from jobs import huey
from jobs.kinds.event_aggregator_vision import event_aggregator_vision

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parents[2] / "event-aggregator"
_SWAP_DECISION_TIMEOUT_MIN = 5


def _load_ea_state():
    """Load event-aggregator state module via importlib to avoid venv pollution."""
    spec = importlib.util.spec_from_file_location("_ea_state_tmp", PROJECT / "state.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pending_task_count_by_name(task_name: str) -> int:
    """Count pending huey tasks with the given (short) function name."""
    try:
        return sum(1 for t in huey.pending() if getattr(t, "name", "").endswith(task_name))
    except Exception:
        return 0


@huey.periodic_task(crontab(minute="*"))
def event_aggregator_decision_poller() -> dict:
    """Drain ocr_queue + manage swap-decision UX."""
    ea_state = _load_ea_state()

    scheduled_vision = 0
    interrupt_found = False
    wait_found = False

    with ea_state.locked():
        state = ea_state.load()

        # 1. Expire stale swap decisions.
        bucket = state._data.get("swap_decisions", {})
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=_SWAP_DECISION_TIMEOUT_MIN)
        for info in list(bucket.values()):
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
                logger.info("decision-poller: swap decision auto-resolved to 'wait' (timeout)")

        # 2. Check for interrupt decisions.
        for info in bucket.values():
            if info.get("decision") == "interrupt":
                interrupt_found = True
                info["decision"] = "consumed"
                logger.info("decision-poller: consumed 'interrupt' decision (FIFO queue; vision runs after text drains)")
            elif info.get("decision") == "wait":
                wait_found = True

        # 3. Drain ocr_queue into huey vision tasks.
        while True:
            job = state.pop_ocr_job()
            if job is None:
                break
            event_aggregator_vision(job)
            scheduled_vision += 1
            logger.info("decision-poller: scheduled vision task for %s", job.get("file_path", "?"))

        ea_state.save(state)

    # 4. Post swap decision if both text and vision tasks are pending in huey.
    if scheduled_vision > 0:
        text_pending = _pending_task_count_by_name("event_aggregator_text")
        vision_pending = _pending_task_count_by_name("event_aggregator_vision")
        if text_pending > 0 and vision_pending > 0:
            _post_swap_decision_if_needed(ea_state, text_pending, vision_pending)

    return {
        "scheduled_vision": scheduled_vision,
        "interrupt_consumed": interrupt_found,
        "wait_found": wait_found,
    }


def _post_swap_decision_if_needed(ea_state, text_pending: int, vision_pending: int) -> None:
    """Post a Slack swap-decision message if none is already pending."""
    try:
        with ea_state.locked():
            state = ea_state.load()
            bucket = state._data.get("swap_decisions", {})
            if any(info.get("decision") == "pending" for info in bucket.values()):
                return  # already have a pending decision
            decision_id = state.add_swap_decision("(huey queue)", text_pending)
            ea_state.save(state)

        # Trigger a dashboard render so the buttons show up.
        import sys
        sys.path.insert(0, str(PROJECT))
        import tz_utils
        from notifiers import slack_notifier
        with ea_state.locked():
            state2 = ea_state.load()
        today = tz_utils.today_user_str()
        all_items = state2.get_all_proposal_items_for_dashboard(today)
        slack_notifier.post_or_update_dashboard(all_items, state2)
        logger.info(
            "decision-poller: posted swap decision %s (text_pending=%d, vision_pending=%d)",
            decision_id, text_pending, vision_pending,
        )
    except Exception as exc:
        logger.warning("decision-poller: swap decision post failed: %s", exc)
