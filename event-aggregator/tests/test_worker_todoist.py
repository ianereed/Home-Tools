"""Regression test for Tier 1.1 — worker → todoist_writer.create_task signature.

The kwarg shape on worker.py was left over from the pre-CandidateTodo API.
Every todo since the refactor was failing with TypeError. These tests use
inspect.signature.bind() to catch any future drift between caller and callee
without needing to run the full extraction pipeline (which would require
Ollama, Slack, GCal, and Todoist mocks).
"""
from __future__ import annotations

import inspect

from writers import todoist_writer


def test_create_task_accepts_canonical_kwargs():
    """worker._run_text_job calls create_task(token=, project_id=, todo=, dry_run=)."""
    sig = inspect.signature(todoist_writer.create_task)
    sig.bind(token="<sentinel>", project_id="<sentinel>", todo="<sentinel>", dry_run=False)


def test_get_or_create_project_accepts_canonical_args():
    """worker._run_text_job calls get_or_create_project(token, name, state) positionally."""
    sig = inspect.signature(todoist_writer.get_or_create_project)
    sig.bind("<token>", "<name>", "<state>")
