"""
Phase 12.5 — event_aggregator_fetch kind sanity checks.

Mirrors the shape of test_migrations_registered: the cron-style decorators,
@baseline metric, @migrates_from label, and subprocess invocation pattern
all need to be in place before the cutover ritual on the mini.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from jobs.kinds import event_aggregator_fetch as mod
from jobs.lib import get_baseline, get_plist_label, get_requires


def test_baseline_metric_points_at_run_dir_touchfile():
    bl = get_baseline(mod.event_aggregator_fetch)
    assert bl is not None
    assert bl.metric == "file-mtime:event-aggregator/run/event-aggregator-fetch.last"
    assert bl.divergence_window == "12m"
    assert bl.cadence == "10m"
    # Sanity: parsed durations.
    assert bl.divergence_seconds == 12 * 60
    assert bl.cadence_seconds == 10 * 60


def test_migrates_from_label_matches_legacy_plist():
    """The verifier reads this label to find the .plist.disabled on rollback."""
    label = get_plist_label(mod.event_aggregator_fetch)
    assert label == "com.home-tools.event-aggregator.fetch"


def test_requires_includes_event_aggregator_dir():
    req = get_requires(mod.event_aggregator_fetch)
    assert req is not None
    assert "fs:event-aggregator" in req.items


def test_project_path_resolves_to_repo_event_aggregator_dir():
    """Sanity-check: the kind targets the repo's event-aggregator/ dir."""
    assert mod.PROJECT.name == "event-aggregator"
    # Repo layout: jobs/kinds/event_aggregator_fetch.py → repo / event-aggregator
    assert mod.PROJECT == Path(__file__).resolve().parents[2] / "event-aggregator"


def test_uses_project_venv_python(tmp_path):
    """Subprocess call must use the event-aggregator venv (not the consumer's
    sys.executable) — main.py imports gmail/slack/imessage modules that
    aren't in the jobs-consumer venv."""
    assert mod.VENV_PYTHON == mod.PROJECT / ".venv" / "bin" / "python3"


def test_invocation_shape_with_mocked_subprocess(monkeypatch):
    """The body should call `<venv-python> main.py fetch-only` from the
    project cwd. Capture the exact argv via monkeypatch."""
    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        return _Result()

    # Stub _load_ea_state — returns a state module with an empty text_queue.
    class _FakeState:
        _data = {"text_queue": []}
        def pop_text_job(self): return None

    class _FakeEaState:
        @staticmethod
        def locked():
            import contextlib
            return contextlib.nullcontext()
        @staticmethod
        def load():
            return _FakeState()
        @staticmethod
        def save(_state): pass

    monkeypatch.setattr(mod, "_load_ea_state", lambda: _FakeEaState())

    # @requires fires before the body — fs:event-aggregator must resolve.
    # In the tmp-HOME conftest the dir doesn't exist, so stub the validator.
    import jobs.lib
    monkeypatch.setattr(
        jobs.lib.RequiresSpec, "validate", lambda self: []
    )
    # Stub record_fire — writes to ~/Home-Tools/run/migrations.json which the
    # conftest tmp-HOME pre-creates, but we don't want side effects.
    monkeypatch.setattr(mod, "record_fire", lambda _name: None)
    # Stub event_aggregator_text to avoid scheduling real huey tasks.
    import jobs.kinds.event_aggregator_text as text_mod
    monkeypatch.setattr(mod, "event_aggregator_text", lambda _job: None)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)

    result = mod.event_aggregator_fetch.func()
    assert result["rc"] == 0
    assert "text_scheduled" in result
    assert captured["argv"] == [str(mod.VENV_PYTHON), "main.py", "fetch-only"]
    assert captured["cwd"] == str(mod.PROJECT)
    # Timeout < 600s (the 10-min cadence) so a stuck run can't pile up.
    assert captured["timeout"] is not None
    assert captured["timeout"] < 600


def test_text_queue_drain_schedules_tasks(monkeypatch):
    """After subprocess, event_aggregator_fetch must pop all text_queue items
    and schedule event_aggregator_text tasks for each."""
    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    jobs_to_drain = [
        {"source": "gmail", "id": "m1", "body_text": "hi", "metadata": {}, "timestamp": "2026-05-03T00:00:00Z"},
        {"source": "slack", "id": "m2", "body_text": "meeting", "metadata": {}, "timestamp": "2026-05-03T01:00:00Z"},
    ]

    class _FakeState:
        def __init__(self):
            self._queue = list(jobs_to_drain)
        def pop_text_job(self):
            return self._queue.pop(0) if self._queue else None

    class _FakeEaState:
        _state = _FakeState()
        @staticmethod
        def locked():
            import contextlib
            return contextlib.nullcontext()
        @classmethod
        def load(cls):
            return cls._state
        @staticmethod
        def save(_state): pass

    monkeypatch.setattr(mod, "_load_ea_state", lambda: _FakeEaState())
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: _Result())
    monkeypatch.setattr(mod, "record_fire", lambda _name: None)

    scheduled = []
    monkeypatch.setattr(mod, "event_aggregator_text", lambda job: scheduled.append(job))

    import jobs.lib
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])

    result = mod.event_aggregator_fetch.func()
    assert result["text_scheduled"] == 2
    assert len(scheduled) == 2
    assert scheduled[0]["id"] == "m1"
    assert scheduled[1]["id"] == "m2"
