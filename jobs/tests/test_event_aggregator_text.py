"""Phase 12.7 — event_aggregator_text kind sanity checks."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from jobs.kinds import event_aggregator_text as mod
from jobs.lib import get_baseline, get_plist_label, get_requires


def test_baseline_metric():
    bl = get_baseline(mod.event_aggregator_text)
    assert bl is not None
    assert bl.metric == "file-mtime:event-aggregator/run/event-aggregator-text-or-vision.last"
    assert bl.divergence_window == "2h"
    assert bl.cadence == "2h"


def test_migrates_from_worker_plist():
    label = get_plist_label(mod.event_aggregator_text)
    assert label == "com.home-tools.event-aggregator.worker"


def test_requires_includes_event_aggregator_dir():
    req = get_requires(mod.event_aggregator_text)
    assert req is not None
    assert "fs:event-aggregator" in req.items


def test_project_path():
    assert mod.PROJECT.name == "event-aggregator"
    assert mod.PROJECT == Path(__file__).resolve().parents[2] / "event-aggregator"


def test_subprocess_invocation(monkeypatch, tmp_path):
    """Body must call `cli.py run-text-job --job-json <json>` in the project dir."""
    import json
    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        return _Result()

    import jobs.lib
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])
    monkeypatch.setattr(jobs.lib._model_state, "_http_post", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "record_fire", lambda _name: None)
    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    # Redirect baseline touch to a tmp path so tests don't write to the repo.
    from pathlib import Path
    monkeypatch.setattr(mod, "TOUCH_FILE", Path(tmp_path) / "ea-tv.last")

    job = {"source": "gmail", "id": "m1", "body_text": "hi", "metadata": {}, "timestamp": "2026-05-03T00:00:00Z"}
    # Call the underlying function (bypass huey task wrapper).
    result = mod.event_aggregator_text.func(job)
    assert result["rc"] == 0
    assert result["source"] == "gmail"
    assert result["id"] == "m1"
    assert "cli.py" in captured["argv"][1]
    assert "run-text-job" in captured["argv"]
    assert "--job-json" in captured["argv"]
    assert json.loads(captured["argv"][captured["argv"].index("--job-json") + 1])["id"] == "m1"
    assert captured["cwd"] == str(mod.PROJECT)


def test_subprocess_failure_logged(monkeypatch, caplog):
    import logging
    import jobs.lib
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])
    monkeypatch.setattr(jobs.lib._model_state, "_http_post", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "record_fire", lambda _name: None)

    class _Fail:
        returncode = 1
        stdout = ""
        stderr = "something went wrong"

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: _Fail())

    job = {"source": "gmail", "id": "m2", "body_text": "x", "metadata": {}, "timestamp": "2026-05-03T00:00:00Z"}
    with caplog.at_level(logging.WARNING, logger="jobs.kinds.event_aggregator_text"):
        result = mod.event_aggregator_text.func(job)
    assert result["rc"] == 1
    assert any("rc=1" in r.message for r in caplog.records)
