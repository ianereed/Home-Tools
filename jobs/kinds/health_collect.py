"""Migration of health-dashboard com.health-dashboard.collect — 07:00 + 07:20.

The original plist runs `python3 -m collectors.collect_all` from the
health-dashboard project directory using its own venv. We invoke that same
venv-python so dependencies (garmin/strava libs) come from the right place.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, requires
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parents[2] / "health-dashboard"
VENV_PYTHON = PROJECT / ".venv" / "bin" / "python3"


@huey.periodic_task(crontab(minute="0,20", hour="7"))
@requires(["db:health-dashboard/data/health.db", "fs:health-dashboard"])
@baseline(metric="db-mtime:health-dashboard/data/health.db", divergence_window="35m")
def health_collect() -> dict:
    proc = subprocess.run(
        [str(VENV_PYTHON), "-m", "collectors.collect_all"],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=900,
    )
    record_fire("health_collect")
    if proc.returncode != 0:
        logger.warning("health.collect rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    return {"rc": proc.returncode}
