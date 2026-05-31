"""event_aggregator_decide kind — argv construction + result shape.

No real venv / GCal: subprocess.run is mocked, @requires is stubbed."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jobs.kinds import event_aggregator_decide as mod
from jobs.lib import get_requires


@pytest.fixture(autouse=True)
def _stub_requires(monkeypatch):
    import jobs.lib
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])


def _capture(monkeypatch, returncode=0, stdout="ok", stderr=""):
    captured = {}

    class _Result:
        pass

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        r = _Result()
        r.returncode, r.stdout, r.stderr = returncode, stdout, stderr
        return r

    monkeypatch.setattr(mod.subprocess, "run", _fake_run)
    return captured


# ── structural ─────────────────────────────────────────────────────────────

def test_requires_event_aggregator_fs():
    req = get_requires(mod.event_aggregator_decide)
    assert req is not None and "fs:event-aggregator" in req.items


def test_uses_project_venv_and_path():
    assert mod.PROJECT == Path(__file__).resolve().parents[2] / "event-aggregator"
    assert mod.VENV_PYTHON == mod.PROJECT / ".venv" / "bin" / "python3"


# ── _norm ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("val,expected", [
    ([1, 2, 3], "1,2,3"),
    ("all", "all"),
    ("1,2", "1,2"),
    (5, "5"),
    ("", ""),
    (None, ""),
])
def test_norm(val, expected):
    assert mod._norm(val) == expected


# ── decide (approve/reject) ──────────────────────────────────────────────────

def test_decide_approve_and_reject_argv(monkeypatch):
    captured = _capture(monkeypatch)
    out = mod.event_aggregator_decide.func(approve=[12, 14], reject=[9])
    assert captured["argv"] == [
        str(mod.VENV_PYTHON), "main.py", "decide", "--approve", "12,14", "--reject", "9",
    ]
    assert captured["cwd"] == str(mod.PROJECT)
    assert captured["timeout"] == mod._TIMEOUT
    assert out["rc"] == 0
    assert out["summary"] == "ok"


def test_decide_approve_only(monkeypatch):
    captured = _capture(monkeypatch)
    mod.event_aggregator_decide.func(approve="all")
    assert captured["argv"] == [str(mod.VENV_PYTHON), "main.py", "decide", "--approve", "all"]
    assert "--reject" not in captured["argv"]


def test_decide_reject_only(monkeypatch):
    captured = _capture(monkeypatch)
    mod.event_aggregator_decide.func(reject=[3])
    assert captured["argv"] == [str(mod.VENV_PYTHON), "main.py", "decide", "--reject", "3"]
    assert "--approve" not in captured["argv"]


def test_nothing_to_decide_skips_subprocess(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    out = mod.event_aggregator_decide.func()
    assert out["rc"] == 1
    assert "nothing" in out["error"]
    assert called["n"] == 0


@pytest.mark.parametrize("rc", [0, 1, 2])
def test_decide_returns_rc(monkeypatch, rc):
    _capture(monkeypatch, returncode=rc)
    out = mod.event_aggregator_decide.func(approve=[1])
    assert out["rc"] == rc


# ── undo ─────────────────────────────────────────────────────────────────────

def test_undo_argv(monkeypatch):
    captured = _capture(monkeypatch)
    out = mod.event_aggregator_decide.func(undo_gcal_id="abc123")
    assert captured["argv"] == [str(mod.VENV_PYTHON), "main.py", "undo", "--gcal-id", "abc123"]
    assert out["rc"] == 0


def test_undo_takes_precedence_over_decide(monkeypatch):
    captured = _capture(monkeypatch)
    mod.event_aggregator_decide.func(approve=[1], undo_gcal_id="g1")
    assert "undo" in captured["argv"]
    assert "decide" not in captured["argv"]


# ── timeout ──────────────────────────────────────────────────────────────────

def test_timeout_returns_negative_rc(monkeypatch):
    def _boom(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=mod._TIMEOUT)

    monkeypatch.setattr(mod.subprocess, "run", _boom)
    out = mod.event_aggregator_decide.func(approve=[1])
    assert out["rc"] == -1
    assert "timeout" in out["error"]
