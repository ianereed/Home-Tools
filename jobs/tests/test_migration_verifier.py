"""
migration_verifier behaviour (post-2026-05-01 hotfix):
  - empty in_flight → idle
  - in-grace path-missing → grace-skip (not rolled back)
  - past-grace path-missing → rollback
  - baseline file existed pre-cutover but doesn't advance → rollback
  - past-grace baseline advanced + within window → soak ticks
  - 72 successful checks → promote
  - manual halt → not acted on
  - restic baseline path uses ~/Share1/mac-mini-backups/<repo>
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobs.kinds._internal import migration_verifier as mv


def _migration(
    kind: str = "heartbeat",
    baseline_metric: str = "incidents.jsonl-mtime",
    window: str = "35m",
    cadence_seconds: int = 1800,
    started_offset_min: int = 240,  # 4h: well past grace for 30m cadence
    last_fire_offset_min: int = 5,
    hours_soaked: int = 0,
    baseline_snapshot=None,
):
    """Build an in-flight migration record with sensible defaults.

    Defaults put the migration well past grace (4h elapsed, cadence 30m,
    grace = 60m), so the verifier treats failures as real, not first-fire.
    """
    started = datetime.now(timezone.utc) - timedelta(minutes=started_offset_min)
    last_fire = datetime.now(timezone.utc) - timedelta(minutes=last_fire_offset_min)
    return {
        "kind": kind,
        "plist_label": f"com.home-tools.{kind.replace('_', '-')}",
        "plist_source_path": "/tmp/fake.plist",
        "cadence_seconds": cadence_seconds,
        "baseline_metric": baseline_metric,
        "divergence_window": window,
        "started_at": started.isoformat(),
        "last_fire": last_fire.isoformat(),
        "last_check": "",
        "hours_soaked": hours_soaked,
        "baseline_snapshot": baseline_snapshot,
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
    assert out["grace_skips"] == []
    after = mv.load_state()
    assert after["in_flight"]["heartbeat"]["hours_soaked"] == 1


def test_path_missing_in_grace_period_skips(monkeypatch, tmp_path):
    """A path-missing baseline within grace period should NOT roll back."""
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    # No incidents.jsonl file. Migration started 5 min ago; cadence 30m;
    # grace = 60m. Should grace-skip, not roll back.
    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    m = _migration(started_offset_min=5)
    m["plist_source_path"] = str(fake_plist)
    state = {"in_flight": {"heartbeat": m}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert out["rolled_back"] == []
    assert "heartbeat" in out["grace_skips"]
    assert fake_disabled.exists()  # still disabled — original plist NOT re-enabled
    after = mv.load_state()
    assert after["in_flight"]["heartbeat"]["hours_soaked"] == 0  # grace doesn't increment


def test_path_missing_past_grace_rolls_back(monkeypatch, tmp_path):
    """A path-missing baseline past grace period should roll back."""
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    # 4h elapsed, cadence 30m → past grace (60m). No incidents.jsonl.
    m = _migration(started_offset_min=240)
    m["plist_source_path"] = str(fake_plist)
    state = {"in_flight": {"heartbeat": m}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert "heartbeat" in out["rolled_back"]
    assert fake_plist.exists()  # renamed back
    after = mv.load_state()
    assert "heartbeat" not in after["in_flight"]


def test_baseline_no_advance_past_grace_rolls_back(monkeypatch, tmp_path):
    """If the snapshot value is set and current matches, rollback past grace."""
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    f = tmp_path / "incidents.jsonl"
    f.write_text("{}\n")
    pre_cutover_mtime = f.stat().st_mtime  # snapshot

    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    m = _migration(
        started_offset_min=240,
        baseline_snapshot=pre_cutover_mtime,
    )
    m["plist_source_path"] = str(fake_plist)
    state = {"in_flight": {"heartbeat": m}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert "heartbeat" in out["rolled_back"]
    after = mv.load_state()
    rb_record = next(e for e in after["rolled_back"] if e["kind"] == "heartbeat")
    assert rb_record["evidence"]["reason"] == "no_advance_since_snapshot"


def test_baseline_advance_past_grace_passes(monkeypatch, tmp_path):
    """Snapshot set, current advanced past it, within window → pass."""
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    f = tmp_path / "incidents.jsonl"
    f.write_text("{}\n")
    snapshot_mtime = f.stat().st_mtime - 100  # snapshot is OLDER than current
    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    m = _migration(
        started_offset_min=240,
        baseline_snapshot=snapshot_mtime,
    )
    m["plist_source_path"] = str(fake_plist)
    state = {"in_flight": {"heartbeat": m}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert out["rolled_back"] == []
    after = mv.load_state()
    assert after["in_flight"]["heartbeat"]["hours_soaked"] == 1


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


def test_no_fire_past_grace_rolls_back(monkeypatch, tmp_path):
    """Kind hasn't fired in cadence × 1.5 (past grace) → rollback."""
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    (tmp_path / "incidents.jsonl").write_text("{}\n")
    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    # 4h elapsed (past 60m grace), last_fire 2h ago, cadence 1800 (30m) →
    # 2h gap > cadence × 1.5 (45m) AND > 600s. Should roll back.
    m = _migration(started_offset_min=240, last_fire_offset_min=120)
    m["plist_source_path"] = str(fake_plist)
    state = {"in_flight": {"heartbeat": m}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert "heartbeat" in out["rolled_back"]


def test_no_fire_in_grace_does_not_roll_back(monkeypatch, tmp_path):
    """Even with no fire yet, in grace → no rollback."""
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    (tmp_path / "incidents.jsonl").write_text("{}\n")
    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    # 5 min elapsed, cadence 30m → in grace (60m).
    m = _migration(started_offset_min=5, last_fire_offset_min=999999)
    m["last_fire"] = ""  # never fired
    m["plist_source_path"] = str(fake_plist)
    state = {"in_flight": {"heartbeat": m}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert out["rolled_back"] == []


def test_halted_kind_not_acted_on(monkeypatch, tmp_path):
    """`halted: true` means the verifier ignores this kind entirely."""
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    fake_plist = tmp_path / "fake.plist"
    fake_disabled = tmp_path / "fake.plist.disabled"
    fake_disabled.write_text("")
    # Past grace, no incidents.jsonl — would roll back if not halted.
    m = _migration(started_offset_min=240)
    m["plist_source_path"] = str(fake_plist)
    m["halted"] = True
    state = {"in_flight": {"heartbeat": m}}
    mv.save_state(state)

    out = mv.migration_verifier.call_local()
    assert out["rolled_back"] == []
    after = mv.load_state()
    assert "heartbeat" in after["in_flight"]


def test_check_baseline_unsupported_metric():
    passed, evidence = mv.check_baseline(
        "nonsense-metric", "1h", cadence_seconds=300, snapshot=None, elapsed_seconds=99999,
    )
    assert passed is False
    assert evidence["reason"] == "unsupported_metric"


def test_check_baseline_no_op_always_passes():
    passed, _ = mv.check_baseline(
        "no-op", "1h", cadence_seconds=300, snapshot=None, elapsed_seconds=0,
    )
    assert passed is True


def test_check_baseline_file_mtime_recent(tmp_path, monkeypatch):
    # file-mtime: prefix uses ~/Home-Tools/<rel> — conftest pinned $HOME to a tmp
    home = Path.home() / "Home-Tools"
    home.mkdir(parents=True, exist_ok=True)
    f = home / "fresh.txt"
    f.write_text("x")
    passed, _ = mv.check_baseline(
        "file-mtime:fresh.txt", "1h", cadence_seconds=300, snapshot=None, elapsed_seconds=99999,
    )
    assert passed is True


def test_check_baseline_file_mtime_stale_past_grace(tmp_path, monkeypatch):
    """File exists but too old → fails after grace."""
    import os
    home = Path.home() / "Home-Tools"
    home.mkdir(parents=True, exist_ok=True)
    f = home / "stale.txt"
    f.write_text("x")
    # Mtime 2 hours ago. cadence 5m + window 6m = 11m. Way too stale.
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(f, (old_ts, old_ts))
    passed, evidence = mv.check_baseline(
        "file-mtime:stale.txt", "6m", cadence_seconds=300, snapshot=None, elapsed_seconds=99999,
    )
    assert passed is False
    assert evidence["reason"] == "stale"


def test_capture_baseline_snapshot_for_missing_file_returns_none(tmp_path):
    snap = mv.capture_baseline_snapshot("file-mtime:does-not-exist.txt")
    assert snap is None


def test_capture_baseline_snapshot_for_existing_file_returns_mtime():
    home = Path.home() / "Home-Tools"
    home.mkdir(parents=True, exist_ok=True)
    f = home / "snap.txt"
    f.write_text("x")
    snap = mv.capture_baseline_snapshot("file-mtime:snap.txt")
    assert snap == f.stat().st_mtime


def test_restic_repo_path_uses_mac_mini_backups():
    """Hotfix Cluster D — verifier looks under ~/Share1/mac-mini-backups/."""
    p = mv._restic_repo_path("restic-hourly")
    assert p == Path.home() / "Share1" / "mac-mini-backups" / "restic-hourly"


def test_rollback_bootstraps_launchagent_after_rename(monkeypatch, tmp_path):
    """Regression (2026-05-03): rollback() must bootout-then-bootstrap, not
    `kickstart -k`. The pre-fix call was a silent no-op because migrate() had
    already bootout'd the agent — leaving rolled-back kinds unloaded.

    LAUNCHAGENTS_DIR monkeypatch is REQUIRED — the existing rollback tests
    don't override it, so their `user_plist.exists()` check returns False on
    the pinned-HOME conftest and the launchctl block is silently skipped.
    That false-pass is exactly why this bug went undetected.
    """
    import json
    import os
    import subprocess
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    monkeypatch.setattr(mv, "LAUNCHAGENTS_DIR", tmp_path)

    plist = tmp_path / "com.home-tools.heartbeat.plist"
    disabled = tmp_path / "com.home-tools.heartbeat.plist.disabled"
    disabled.write_text("<plist></plist>")  # rename precondition

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mv.subprocess, "run", fake_run)

    migration = {
        "kind": "heartbeat",
        "plist_label": "com.home-tools.heartbeat",
        "plist_source_path": str(plist),
    }
    mv.rollback(migration, reason="test_reason", evidence={"k": "v"})

    uid = os.getuid()
    assert calls == [
        ["launchctl", "bootout", f"gui/{uid}/com.home-tools.heartbeat"],
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
    ]
    assert plist.exists()
    assert not disabled.exists()
    # Successful bootstrap → no bootstrap_stderr in evidence
    incidents = (tmp_path / "incidents.jsonl").read_text().strip().splitlines()
    rec = json.loads(incidents[0])
    assert rec["event"] == "migration_rollback"
    assert rec["evidence"] == {"k": "v"}


def test_rollback_logs_bootstrap_stderr_on_failure(monkeypatch, tmp_path):
    """If bootstrap returns non-zero, stderr lands in incident evidence
    (truncated to 200 chars). Without this signal, a silent re-load failure
    looks identical to a successful rollback in the daily-digest."""
    import json
    import subprocess
    monkeypatch.setattr(mv, "MIGRATIONS_STATE_PATH", tmp_path / "m.json")
    monkeypatch.setattr(mv, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    monkeypatch.setattr(mv, "LAUNCHAGENTS_DIR", tmp_path)

    plist = tmp_path / "com.home-tools.heartbeat.plist"
    disabled = tmp_path / "com.home-tools.heartbeat.plist.disabled"
    disabled.write_text("<plist></plist>")

    def fake_run(cmd, **kwargs):
        if len(cmd) >= 2 and cmd[1] == "bootstrap":
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="Boot-out failed: 5: Input/output error\n",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mv.subprocess, "run", fake_run)

    migration = {
        "kind": "heartbeat",
        "plist_label": "com.home-tools.heartbeat",
        "plist_source_path": str(plist),
    }
    mv.rollback(migration, reason="test_reason", evidence={"k": "v"})

    incidents = (tmp_path / "incidents.jsonl").read_text().strip().splitlines()
    rec = json.loads(incidents[0])
    assert rec["event"] == "migration_rollback"
    assert rec["evidence"]["k"] == "v"
    assert "Boot-out failed" in rec["evidence"]["bootstrap_stderr"]
    assert len(rec["evidence"]["bootstrap_stderr"]) <= 200
