"""Migration of health-dashboard staleness check — 07:00 + 21:00.

Runs the project's staleness check (which itself pushes an ntfy alert when data
is stale). The check writes a heartbeat to logs/health-staleness.log on every
run — that file's mtime is this kind's migration baseline metric.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, migrates_from, requires
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parents[2] / "health-dashboard"
VENV_PYTHON = PROJECT / ".venv" / "bin" / "python3"
LOG_DIR = PROJECT / "logs"


@huey.periodic_task(crontab(minute="0", hour="7,21"))
@requires(["db:health-dashboard/data/health.db", "fs:health-dashboard"])
@baseline(metric="file-mtime:logs/health-staleness.log", divergence_window="20m", cadence="12h")
@migrates_from("com.health-dashboard.staleness")
def health_staleness() -> dict:
    LOG_DIR.mkdir(exist_ok=True)
    proc = subprocess.run(
        [str(VENV_PYTHON), "-m", "collectors.staleness_check"],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        # The checker itself broke (distinct from "data is stale", which is a
        # normal rc=0 outcome that fires its own ntfy). Surface the full error.
        logger.warning("health.staleness rc=%d stderr=%s", proc.returncode, proc.stderr)
        raise RuntimeError(f"health_staleness failed rc={proc.returncode}: {proc.stderr[-400:]}")
    record_fire("health_staleness")
    return {"rc": proc.returncode}
