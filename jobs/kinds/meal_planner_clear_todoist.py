"""Phase 14.8 — Delete all Todoist tasks labeled "meal-planner".

Safety: LABEL is a module-level constant, not a parameter. Every list and
delete request is scoped to this label. A future contributor cannot widen the
scope by passing a different label — they would have to change this constant
and the comment that names it as the safety boundary.
"""
from __future__ import annotations

import logging
import os

import requests

from jobs import huey_fast as huey

logger = logging.getLogger(__name__)

# SAFETY BOUNDARY — this label scopes every list and delete in this module.
# Never pass this as a parameter. Changing it here changes which tasks are
# deleted; event-aggregator and finance-monitor tasks are untouched as long
# as this stays "meal-planner".
LABEL = "meal-planner"

_BASE_URL = "https://api.todoist.com/api/v1"


@huey.task()
def meal_planner_clear_todoist() -> dict:
    """Delete all Todoist tasks with label 'meal-planner'.

    Required env vars:
        TODOIST_API_TOKEN — loaded by run-consumer.sh from meal_planner/.env

    Returns:
        {"items_cleared": N, "error": str | None}
    """
    token = os.environ["TODOIST_API_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}

    task_ids = _list_labeled_tasks(headers)
    logger.info("meal_planner_clear_todoist: found %d tasks to delete", len(task_ids))

    cleared = 0
    failed_ids: list[str] = []

    for task_id in task_ids:
        resp = requests.delete(f"{_BASE_URL}/tasks/{task_id}", headers=headers, timeout=10)
        if resp.status_code in (200, 204):
            cleared += 1
        else:
            logger.warning(
                "meal_planner_clear_todoist: failed to delete task %s (HTTP %d)",
                task_id,
                resp.status_code,
            )
            failed_ids.append(task_id)

    error = f"{len(failed_ids)} task(s) failed to delete" if failed_ids else None
    logger.info(
        "meal_planner_clear_todoist: cleared=%d error=%s",
        cleared,
        error,
    )
    return {"items_cleared": cleared, "error": error}


def _list_labeled_tasks(headers: dict) -> list[str]:
    """Return all task IDs labeled LABEL, following next_cursor pagination."""
    task_ids: list[str] = []
    cursor: str | None = None

    while True:
        params: dict = {"label": LABEL}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(f"{_BASE_URL}/tasks", headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for task in data.get("results", []):
            task_ids.append(task["id"])

        cursor = data.get("next_cursor")
        if not cursor:
            break

    return task_ids
