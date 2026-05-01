"""Migration of com.home-tools.restic-hourly — every :17."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, requires
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[2] / "Mac-mini" / "scripts" / "restic-backup.py"


@huey.periodic_task(crontab(minute="17"))
@requires(["bin:restic", "fs:Mac-mini/scripts"])
@baseline(metric="restic-snapshot-count:restic-hourly", divergence_window="80m")
def restic_hourly() -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPT), "--profile", "hourly"],
                          capture_output=True, text=True, timeout=900)
    record_fire("restic_hourly")
    if proc.returncode != 0:
        logger.warning("restic-hourly rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    return {"rc": proc.returncode}
