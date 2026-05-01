"""
Todoist adapter — creates a task. Reuses event-aggregator's todoist writer.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def create_task(output_config: dict, payload: dict) -> dict:
    """Create a Todoist task.

    output_config:
        target: "todoist"
        project_id: str | None  (None = inbox)
    payload:
        title, due_date, priority, source, source_id, ... (CandidateTodo fields)
    """
    token = os.environ.get("TODOIST_API_TOKEN")
    if not token:
        raise RuntimeError("TODOIST_API_TOKEN not set in environment")

    repo_root = Path(__file__).resolve().parents[2]
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
        token, project_id=output_config.get("project_id"), todo=todo, dry_run=False,
    )
    return {"created": bool(ok)}
