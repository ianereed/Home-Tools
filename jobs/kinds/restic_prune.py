"""Migration of com.home-tools.restic-prune — Sun 04:00."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, requires
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[2] / "Mac-mini" / "scripts" / "restic-prune.py"


@huey.periodic_task(crontab(minute="0", hour="4", day_of_week="0"))
@requires(["bin:restic", "fs:Mac-mini/scripts"])
@baseline(metric="file-mtime:logs/restic-prune.log", divergence_window="8d")
def restic_prune() -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPT)],
                          capture_output=True, text=True, timeout=7200)
    record_fire("restic_prune")
    if proc.returncode != 0:
        logger.warning("restic-prune rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    return {"rc": proc.returncode}
