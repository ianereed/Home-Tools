"""Phase 14.10 — Send scaled grocery lines to Todoist (no consolidation).

For each selected recipe, scales ingredients to the requested serving count
and creates one Todoist task per ingredient. Ingredients are NOT merged across
recipes; duplicates appear as separate tasks with a (Recipe Name) suffix.

Phase 17 Chunk D: also creates one recipe-header task per recipe in the Meals
section, titled "<recipe> (<servings> servings)", with the meal-planner label.
Header counts toward items_sent / items_attempted.

Result-dict contract (forward-compatible with future Consolidate + Send phase):
  items_sent:          int   — tasks successfully created
  items_attempted:     int   — tasks attempted
  consolidate_failed:  str | None — None (reserved for future phase)
  consolidate_dropped: int   — 0 (reserved for future phase)
  error:               str | None — top-level error string, None on success
"""
from __future__ import annotations

import json
import logging
import os

from jobs import huey
from jobs.adapters import todoist as todoist_adapter
from meal_planner.queries import get_recipe
from meal_planner.scaling import scale_ingredients

logger = logging.getLogger(__name__)

MEALS_SECTION_NAME = "Meals"


def send_recipes_to_todoist_sync(recipe_scales: list[list]) -> dict:
    """Synchronous core of the Todoist push. Used by the @huey.task wrapper below
    AND by other jobs (Phase 21 iPhone intake's `shop_only` intent) that need to
    invoke the push within the same worker without re-enqueueing.

    Same contract as meal_planner_send_to_todoist (see module docstring).
    """
    sections: dict[str, str] = json.loads(os.environ["TODOIST_SECTIONS"])
    fallback_name: str = next(iter(sections))

    if MEALS_SECTION_NAME not in sections:
        raise RuntimeError(
            f"TODOIST_SECTIONS is missing '{MEALS_SECTION_NAME}' section. "
            "Add it to meal_planner/.env and kickstart the consumer."
        )
    meals_section_id = sections[MEALS_SECTION_NAME]

    project_id = os.environ.get("TODOIST_PROJECT_ID")
    sent = 0
    attempted = 0

    for rid, target_servings in recipe_scales:
        recipe = get_recipe(int(rid))
        scaled = scale_ingredients(recipe, int(target_servings))

        attempted += 1
        header_title = f"{recipe.title} ({int(target_servings)} servings)"
        header_result = todoist_adapter.create_task(
            output_config={
                "project_id": project_id,
                "section_id": meals_section_id,
                "labels": ["meal-planner"],
            },
            payload={
                "title": header_title,
                "source": "meal-planner",
                "source_id": f"recipes:{recipe.id}:header",
                "priority": "normal",
                "confidence": 1.0,
            },
        )
        if header_result.get("created"):
            sent += 1

        for ingredient in scaled:
            attempted += 1

            qty = ingredient.qty_per_serving  # already multiplied by target_servings
            if qty is not None:
                qty_str = f"{qty:.4g}"
                if ingredient.unit:
                    base = f"{qty_str} {ingredient.unit} {ingredient.name}"
                else:
                    base = f"{qty_str} {ingredient.name}"
            else:
                base = ingredient.name
            title = f"{base.strip()} ({recipe.title})"

            section_name = (
                ingredient.todoist_section
                if ingredient.todoist_section in sections
                else fallback_name
            )
            section_id = sections[section_name]

            result = todoist_adapter.create_task(
                output_config={
                    "project_id": project_id,
                    "section_id": section_id,
                    "labels": ["meal-planner"],
                },
                payload={
                    "title": title,
                    "source": "meal-planner",
                    "source_id": f"recipes:{recipe.id}",
                    "priority": "normal",
                    "confidence": 1.0,
                },
            )
            if result.get("created"):
                sent += 1

    logger.info(
        "meal_planner_send_to_todoist: sent %d/%d items", sent, attempted
    )
    return {
        "items_sent": sent,
        "items_attempted": attempted,
        "consolidate_failed": None,
        "consolidate_dropped": 0,
        "error": None,
    }


@huey.task()
def meal_planner_send_to_todoist(recipe_scales: list[list]) -> dict:
    """Send raw scaled grocery lines to Todoist.

    recipe_scales: list of [recipe_id, target_servings] pairs
    (JSON-serialised as lists, not tuples).

    Required env vars:
        TODOIST_SECTIONS   — JSON object mapping section name → section_id.
                             MUST include a "Meals" section (recipe-header tasks land there).

    Optional:
        TODOIST_PROJECT_ID — target Todoist project; defaults to inbox
    """
    return send_recipes_to_todoist_sync(recipe_scales)
