"""
Tests for tier-2 CLI subcommands: config (mute/watch), undo-last, undo, changes.
"""
from __future__ import annotations

import contextlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import state as state_module


# ── undo (undo-last + undo --gcal-id) ─────────────────────────────────────────


@pytest.fixture
def undo_env(monkeypatch):
    """Wire cli's undo path to an in-memory State and stubbed GCal/event-log.

    load() returns one shared State so the find-then-remove sequence is coherent;
    delete_event and record_cancellation are captured."""
    state = state_module.State({"written_events": {
        "g_old": {"title": "Old Event", "start": "2026-05-01T10:00:00+00:00",
                  "created_at": "2026-05-01T09:00:00+00:00", "calendar_id": "weekend@x", "source": "gmail"},
        "g_new": {"title": "New Event", "start": "2026-05-30T10:00:00+00:00",
                  "created_at": "2026-05-30T09:00:00+00:00", "calendar_id": "weekend@x", "source": "slack"},
    }})
    monkeypatch.setattr(state_module, "load", lambda: state)
    monkeypatch.setattr(state_module, "save", lambda _s: None)
    monkeypatch.setattr(state_module, "locked", lambda: contextlib.nullcontext())

    deleted: list = []
    from writers import google_calendar as gcal
    monkeypatch.setattr(gcal, "delete_event", lambda cal, gid, dry_run=False: (deleted.append((cal, gid)) or True))

    logged: list = []
    from logs import event_log
    monkeypatch.setattr(event_log, "record_cancellation", lambda gid, title, src: logged.append((gid, title, src)))

    return {"state": state, "deleted": deleted, "logged": logged}


def test_undo_gcal_id_success(undo_env):
    from cli import _cmd_undo_gcal_id
    rc = _cmd_undo_gcal_id("g_old")
    assert rc == 0
    assert "g_old" not in undo_env["state"].get_written_events()
    assert undo_env["deleted"] == [("weekend@x", "g_old")]
    assert undo_env["logged"] == [("g_old", "Old Event", "gmail")]


def test_undo_gcal_id_not_found(undo_env):
    from cli import _cmd_undo_gcal_id
    rc = _cmd_undo_gcal_id("ghost")
    assert rc == 1
    assert undo_env["deleted"] == []  # no GCal call for an untracked id


def test_undo_gcal_id_delete_failure_keeps_state(undo_env, monkeypatch):
    from writers import google_calendar as gcal
    monkeypatch.setattr(gcal, "delete_event", lambda *a, **k: False)
    from cli import _cmd_undo_gcal_id
    rc = _cmd_undo_gcal_id("g_old")
    assert rc == 1
    assert "g_old" in undo_env["state"].get_written_events()  # NOT modified
    assert undo_env["logged"] == []


def test_undo_gcal_id_idempotent_double(undo_env):
    from cli import _cmd_undo_gcal_id
    assert _cmd_undo_gcal_id("g_old") == 0       # delete_event idempotent (404/410 → True)
    assert _cmd_undo_gcal_id("g_old") == 1       # already gone from state → not found


def test_undo_last_delegates_to_most_recent(undo_env):
    from cli import _cmd_undo_last
    rc = _cmd_undo_last()
    assert rc == 0
    # g_new has the newer created_at → it's the one undone
    assert undo_env["deleted"] == [("weekend@x", "g_new")]
    assert "g_new" not in undo_env["state"].get_written_events()


def test_undo_last_empty():
    with patch.object(state_module, "load", lambda: state_module.State({})):
        from cli import _cmd_undo_last
        assert _cmd_undo_last() == 0  # nothing written → no-op success


# ── state helpers (last_written_event / remove_written_event) ─────────────────


class TestWrittenEventHelpers:
    def test_last_written_event_empty(self):
        import state as state_module
        s = state_module.State({})
        assert s.last_written_event() is None

    def test_last_written_event_picks_most_recent(self):
        import state as state_module
        s = state_module.State({})
        # add_written_event uses _utcnow() for created_at — write directly to bypass
        # the internal timestamp so the test is deterministic.
        s._data["written_events"] = {
            "g1": {"title": "earlier", "start": "2026-04-20T09:00:00+00:00",
                   "fingerprint": "f1", "created_at": "2026-04-20T10:00:00+00:00",
                   "is_tentative": False},
            "g2": {"title": "later",   "start": "2026-04-21T09:00:00+00:00",
                   "fingerprint": "f2", "created_at": "2026-04-21T10:00:00+00:00",
                   "is_tentative": False},
        }
        result = s.last_written_event()
        assert result is not None
        gcal_id, info = result
        assert gcal_id == "g2"
        assert info["title"] == "later"

    def test_remove_written_event(self):
        import state as state_module
        s = state_module.State({})
        s._data["written_events"] = {"g1": {"title": "x"}}
        removed = s.remove_written_event("g1")
        assert removed == {"title": "x"}
        assert s.get_written_events() == {}
        # Removing a missing one returns None, doesn't raise
        assert s.remove_written_event("ghost") is None


# ── _parse_since ──────────────────────────────────────────────────────────────


class TestParseSince:
    def test_relative_days(self):
        from cli import _parse_since
        now = datetime.now(timezone.utc)
        result = _parse_since("1d")
        assert result is not None
        delta = now - result
        # Allow a 5s window for test latency
        assert abs(delta.total_seconds() - 86400) < 5

    def test_relative_hours(self):
        from cli import _parse_since
        now = datetime.now(timezone.utc)
        result = _parse_since("12h")
        assert abs((now - result).total_seconds() - 43200) < 5

    def test_relative_minutes(self):
        from cli import _parse_since
        now = datetime.now(timezone.utc)
        result = _parse_since("30m")
        assert abs((now - result).total_seconds() - 1800) < 5

    def test_iso_date(self):
        from cli import _parse_since
        result = _parse_since("2026-04-22")
        assert result == datetime(2026, 4, 22, tzinfo=timezone.utc)

    def test_iso_datetime(self):
        from cli import _parse_since
        result = _parse_since("2026-04-22T15:30:00+00:00")
        assert result == datetime(2026, 4, 22, 15, 30, tzinfo=timezone.utc)

    def test_garbage_returns_none(self):
        from cli import _parse_since
        assert _parse_since("yesterday") is None
        assert _parse_since("") is None
        assert _parse_since("nope") is None


# ── changes (event_log.jsonl reader) ─────────────────────────────────────────


class TestChangesCommand:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_log(self, entries: list[dict]) -> Path:
        log = self.tmpdir / "event_log.jsonl"
        with log.open("w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return log

    def _patch_log_path(self, log_path: Path):
        # cli._cmd_changes resolves the log path from config.__file__.parent.
        # Patch config.__file__ so the lookup lands in our tmpdir.
        import config
        return patch.object(config, "__file__", str(self.tmpdir / "config.py"))

    def test_filters_by_cutoff_and_groups(self, capsys):
        from cli import _cmd_changes
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=7)).isoformat()
        recent_a = (now - timedelta(hours=12)).isoformat()
        recent_b = (now - timedelta(hours=6)).isoformat()
        self._write_log([
            {"ts": old,      "action": "created",   "title": "TooOld",  "start": "2026-04-15T09:00:00+00:00", "source": "gmail"},
            {"ts": recent_a, "action": "created",   "title": "NewEvt",  "start": "2026-04-25T09:00:00+00:00", "source": "slack"},
            {"ts": recent_b, "action": "updated",   "title": "Patched", "start": "2026-04-26T10:00:00+00:00", "source": "gcal"},
            {"ts": recent_b, "action": "cancelled", "title": "Killed",  "start": "2026-04-27T10:00:00+00:00", "source": "manual"},
        ])
        with self._patch_log_path(self.tmpdir):
            rc = _cmd_changes("1d")
        out = capsys.readouterr().out
        assert rc == 0
        assert "TooOld" not in out
        assert "NewEvt" in out
        assert "Patched" in out
        assert "Killed" in out
        assert "created (1)" in out
        assert "updated (1)" in out
        assert "cancelled (1)" in out

    def test_no_log_file_emits_friendly_message(self, capsys):
        from cli import _cmd_changes
        with self._patch_log_path(self.tmpdir):
            rc = _cmd_changes("1d")
        out = capsys.readouterr().out
        assert rc == 0
        assert "empty" in out.lower() or "no" in out.lower()

    def test_no_changes_in_window(self, capsys):
        from cli import _cmd_changes
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self._write_log([{"ts": old, "action": "created", "title": "Ancient", "start": "2026-04-01T09:00:00+00:00", "source": "gmail"}])
        with self._patch_log_path(self.tmpdir):
            rc = _cmd_changes("1d")
        out = capsys.readouterr().out
        assert rc == 0
        assert "No changes" in out

    def test_invalid_since_returns_error(self, capsys):
        from cli import _cmd_changes
        rc = _cmd_changes("yesterday")
        captured = capsys.readouterr()
        assert rc == 1
        assert "could not parse" in captured.err.lower()

    def test_skips_malformed_jsonl_lines(self, capsys):
        from cli import _cmd_changes
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat()
        log = self.tmpdir / "event_log.jsonl"
        with log.open("w") as f:
            f.write("not even json\n")
            f.write(json.dumps({"ts": recent, "action": "created", "title": "Good", "start": "2026-04-25T09:00:00+00:00", "source": "x"}) + "\n")
            f.write("\n")  # blank line
            f.write('{"ts": "garbage-ts", "action": "created", "title": "BadTs"}\n')
        with self._patch_log_path(self.tmpdir):
            rc = _cmd_changes("1d")
        out = capsys.readouterr().out
        assert rc == 0
        assert "Good" in out
        assert "BadTs" not in out


# ── cli.py is directly runnable (regression guard) ────────────────────────────


def test_cli_py_has_main_guard_and_runs():
    """`python cli.py <cmd>` must NOT be a silent no-op. cli.py previously lacked a
    __main__ block, which silently broke the text/vision/decide job kinds (they
    shelled cli.py and got rc 0 / no output). --help proves the entrypoint runs."""
    import subprocess
    import sys
    ea_dir = Path(__file__).resolve().parents[1]  # event-aggregator/
    proc = subprocess.run(
        [sys.executable, "cli.py", "--help"],
        cwd=ea_dir, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    assert "usage" in (proc.stdout + proc.stderr).lower()
    assert "decide" in proc.stdout  # subcommands registered
