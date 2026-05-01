"""Migration of Mac-mini/scripts/heartbeat.py — every 30 min.

Thin wrapper: invokes the existing script via subprocess so the Job's
behavior is byte-identical to the LaunchAgent version. The migration_verifier
checks `incidents.jsonl-mtime` (the script's own output) within a 35m window.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, requires
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[2] / "Mac-mini" / "scripts" / "heartbeat.py"


@huey.periodic_task(crontab(minute="*/30"))
@requires(["fs:logs", "fs:run"])
@baseline(metric="incidents.jsonl-mtime", divergence_window="35m")
def heartbeat() -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=300)
    record_fire("heartbeat")
    if proc.returncode != 0:
        logger.warning("heartbeat exited %d: %s", proc.returncode, proc.stderr[:200])
    return {"rc": proc.returncode, "stdout_tail": proc.stdout.splitlines()[-1:] if proc.stdout else []}
