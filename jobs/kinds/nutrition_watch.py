"""Fuel planner watcher — has a new Garmin activity synced? Every 15 min, 06:00–22:00 local.

One cheap Garmin call (`python -m nutrition.watch`). When it reports a change, mark the
enqueue time (debounce) and enqueue nutrition_refresh.
"""
from __future__ import annotations

import json
import logging
import subprocess

from huey import crontab

from jobs import huey, requires
from jobs.kinds.nutrition_refresh import PROJECT, VENV_PYTHON, local_now, nutrition_refresh
from jobs.kinds.garmin_gcal_sync import garmin_gcal_sync

logger = logging.getLogger(__name__)

WATCH_HOURS = range(6, 22)


def _run(args: list[str], timeout: int = 90):
    return subprocess.run([str(VENV_PYTHON), "-m", "nutrition.watch", *args], cwd=str(PROJECT),
                          capture_output=True, text=True, timeout=timeout)


@huey.periodic_task(crontab(minute="*/15"))
@requires(["db:health-dashboard/data/health.db", "fs:health-dashboard"])
def nutrition_watch() -> dict:
    if local_now().hour not in WATCH_HOURS:
        return {"skipped": "outside watch hours"}
    proc = _run([])
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if proc.returncode != 0 or not lines:
        # A Garmin hiccup on one tick isn't worth an alert; the next tick retries.
        logger.warning("nutrition.watch rc=%s stderr=%s", proc.returncode, proc.stderr[-400:])
        return {"ok": False, "rc": proc.returncode}
    out = json.loads(lines[-1])
    if out.get("changed"):
        _run(["--mark-enqueued"], timeout=30)
        nutrition_refresh(out.get("reason", "watch"), "watch")
        garmin_gcal_sync("watch")  # Coach re-plans after a ride; mirror it to Google Calendar too
        out["enqueued"] = True
    return out
