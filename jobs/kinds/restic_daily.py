"""Migration of com.home-tools.restic-daily — 03:30 daily."""
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
_HEARTBEAT_LOG = Path.home() / "Library" / "Logs" / "home-tools" / "restic-daily.log"


@huey.periodic_task(crontab(minute="30", hour="3"))
@requires(["bin:restic", "fs:Mac-mini/scripts"])
@baseline(metric="restic-snapshot-count:restic-daily", divergence_window="25h", cadence="1d")
def restic_daily() -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPT), "--profile", "daily"],
                          capture_output=True, text=True, timeout=3600)
    record_fire("restic_daily")
    if proc.returncode != 0:
        logger.warning("restic-daily rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    _HEARTBEAT_LOG.write_text(f"rc={proc.returncode}\n")
    return {"rc": proc.returncode}
