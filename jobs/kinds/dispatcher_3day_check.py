"""Migration of Mac-mini/scripts/dispatcher-3day-check.sh — every 3 days."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, requires
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[2] / "Mac-mini" / "scripts" / "dispatcher-3day-check.sh"
_LOG = Path(__file__).resolve().parents[2] / "logs" / "dispatcher-3day.txt"


# Every 3 days at 09:30. crontab doesn't support "every 3 days" directly, so
# we anchor to day_of_month at known offsets.
@huey.periodic_task(crontab(minute="30", hour="9", day="1,4,7,10,13,16,19,22,25,28"))
@requires(["bin:bash", "fs:logs"])
@baseline(metric="file-mtime:logs/dispatcher-3day.txt", divergence_window="80h", cadence="3d")
def dispatcher_3day_check() -> dict:
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=300)
    record_fire("dispatcher_3day_check")
    if proc.returncode != 0:
        logger.warning("dispatcher-3day-check rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    _LOG.write_text(f"rc={proc.returncode}\n")
    return {"rc": proc.returncode}
