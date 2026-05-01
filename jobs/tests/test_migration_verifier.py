"""
migration_verifier behaviour:
  - empty in_flight → idle
  - missing-file baseline → rollback
  - passing baseline → soak counter increments
  - 72 successful checks → promote
  - rollback removes from in_flight, records in rolled_back
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobs.kinds._internal import migration_verifier as mv


def _migration(kind="heartbeat", baseline_metric="incidents.jsonl-mtime",
               window="35m", started_offset_min=2, hours_soaked=0):
    started = datetime.now(timezone.utc) - timedelta(minutes=started_offset_min)
    last_fire = datetime.now(timezone.utc) - timedelta(minutes=2)
    return {
        "kind": kind,
        "plist_label": f"com.home-tools.{kind.replace('_', '-')}",
        "plist_source_path": "/tmp/fake.plist",
        "cadence_seconds": 1800,
        "baseline_metric": baseline_metric,
        "divergence_window": window,
        "started_at": started.isoformat(),
        "last_fire": last_fire.isoformat(),
        "last_check": "",
        "hours_soaked": hours_soaked,
        "notes": [],
    }


def test_idle_when_no_in_flight(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    out = mv.migration_verifier.call_local()
    assert out["status"] == "idle"


def test_baseline_pass_increments_soak(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    (tmp_path / "incidents.jsonl").write_text("{}\n")  # touch -> recent mtime
    state = {"in_flight": {"heartbeat": _migration()}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert out["status"] == "ran"
    assert out["promoted"] == []
    assert out["rolled_back"] == []
    after = mv.load_state()
    assert after["in_flight"]["heartbeat"]["hours_soaked"] == 1


def test_baseline_diverge_rolls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    # No incidents.jsonl at all → mtime check fails
    state = {"in_flight": {"heartbeat": _migration()}}
    mv.save_state(state)

    # Stub the rollback's launchctl + rename so test doesn't need real plists
    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    state["in_flight"]["heartbeat"]["plist_source_path"] = str(fake_plist)
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert "heartbeat" in out["rolled_back"]
    assert fake_plist.exists()  # renamed back
    after = mv.load_state()
    assert "heartbeat" not in after["in_flight"]
    assert any(e["kind"] == "heartbeat" for e in after["rolled_back"])


def test_72h_soak_promotes(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    (tmp_path / "incidents.jsonl").write_text("{}\n")
    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    m = _migration(hours_soaked=71)
    m["plist_source_path"] = str(fake_plist)
    state = {"in_flight": {"heartbeat": m}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert out["promoted"] == ["heartbeat"]
    assert not fake_disabled.exists()  # promote deletes it
    after = mv.load_state()
    assert "heartbeat" not in after["in_flight"]
    assert any(p["kind"] == "heartbeat" for p in after["promoted"])


def test_no_fire_rolls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    (tmp_path / "incidents.jsonl").write_text("{}\n")
    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    m = _migration()
    # Set last_fire to 2 hours ago, cadence 30 min — well past 1.5x cadence
    m["last_fire"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    m["cadence_seconds"] = 1800
    m["plist_source_path"] = str(fake_plist)
    state = {"in_flight": {"heartbeat": m}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert "heartbeat" in out["rolled_back"]


def test_check_baseline_unsupported_metric():
    passed, evidence = mv.check_baseline("nonsense-metric", "1h")
    assert passed is False
    assert evidence["reason"] == "unsupported_metric"


def test_check_baseline_no_op_always_passes():
    passed, _ = mv.check_baseline("no-op", "1h")
    assert passed is True


def test_check_baseline_file_mtime_recent(tmp_path, monkeypatch):
    # file-mtime: prefix uses ~/Home-Tools/<rel> — conftest pinned $HOME to a tmp
    home = Path.home() / "Home-Tools"
    home.mkdir(parents=True, exist_ok=True)
    f = home / "fresh.txt"
    f.write_text("x")
    passed, _ = mv.check_baseline("file-mtime:fresh.txt", "1h")
    assert passed is True
