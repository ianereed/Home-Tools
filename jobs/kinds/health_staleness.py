"""Migration of health-dashboard staleness check — 07:00 + 21:00."""
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


@huey.periodic_task(crontab(minute="0", hour="7,21"))
@requires(["db:health-dashboard/data/health.db", "fs:health-dashboard"])
@baseline(metric="file-mtime:logs/health-staleness.log", divergence_window="20m")
def health_staleness() -> dict:
    proc = subprocess.run(
        [str(VENV_PYTHON), "-m", "collectors.staleness_check"],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=120,
    )
    record_fire("health_staleness")
    if proc.returncode != 0:
        logger.warning("health.staleness rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    return {"rc": proc.returncode}
