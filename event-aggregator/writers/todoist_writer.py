"""
Todoist writer — creates tasks in the "automated todo aggregation" project.

Uses the Todoist REST API v1 with a bearer token. No SDK required.
The target project is auto-created on first run if it doesn't exist; its ID
is cached in state.json to avoid repeated API lookups.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

import config

if TYPE_CHECKING:
    import state as state_module
    from models import CandidateTodo

logger = logging.getLogger(__name__)

_BASE = "https://api.todoist.com/api/v1"

# Todoist priority values: 4=urgent, 3=high, 2=normal, 1=low
_PRIORITY_MAP: dict[str, int] = {
    "urgent": 4,
    "high": 3,
    "normal": 2,
    "low": 1,
}


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_or_create_project(
    token: str,
    name: str,
    state: "state_module.State",
) -> str | None:
    """
    Find the target project by name, creating it if absent. Caches the ID in state.
    Returns the project_id string, or None on error.
    """
    cached = state.get_todoist_project_id()
    if cached:
        return cached

    try:
        resp = requests.get(f"{_BASE}/projects", headers=_headers(token), timeout=10)
        resp.raise_for_status()
        for project in resp.json().get("results", []):
            if project.get("name", "").lower() == name.lower():
                project_id = project["id"]
                state.set_todoist_project_id(project_id)
                logger.info("todoist: found existing project %r (id=%s)", name, project_id)
                return project_id

        # Project not found — create it
        resp = requests.post(
            f"{_BASE}/projects",
            json={"name": name},
            headers=_headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        project_id = resp.json()["id"]
        state.set_todoist_project_id(project_id)
        logger.info("todoist: created project %r (id=%s)", name, project_id)
        return project_id

    except Exception as exc:
        logger.warning("todoist: could not resolve project %r — %s", name, exc)
        return None


def create_task(
    token: str,
    project_id: str,
    todo: "CandidateTodo",
    dry_run: bool = False,
) -> bool:
    """
    Create a Todoist task from a CandidateTodo.

    The task description includes the context sentence plus source attribution
    so the provenance is always visible in Todoist.
    Returns True on success (or in dry-run mode).
    """
    if dry_run:
        logger.info(
            "DRY RUN — would create todo: %r (source=%s, priority=%s, due=%s)",
            todo.title, todo.source, todo.priority, todo.due_date,
        )
        return True

    description_parts: list[str] = []
    if todo.context:
        description_parts.append(todo.context)
    description_parts.append(f"Source: {todo.source}")
    if todo.source_url:
        description_parts.append(f"Link: {todo.source_url}")

    payload: dict = {
        "content": todo.title,
        "project_id": project_id,
        "description": "\n".join(description_parts),
        "priority": _PRIORITY_MAP.get(todo.priority, 2),
        "labels": ["event-aggregator"],
    }
    if todo.due_date:
        payload["due_date"] = todo.due_date

    try:
        resp = requests.post(
            f"{_BASE}/tasks",
            json=payload,
            headers=_headers(token),
            timeout=10,
        )
        resp.raise_for_status()
        task_id = resp.json().get("id", "?")
        logger.info(
            "todoist: created task %r (id=%s, source=%s)",
            todo.title, task_id, todo.source,
        )
        return True
    except Exception as exc:
        logger.warning("todoist: failed to create task %r — %s", todo.title, exc)
        return False
