"""Phase 21 — iPhone-driven recipe intake (huey wrapper).

The actual implementation lives in `meal_planner.runner.process_iphone_intake_sync`
so the Streamlit Capture tab can call it directly without transitively
importing huey (see memory `feedback_streamlit_in_process_huey.md`).

This file just registers the kind so the consumer can still execute it
when something enqueues via POST /jobs.
"""
from __future__ import annotations

from jobs import huey_fast as huey
from meal_planner.runner import iphone_intake_dir, process_iphone_intake_sync

# Re-export so existing callers of `from jobs.kinds.meal_planner_iphone_intake
# import iphone_intake_dir` keep working.
__all__ = ["iphone_intake_dir", "meal_planner_iphone_intake"]


@huey.task()
def meal_planner_iphone_intake(sha: str, intent: str, servings: int = 4) -> dict:
    return process_iphone_intake_sync(sha, intent, servings)
