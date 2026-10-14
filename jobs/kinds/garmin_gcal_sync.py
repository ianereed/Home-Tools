"""Garmin calendar → Google Calendar mirror — health-dashboard `python -m nutrition.gcal_workouts`.

Runs three times a day (07:10, 13:10, 19:10 local) so Coach re-plans and new lifts reach Google
Calendar the same day, plus whenever nutrition_watch sees a new Garmin activity (Coach re-plans
after a ride). huey cron runs in UTC, so each kind fires at both possible UTC hours and gates on
the local hour inside the body (DST-safe), like nutrition_refresh.
"""
from __future__ import annotations

import json
import logging
import subprocess

from huey import crontab

from jobs import huey, requires
from jobs.kinds.nutrition_refresh import LOG_DIR, PROJECT, VENV_PYTHON, _notify, local_now

logger = logging.getLogger(__name__)

FAILS_FILE = LOG_DIR / "garmin_gcal_sync.failures"
TIMEOUT_S = 300


def _fail_count(delta: int | None) -> int:
    LOG_DIR.mkdir(exist_ok=True)
    if delta is None:
        FAILS_FILE.write_text("0")
        return 0
    try:
        n = int(FAILS_FILE.read_text().strip() or 0) + delta
    except (FileNotFoundError, ValueError):
        n = delta
    FAILS_FILE.write_text(str(n))
    return n


@huey.task(retries=1, retry_delay=600)
@requires(["db:health-dashboard/data/health.db", "fs:health-dashboard", "fs:event-aggregator"])
def garmin_gcal_sync(reason: str = "manual") -> dict:
    LOG_DIR.mkdir(exist_ok=True)
    proc = subprocess.run([str(VENV_PYTHON), "-m", "nutrition.gcal_workouts", "--print"],
                          cwd=str(PROJECT), capture_output=True, text=True, timeout=TIMEOUT_S)
    (LOG_DIR / "garmin_gcal_sync.log").write_text(
        f"{local_now().isoformat(timespec='seconds')} reason={reason} rc={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr[-6000:]}\n")
    if proc.returncode != 0:
        n = _fail_count(1)
        if n == 2:  # one blip is fine; two in a row means something is actually broken
            _notify("Garmin → Google Calendar sync failing",
                    f"nutrition.gcal_workouts rc={proc.returncode} ({n}x in a row). "
                    f"See health-dashboard/logs/garmin_gcal_sync.log\n\n{proc.stderr[-500:]}")
        raise RuntimeError(f"garmin_gcal_sync failed rc={proc.returncode}")
    _fail_count(None)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    out = json.loads(lines[-1]) if lines else {"ok": True}
    return {k: out.get(k) for k in ("ok", "mode", "skipped", "desired", "existing", "duration_ms")} | \
        {"ops": len(out.get("ops") or [])}


def _gate(hour: int, reason: str) -> dict:
    if local_now().hour != hour:
        return {"skipped": f"not {hour:02d}:10 local"}
    garmin_gcal_sync(reason)
    return {"enqueued": True}


@huey.periodic_task(crontab(minute="10", hour="14,15"))  # 07:10 local (PDT 14:10 UTC / PST 15:10 UTC)
def garmin_gcal_sync_morning() -> dict:
    return _gate(7, "morning")


@huey.periodic_task(crontab(minute="10", hour="20,21"))  # 13:10 local
def garmin_gcal_sync_midday() -> dict:
    return _gate(13, "midday")


@huey.periodic_task(crontab(minute="10", hour="2,3"))  # 19:10 local
def garmin_gcal_sync_evening() -> dict:
    return _gate(19, "evening")
