"""Migration of health-dashboard intervals-poll — every 5 min."""
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


@huey.periodic_task(crontab(minute="*/5"))
@requires(["db:health-dashboard/data/health.db", "fs:health-dashboard"])
@baseline(metric="db-mtime:health-dashboard/data/health.db", divergence_window="6m")
def health_intervals_poll() -> dict:
    proc = subprocess.run(
        [str(VENV_PYTHON), "-m", "collectors.intervals_collector", "--days", "14"],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=300,
    )
    record_fire("health_intervals_poll")
    if proc.returncode != 0:
        logger.warning("health.intervals-poll rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    return {"rc": proc.returncode}
