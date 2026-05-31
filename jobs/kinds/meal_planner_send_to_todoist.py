"""Send raw scaled grocery lines to Todoist (huey wrapper).

Implementation lives in `meal_planner.runner.send_recipes_to_todoist_sync`
so it can be called from huey-free contexts (Streamlit Capture tab,
shop_only branch of the intake runner) without touching huey.
"""
from __future__ import annotations

from jobs import huey_fast as huey
from meal_planner.runner import send_recipes_to_todoist_sync

__all__ = ["meal_planner_send_to_todoist", "send_recipes_to_todoist_sync"]


@huey.task()
def meal_planner_send_to_todoist(recipe_scales: list[list]) -> dict:
    """Send raw scaled grocery lines to Todoist.

    recipe_scales: list of [recipe_id, target_servings] pairs
    (JSON-serialised as lists, not tuples).

    Required env vars:
        TODOIST_SECTIONS — JSON object mapping section name → section_id.
                           MUST include a "Meals" section.

    Optional:
        TODOIST_PROJECT_ID — target Todoist project; defaults to inbox.
    """
    return send_recipes_to_todoist_sync(recipe_scales)
