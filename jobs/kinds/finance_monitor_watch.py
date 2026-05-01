"""Migration of finance-monitor/watcher.py — every 5 min."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, requires
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[2] / "finance-monitor" / "watcher.py"


@huey.periodic_task(crontab(minute="*/5"))
@requires(["db:finance-monitor/finance.db", "fs:finance-monitor"])
@baseline(metric="db-mtime:finance-monitor/finance.db", divergence_window="6m")
def finance_monitor_watch() -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=240)
    record_fire("finance_monitor_watch")
    if proc.returncode != 0:
        logger.warning("finance-monitor-watcher rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    return {"rc": proc.returncode}
