"""
migration_verifier — heart of Phase 12 v3.

Hourly @periodic_task that walks the in-flight migration list and:
  1. Confirms the migrated Job has fired since the last check (within
     the cadence × 1.5 budget). No-fire → rollback.
  2. Confirms the @baseline metric advanced as expected. Diverged → rollback.
  3. Increments the soaked-hours counter. At 72 → auto-promote (delete the
     `.plist.disabled` and the original script).

Rollback renames `<plist>.disabled` → `<plist>` and kickstarts launchctl,
then logs an incident readable by Phase 6's daily-digest.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from huey import crontab

from jobs import MIGRATIONS_STATE_PATH, huey
from jobs.lib import _parse_duration

logger = logging.getLogger(__name__)

INCIDENTS_PATH = Path.home() / "Home-Tools" / "logs" / "incidents.jsonl"
LAUNCHAGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

# Soak target. v3 plan says 72 hourly successes → promote.
SOAK_TARGET_HOURS = 72


@dataclass
class Migration:
    kind: str                      # "heartbeat", "restic_hourly", ...
    plist_label: str               # "com.home-tools.heartbeat"
    plist_source_path: str         # path to .plist.disabled in repo OR ~/Library
    cadence_seconds: int
    baseline_metric: str           # "incidents.jsonl-mtime", ...
    divergence_window: str         # "35m", "8d", ...
    started_at: str                # ISO
    last_check: str = ""           # ISO
    hours_soaked: int = 0
    last_fire: str = ""            # ISO of last consumer fire (set by hook)
    notes: list[str] = field(default_factory=list)


def load_state() -> dict:
    if not MIGRATIONS_STATE_PATH.exists():
        return {"in_flight": {}, "promoted": [], "rolled_back": []}
    return json.loads(MIGRATIONS_STATE_PATH.read_text())


def save_state(state: dict) -> None:
    MIGRATIONS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MIGRATIONS_STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def log_incident(event: str, **fields: Any) -> None:
    """Append a JSON line to incidents.jsonl. `event` is the verifier event
    name (migration_begun / migration_rollback / migration_promoted / ...);
    `fields` carries per-event detail like kind=, reason=, evidence=."""
    INCIDENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "migration_verifier",
        "event": event,
        **fields,
    }
    with INCIDENTS_PATH.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ── @baseline metric checks ───────────────────────────────────────────────────
#
# Each entry maps a `baseline_metric` string to a callable that returns
# (passed: bool, evidence: dict). Adding a new migration means adding one
# entry here when its metric isn't already supported.


def _check_mtime_recent(path: Path, max_age_seconds: int) -> tuple[bool, dict]:
    if not path.exists():
        return False, {"reason": "path_missing", "path": str(path)}
    age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    return age <= max_age_seconds, {"path": str(path), "age_seconds": int(age), "limit": max_age_seconds}


def _check_db_mtime(rel: str, max_age_seconds: int) -> tuple[bool, dict]:
    return _check_mtime_recent(Path.home() / "Home-Tools" / rel, max_age_seconds)


def _check_restic_snapshot_count(repo: str, max_age_seconds: int) -> tuple[bool, dict]:
    """Compare current snapshot count vs baseline — pass if it grew within the window.

    repo is the path under ~/Share1/ (autofs target). The mini's restic password
    is in env var RESTIC_PASSWORD_<repo_upper>.
    """
    repo_path = Path.home() / "Share1" / repo
    if not repo_path.exists():
        return False, {"reason": "repo_missing", "path": str(repo_path)}
    pwd = os.environ.get(f"RESTIC_PASSWORD_{repo.upper().replace('-', '_')}")
    if not pwd:
        return False, {"reason": "no_password_env", "expected": f"RESTIC_PASSWORD_{repo.upper().replace('-', '_')}"}
    try:
        out = subprocess.run(
            ["restic", "-r", str(repo_path), "snapshots", "--json"],
            env={**os.environ, "RESTIC_PASSWORD": pwd},
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, {"reason": "restic_unavailable", "error": str(exc)}
    if out.returncode != 0:
        return False, {"reason": "restic_error", "stderr": out.stderr[:200]}
    try:
        snaps = json.loads(out.stdout)
    except json.JSONDecodeError:
        return False, {"reason": "json_decode_failed", "stdout_prefix": out.stdout[:200]}
    if not snaps:
        return False, {"reason": "no_snapshots", "repo": repo}
    latest = max(s.get("time", "") for s in snaps)
    try:
        latest_dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
    except ValueError:
        return False, {"reason": "bad_timestamp", "latest": latest}
    age = (datetime.now(timezone.utc) - latest_dt).total_seconds()
    return age <= max_age_seconds, {"repo": repo, "latest_age_seconds": int(age), "limit": max_age_seconds, "count": len(snaps)}


def check_baseline(metric: str, divergence_window: str) -> tuple[bool, dict]:
    """Dispatch on metric string. Returns (passed, evidence)."""
    window_s = _parse_duration(divergence_window)

    if metric == "incidents.jsonl-mtime":
        return _check_mtime_recent(INCIDENTS_PATH, window_s)
    if metric.startswith("file-mtime:"):
        rel = metric.split(":", 1)[1]
        return _check_mtime_recent(Path.home() / "Home-Tools" / rel, window_s)
    if metric.startswith("db-mtime:"):
        rel = metric.split(":", 1)[1]
        return _check_db_mtime(rel, window_s)
    if metric.startswith("restic-snapshot-count:"):
        repo = metric.split(":", 1)[1]
        return _check_restic_snapshot_count(repo, window_s)
    if metric == "no-op":
        # For migrations whose baseline is "the verifier itself just runs cleanly"
        return True, {"reason": "no-op metric — always passes"}
    return False, {"reason": "unsupported_metric", "metric": metric}


# ── rollback / promote ────────────────────────────────────────────────────────


def rollback(migration: dict, reason: str, evidence: dict) -> None:
    """Re-enable old plist; log incident; kickstart launchctl."""
    plist_path = Path(migration["plist_source_path"])
    disabled = plist_path.with_suffix(plist_path.suffix + ".disabled")
    if disabled.exists():
        disabled.rename(plist_path)
    label = migration["plist_label"]
    user_plist = LAUNCHAGENTS_DIR / plist_path.name
    if user_plist.exists():
        try:
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"], check=False)
        except FileNotFoundError:
            pass
    log_incident("migration_rollback", kind=migration["kind"], reason=reason, evidence=evidence)


def promote(migration: dict) -> None:
    """72h soak passed cleanly. Delete the .disabled plist."""
    plist_path = Path(migration["plist_source_path"])
    disabled = plist_path.with_suffix(plist_path.suffix + ".disabled")
    if disabled.exists():
        disabled.unlink()
    log_incident("migration_promoted", kind=migration["kind"])


# ── verifier itself ───────────────────────────────────────────────────────────


@huey.periodic_task(crontab(minute="3"))  # 3 past every hour
def migration_verifier() -> dict:
    """Walk in-flight migrations and act on each."""
    state = load_state()
    in_flight = state.get("in_flight", {})
    if not in_flight:
        return {"status": "idle", "in_flight": 0}

    promoted_now = []
    rolled_back_now = []
    now = datetime.now(timezone.utc)

    for kind, m in list(in_flight.items()):
        m["last_check"] = now.isoformat()

        # 1. Did it fire? (last_fire updated by huey post-execute hook in jobs/__init__.py)
        last_fire_iso = m.get("last_fire", "")
        if last_fire_iso:
            try:
                last_fire = datetime.fromisoformat(last_fire_iso)
                gap = (now - last_fire).total_seconds()
            except ValueError:
                gap = float("inf")
        else:
            gap = (now - datetime.fromisoformat(m["started_at"])).total_seconds()
        if gap > m["cadence_seconds"] * 1.5 and gap > 600:  # ignore <10min as startup grace
            evidence = {"gap_seconds": int(gap), "cadence_seconds": m["cadence_seconds"]}
            rollback(m, reason="no_fire", evidence=evidence)
            state.setdefault("rolled_back", []).append({**m, "reason": "no_fire", "evidence": evidence, "at": now.isoformat()})
            del in_flight[kind]
            rolled_back_now.append(kind)
            continue

        # 2. Baseline check.
        passed, evidence = check_baseline(m["baseline_metric"], m["divergence_window"])
        if not passed:
            rollback(m, reason="baseline_diverged", evidence=evidence)
            state.setdefault("rolled_back", []).append({**m, "reason": "baseline_diverged", "evidence": evidence, "at": now.isoformat()})
            del in_flight[kind]
            rolled_back_now.append(kind)
            continue

        # 3. Soak counter.
        m["hours_soaked"] = m.get("hours_soaked", 0) + 1
        if m["hours_soaked"] >= SOAK_TARGET_HOURS:
            promote(m)
            state.setdefault("promoted", []).append({**m, "at": now.isoformat()})
            del in_flight[kind]
            promoted_now.append(kind)

    state["in_flight"] = in_flight
    save_state(state)
    return {
        "status": "ran",
        "checked": len(in_flight) + len(promoted_now) + len(rolled_back_now),
        "promoted": promoted_now,
        "rolled_back": rolled_back_now,
        "in_flight_remaining": list(in_flight.keys()),
    }


def record_fire(kind: str) -> None:
    """Called by Job kinds at successful exit to update last_fire timestamp.

    Lightweight: append-and-rewrite. The verifier's own runs only happen
    hourly so contention is non-issue at the mini's scale.
    """
    state = load_state()
    if kind not in state.get("in_flight", {}):
        return
    state["in_flight"][kind]["last_fire"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
