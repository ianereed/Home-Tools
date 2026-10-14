"""Fuel planner kinds: local-time gating, enqueue-on-change, failure alerting."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jobs.kinds import nutrition_refresh as R
from jobs.kinds import nutrition_watch as W

TZ = ZoneInfo("America/Los_Angeles")


@pytest.fixture(autouse=True)
def _project_paths():
    """@requires checks ~/Home-Tools/health-dashboard{,/data/health.db}; HOME is a tmpdir in tests."""
    from pathlib import Path
    d = Path.home() / "Home-Tools" / "health-dashboard" / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "health.db").touch()


def _at(hour):
    return lambda: datetime(2026, 11, 2, hour, 30, tzinfo=TZ)


class _Proc:
    def __init__(self, stdout="", rc=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, rc, stderr


def test_paths_target_health_dashboard_venv():
    assert R.PROJECT.name == "health-dashboard"
    assert R.VENV_PYTHON == R.PROJECT / ".venv" / "bin" / "python3"


def test_watch_skips_outside_hours(monkeypatch):
    monkeypatch.setattr(W, "local_now", _at(23))
    monkeypatch.setattr(W.subprocess, "run", lambda *a, **k: pytest.fail("must not call Garmin at night"))
    assert W.nutrition_watch.call_local() == {"skipped": "outside watch hours"}


def test_watch_enqueues_refresh_on_change(monkeypatch):
    calls = []
    monkeypatch.setattr(W, "local_now", _at(10))

    def fake_run(argv, **kw):
        calls.append(argv[3:])
        if argv[3:] == []:
            return _Proc(json.dumps({"changed": True, "reason": "new activity 42"}))
        return _Proc('{"ok": true}')
    monkeypatch.setattr(W.subprocess, "run", fake_run)
    refreshed = []
    monkeypatch.setattr(W, "nutrition_refresh", lambda reason, trigger: refreshed.append((reason, trigger)))
    synced = []
    monkeypatch.setattr(W, "garmin_gcal_sync", lambda reason: synced.append(reason))
    out = W.nutrition_watch.call_local()
    assert out["enqueued"] and refreshed == [("new activity 42", "watch")]
    assert synced == ["watch"]
    assert ["--mark-enqueued"] in calls


def test_watch_no_change_no_enqueue(monkeypatch):
    monkeypatch.setattr(W, "local_now", _at(10))
    monkeypatch.setattr(W.subprocess, "run", lambda *a, **k: _Proc(json.dumps({"changed": False, "reason": "no new activity"})))
    monkeypatch.setattr(W, "nutrition_refresh", lambda *a: pytest.fail("must not enqueue"))
    assert W.nutrition_watch.call_local()["changed"] is False


def test_refresh_alerts_on_second_consecutive_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "LOG_DIR", tmp_path)
    monkeypatch.setattr(R, "FAILS_FILE", tmp_path / "f")
    alerts = []
    monkeypatch.setattr(R, "_notify", lambda t, b: alerts.append(t))

    def run(argv, **kw):
        if argv[0] == "curl":
            return _Proc()
        return _Proc("", rc=1, stderr="boom")
    monkeypatch.setattr(R.subprocess, "run", run)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            R.nutrition_refresh.call_local("t")
    assert alerts == ["Fuel planner refresh failing"]
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: _Proc(json.dumps({"ok": True, "run_id": 3, "sources": {"mfp": "ok"}})))
    assert R.nutrition_refresh.call_local("t")["run_id"] == 3
    assert (tmp_path / "f").read_text() == "0"


def test_refresh_warns_when_mfp_session_expired(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "LOG_DIR", tmp_path)
    monkeypatch.setattr(R, "FAILS_FILE", tmp_path / "f")
    alerts = []
    monkeypatch.setattr(R, "_notify", lambda t, b: alerts.append(t))
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: _Proc(json.dumps({"ok": True, "sources": {"mfp": "auth_expired"}})))
    R.nutrition_refresh.call_local("t")
    assert alerts == ["MyFitnessPal session expired"]


@pytest.mark.parametrize("hour,fires", [(7, True), (8, False), (6, False)])
def test_daily_refresh_local_hour_gate(monkeypatch, hour, fires):
    monkeypatch.setattr(R, "local_now", _at(hour))
    seen = []
    monkeypatch.setattr(R, "nutrition_refresh", lambda *a: seen.append(a))
    R.nutrition_daily_refresh.call_local()
    assert bool(seen) is fires


def test_daily_crons_cover_both_dst_offsets():
    # 07:30 local is 14:30 UTC in PDT and 15:30 UTC in PST; 19:30 local is 02:30 / 03:30 UTC.
    from datetime import timezone
    for month, utc_h in ((10, 14), (12, 15)):
        assert datetime(2026, month, 15, utc_h, 30, tzinfo=timezone.utc).astimezone(TZ).hour == 7
    for month, utc_h in ((10, 2), (12, 3)):
        assert datetime(2026, month, 15, utc_h, 30, tzinfo=timezone.utc).astimezone(TZ).hour == 19


# ---------------------------------------------------------------- garmin_gcal_sync
from jobs.kinds import garmin_gcal_sync as G  # noqa: E402


@pytest.mark.parametrize("fn,hour", [("garmin_gcal_sync_morning", 7), ("garmin_gcal_sync_midday", 13),
                                     ("garmin_gcal_sync_evening", 19)])
def test_gcal_sync_three_times_a_day_local_gate(monkeypatch, fn, hour):
    seen = []
    monkeypatch.setattr(G, "garmin_gcal_sync", lambda reason: seen.append(reason))
    for h in (hour - 1, hour, hour + 1):
        monkeypatch.setattr(G, "local_now", _at(h))
        getattr(G, fn).call_local()
    assert len(seen) == 1


def test_gcal_sync_crons_cover_both_dst_offsets():
    from datetime import timezone
    for local_h, utc_pair in ((7, (14, 15)), (13, (20, 21)), (19, (2, 3))):
        for month, utc_h in zip((10, 12), utc_pair):
            assert datetime(2026, month, 15, utc_h, 10, tzinfo=timezone.utc).astimezone(TZ).hour == local_h


def test_gcal_sync_alerts_on_second_failure_and_summarises_success(monkeypatch, tmp_path):
    from pathlib import Path
    (Path.home() / "Home-Tools" / "event-aggregator").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(G, "LOG_DIR", tmp_path)
    monkeypatch.setattr(G, "FAILS_FILE", tmp_path / "f")
    alerts = []
    monkeypatch.setattr(G, "_notify", lambda t, b: alerts.append(t))
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **k: _Proc("", rc=1, stderr="boom"))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            G.garmin_gcal_sync.call_local("t")
    assert alerts == ["Garmin → Google Calendar sync failing"]
    out = {"ok": True, "mode": "live", "desired": 9, "existing": 7, "ops": [{"op": "insert"}, {"op": "patch"}]}
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **k: _Proc(json.dumps(out)))
    assert G.garmin_gcal_sync.call_local("t") == {"ok": True, "mode": "live", "skipped": None, "desired": 9,
                                                  "existing": 7, "duration_ms": None, "ops": 2}
    assert (tmp_path / "f").read_text() == "0"
