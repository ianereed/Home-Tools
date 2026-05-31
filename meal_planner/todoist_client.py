"""Todoist task creation — Phase 21 v2 huey-free relocation.

The body is functionally identical to `jobs/adapters/todoist.py` but lives
in the `meal_planner` namespace so importing it doesn't trigger
`jobs/__init__.py` (which holds a WAL fd on `jobs.db` and silently drops
enqueues from other processes — see `feedback_streamlit_in_process_huey.md`).

Both the huey wrapper in `jobs/kinds/meal_planner_send_to_todoist.py` and
the Streamlit Capture tab call into this module via `meal_planner.runner`.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def create_task(output_config: dict, payload: dict) -> dict:
    """Create a Todoist task. Same contract as the original `jobs.adapters.todoist`.

    output_config:
        target: "todoist"
        project_id: str | None  (None = inbox)
        section_id: str | None  (None = no section; tasks land in the project inbox)
        labels: list[str] | None
    payload:
        title, due_date, priority, source, source_id, ... (CandidateTodo fields)
    """
    token = os.environ.get("TODOIST_API_TOKEN")
    if not token:
        raise RuntimeError("TODOIST_API_TOKEN not set in environment")

    # Pull the CandidateTodo + writer from event-aggregator's sibling tree
    # (same pattern as jobs/adapters/todoist.py — kept here so we don't have
    # to import that file and thereby trigger jobs/__init__.py).
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "event-aggregator"))
    try:
        from models import CandidateTodo
        from writers import todoist_writer
    finally:
        try:
            sys.path.remove(str(repo_root / "event-aggregator"))
        except ValueError:
            pass

    todo = CandidateTodo(
        title=payload.get("title", ""),
        source=payload.get("source", ""),
        source_id=payload.get("source_id", ""),
        source_url=payload.get("source_url"),
        confidence=payload.get("confidence", 0.5),
        context=payload.get("context"),
        due_date=payload.get("due_date"),
        priority=payload.get("priority", "normal"),
    )
    ok = todoist_writer.create_task(
        token,
        project_id=output_config.get("project_id"),
        todo=todo,
        dry_run=False,
        section_id=output_config.get("section_id"),
        labels=output_config.get("labels"),
    )
    return {"created": bool(ok)}
