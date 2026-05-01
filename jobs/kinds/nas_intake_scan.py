"""Migration of nas-intake/watcher.py — every 5 min."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, requires
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[2] / "nas-intake" / "watcher.py"


@huey.periodic_task(crontab(minute="*/5"))
@requires(["fs:nas-intake"])
@baseline(metric="file-mtime:nas-intake/state.json", divergence_window="6m")
def nas_intake_scan() -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=600)
    record_fire("nas_intake_scan")
    if proc.returncode != 0:
        logger.warning("nas-intake-watcher rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    return {"rc": proc.returncode}
