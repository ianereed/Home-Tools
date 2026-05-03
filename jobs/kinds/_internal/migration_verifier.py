"""
migration_verifier — heart of Phase 12 v3 (post-2026-05-01 hotfix).

Hourly @periodic_task that walks the in-flight migration list and:
  1. Confirms the migrated Job has fired since the last check (within
     the cadence × 1.5 budget). No-fire → rollback.
  2. Confirms the @baseline metric is healthy. Diverged → rollback.
  3. Increments the soaked-hours counter. At 72 → auto-promote (delete the
     `.plist.disabled` and the original script).

Rollback renames `<plist>.disabled` → `<plist>`, bootout-then-bootstrap
launchctl (since `migrate()` bootout'd the agent — kickstart was a silent
no-op), then logs an incident readable by Phase 6's daily-digest.

## Baseline check semantics (hotfix)

Each migration record carries a `baseline_snapshot` captured at
`migration_begun` (mtime float / restic snapshot count / None for
unsupported metrics). The verifier judges health using THREE signals:

  (a) **Grace period.** For the first `cadence_seconds × 2` after
      migration_begun, no rollback fires for path-missing or
      no-advance — the kind hasn't been around long enough for its
      next natural fire. Grace passes count as "healthy" but do NOT
      increment hours_soaked.

  (b) **Snapshot advance.** For mtime/count metrics, after grace, the
      current value must be greater than the snapshot. This catches
      the case where the baseline file existed pre-cutover but the
      migrated kind isn't actually updating it.

  (c) **Staleness window.** After grace, current value must also be
      "recent enough". Recent means: `now - mtime ≤ cadence + window`.
      This handles all cadences: 5-min kinds get short windows;
      weekly kinds get week-long windows.

A single rollback fires only if (a) is exhausted AND ((b) or (c)) fails.
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
    baseline_snapshot: Any = None  # mtime float / count int / None
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


# ── path helpers shared by snapshot capture + verifier ────────────────────────


def _resolve_metric_path(metric: str) -> Path | None:
    """Map a baseline metric string to the on-disk Path it observes.

    Returns None for metrics that aren't path-based (restic counts, no-op).
    """
    if metric == "incidents.jsonl-mtime":
        return INCIDENTS_PATH
    if metric.startswith("file-mtime:"):
        return Path.home() / "Home-Tools" / metric.split(":", 1)[1]
    if metric.startswith("db-mtime:"):
        return Path.home() / "Home-Tools" / metric.split(":", 1)[1]
    return None


def _restic_repo_path(repo: str) -> Path:
    """Where the named restic repo lives on disk.

    Per Mac-mini/scripts/restic-backup.py:25, repos are under
    ~/Share1/mac-mini-backups/, NOT ~/Share1/. Pre-hotfix verifier had
    the wrong path here.

    Resolved lazily via Path.home() so tests that monkeypatch HOME
    pick up the override.
    """
    return Path.home() / "Share1" / "mac-mini-backups" / repo


def _restic_snapshot_count(repo: str) -> tuple[int | None, dict]:
    """Read snapshot count for a restic repo. Returns (count, evidence).
    count=None if unreadable; evidence describes why."""
    repo_path = _restic_repo_path(repo)
    if not repo_path.exists():
        return None, {"reason": "repo_missing", "path": str(repo_path)}
    pwd = os.environ.get(f"RESTIC_PASSWORD_{repo.upper().replace('-', '_')}")
    if not pwd:
        return None, {"reason": "no_password_env", "expected": f"RESTIC_PASSWORD_{repo.upper().replace('-', '_')}"}
    try:
        out = subprocess.run(
            ["restic", "-r", str(repo_path), "snapshots", "--json"],
            env={**os.environ, "RESTIC_PASSWORD": pwd},
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, {"reason": "restic_unavailable", "error": str(exc)}
    if out.returncode != 0:
        return None, {"reason": "restic_error", "stderr": out.stderr[:200]}
    try:
        snaps = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None, {"reason": "json_decode_failed", "stdout_prefix": out.stdout[:200]}
    return len(snaps), {"repo": repo, "count": len(snaps)}


def capture_baseline_snapshot(metric: str) -> Any:
    """Read the current value of a baseline metric — used at migration_begun
    to record a `baseline_snapshot` that the verifier later requires the
    migrated kind to advance past.

    Returns:
      - float (mtime epoch seconds) for file-mtime / db-mtime / incidents.jsonl-mtime
      - int (snapshot count) for restic-snapshot-count
      - None for no-op or unsupported metrics, or when path doesn't exist yet
    """
    p = _resolve_metric_path(metric)
    if p is not None:
        return p.stat().st_mtime if p.exists() else None
    if metric.startswith("restic-snapshot-count:"):
        repo = metric.split(":", 1)[1]
        count, _ = _restic_snapshot_count(repo)
        return count
    return None


# ── @baseline metric checks ───────────────────────────────────────────────────


def _check_path_metric(
    path: Path,
    window_s: int,
    cadence_s: int,
    snapshot: float | None,
    elapsed_s: float,
) -> tuple[bool, dict]:
    """Verifier check for any path-based mtime metric.

    Logic (post-hotfix):
      - If still in grace (elapsed < cadence × 2) and path is missing or
        mtime <= snapshot, return healthy (first_fire_grace).
      - After grace: path must exist, mtime must be > snapshot, and
        `now - mtime ≤ cadence + window`.
    """
    grace_s = cadence_s * 2

    if not path.exists():
        if elapsed_s < grace_s:
            return True, {"reason": "first_fire_grace", "path": str(path), "elapsed_s": int(elapsed_s), "grace_s": grace_s}
        return False, {"reason": "path_missing", "path": str(path)}

    current_mtime = path.stat().st_mtime
    age_s = datetime.now(timezone.utc).timestamp() - current_mtime
    max_age_s = cadence_s + window_s

    # Snapshot-advance: after grace, require strict advance.
    if snapshot is not None and current_mtime <= snapshot:
        if elapsed_s < grace_s:
            return True, {
                "reason": "first_fire_grace",
                "path": str(path),
                "elapsed_s": int(elapsed_s),
                "grace_s": grace_s,
                "snapshot_unchanged": True,
            }
        return False, {
            "reason": "no_advance_since_snapshot",
            "path": str(path),
            "snapshot_mtime": snapshot,
            "current_mtime": current_mtime,
        }

    # Staleness: now-mtime ≤ cadence + window
    if age_s > max_age_s:
        if elapsed_s < grace_s:
            return True, {
                "reason": "first_fire_grace",
                "path": str(path),
                "age_s": int(age_s),
                "limit_s": max_age_s,
            }
        return False, {
            "reason": "stale",
            "path": str(path),
            "age_seconds": int(age_s),
            "limit_seconds": max_age_s,
        }

    return True, {"path": str(path), "age_seconds": int(age_s), "limit_seconds": max_age_s}


def _check_restic_snapshot_count_advanced(
    repo: str,
    window_s: int,
    cadence_s: int,
    snapshot: int | None,
    elapsed_s: float,
) -> tuple[bool, dict]:
    """Restic check using snapshot-advance + cadence-aware staleness."""
    grace_s = cadence_s * 2
    count, evidence = _restic_snapshot_count(repo)
    if count is None:
        if elapsed_s < grace_s:
            return True, {"reason": "first_fire_grace", **evidence, "elapsed_s": int(elapsed_s)}
        return False, evidence

    # Snapshot-advance
    if snapshot is not None and count <= snapshot:
        if elapsed_s < grace_s:
            return True, {"reason": "first_fire_grace", "repo": repo, "snapshot_count": snapshot, "current_count": count, "elapsed_s": int(elapsed_s)}
        return False, {"reason": "no_advance_since_snapshot", "repo": repo, "snapshot_count": snapshot, "current_count": count}

    # Latest-snapshot staleness (was already checked here pre-hotfix)
    pwd = os.environ.get(f"RESTIC_PASSWORD_{repo.upper().replace('-', '_')}")
    if not pwd:
        return True, {"repo": repo, "count": count, "warn": "no password env to fetch latest timestamp"}
    try:
        out = subprocess.run(
            ["restic", "-r", str(_restic_repo_path(repo)), "snapshots", "--json"],
            env={**os.environ, "RESTIC_PASSWORD": pwd},
            capture_output=True, text=True, timeout=120,
        )
        snaps = json.loads(out.stdout)
        if not snaps:
            return False, {"reason": "no_snapshots", "repo": repo}
        latest = max(s.get("time", "") for s in snaps)
        latest_dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - latest_dt).total_seconds()
        max_age_s = cadence_s + window_s
        if age > max_age_s:
            if elapsed_s < grace_s:
                return True, {"reason": "first_fire_grace", "repo": repo, "age_s": int(age), "limit_s": max_age_s}
            return False, {"reason": "stale", "repo": repo, "latest_age_seconds": int(age), "limit_seconds": max_age_s}
        return True, {"repo": repo, "count": count, "latest_age_seconds": int(age), "limit_seconds": max_age_s}
    except Exception as exc:
        return True, {"repo": repo, "count": count, "warn": f"latest-timestamp check skipped: {exc}"}


def check_baseline(
    metric: str,
    divergence_window: str,
    cadence_seconds: int,
    snapshot: Any,
    elapsed_seconds: float,
) -> tuple[bool, dict]:
    """Dispatch on metric string. Returns (passed, evidence).

    Args:
      metric: from @baseline(metric=…)
      divergence_window: from @baseline(divergence_window=…); fudge factor.
      cadence_seconds: kind's expected fire cadence (drives grace + staleness).
      snapshot: value captured at migration_begun (mtime float / count int / None).
      elapsed_seconds: now - migration_begun.
    """
    window_s = _parse_duration(divergence_window)

    p = _resolve_metric_path(metric)
    if p is not None:
        return _check_path_metric(p, window_s, cadence_seconds, snapshot, elapsed_seconds)

    if metric.startswith("restic-snapshot-count:"):
        repo = metric.split(":", 1)[1]
        return _check_restic_snapshot_count_advanced(repo, window_s, cadence_seconds, snapshot, elapsed_seconds)

    if metric == "no-op":
        return True, {"reason": "no-op metric — always passes"}

    return False, {"reason": "unsupported_metric", "metric": metric}


# ── rollback / promote ────────────────────────────────────────────────────────


def rollback(migration: dict, reason: str, evidence: dict) -> None:
    """Re-enable old plist; bootout-then-bootstrap launchctl; log incident."""
    plist_path = Path(migration["plist_source_path"])
    disabled = plist_path.with_suffix(plist_path.suffix + ".disabled")
    if disabled.exists():
        disabled.rename(plist_path)
    label = migration["plist_label"]
    user_plist = LAUNCHAGENTS_DIR / plist_path.name
    bootstrap_stderr = None
    if user_plist.exists():
        try:
            # `migrate()` (jobs/cli.py:204) uses `bootout` to remove the agent,
            # so at rollback time it is unloaded. `kickstart -k` only restarts
            # an already-loaded service — bootstrap is what actually re-loads
            # from the (renamed-back) plist. Run bootout first as an idempotent
            # guard against the rare case where it is loaded.
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                check=False, capture_output=True,
            )
            bs = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
                check=False, capture_output=True, text=True,
            )
            if bs.returncode != 0:
                bootstrap_stderr = (bs.stderr or "").strip()[:200]
        except FileNotFoundError:
            pass
    incident_evidence = {**evidence, "bootstrap_stderr": bootstrap_stderr} if bootstrap_stderr else evidence
    log_incident("migration_rollback", kind=migration["kind"], reason=reason, evidence=incident_evidence)


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
    grace_skips: list[str] = []
    now = datetime.now(timezone.utc)

    for kind, m in list(in_flight.items()):
        m["last_check"] = now.isoformat()

        # Manual halt — operator paused this kind. Don't act on it.
        if m.get("halted"):
            continue

        cadence_s = int(m.get("cadence_seconds") or 3600)
        started_at = datetime.fromisoformat(m["started_at"])
        elapsed_s = (now - started_at).total_seconds()
        grace_s = cadence_s * 2

        # 1. Did it fire? Skip in grace; cadence × 1.5 once we're past grace.
        last_fire_iso = m.get("last_fire", "")
        if last_fire_iso:
            try:
                last_fire = datetime.fromisoformat(last_fire_iso)
                gap = (now - last_fire).total_seconds()
            except ValueError:
                gap = float("inf")
        else:
            gap = elapsed_s
        if elapsed_s >= grace_s and gap > cadence_s * 1.5 and gap > 600:
            evidence = {"gap_seconds": int(gap), "cadence_seconds": cadence_s}
            rollback(m, reason="no_fire", evidence=evidence)
            state.setdefault("rolled_back", []).append({**m, "reason": "no_fire", "evidence": evidence, "at": now.isoformat()})
            del in_flight[kind]
            rolled_back_now.append(kind)
            continue

        # 2. Baseline check.
        passed, evidence = check_baseline(
            m["baseline_metric"],
            m["divergence_window"],
            cadence_s,
            m.get("baseline_snapshot"),
            elapsed_s,
        )
        if not passed:
            rollback(m, reason="baseline_diverged", evidence=evidence)
            state.setdefault("rolled_back", []).append({**m, "reason": "baseline_diverged", "evidence": evidence, "at": now.isoformat()})
            del in_flight[kind]
            rolled_back_now.append(kind)
            continue

        # 3. Soak counter — only ticks for genuinely-healthy checks, not grace.
        if evidence.get("reason") == "first_fire_grace":
            grace_skips.append(kind)
            continue
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
        "grace_skips": grace_skips,
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
