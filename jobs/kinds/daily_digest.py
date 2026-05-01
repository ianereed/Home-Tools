"""Migration of Mac-mini/scripts/daily-digest.py — 07:00 daily.

Posts the Slack digest in the existing format. Verifier checks for a Slack
message in #ian-event-aggregator between 06:55 and 07:05 (file-mtime on
the slack-post.sh log proxies for "did it fire").
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

SCRIPT = Path(__file__).resolve().parents[2] / "Mac-mini" / "scripts" / "daily-digest.py"


@huey.periodic_task(crontab(minute="0", hour="7"))
@requires(["secret:SLACK_BOT_TOKEN", "fs:logs"])
@baseline(metric="file-mtime:logs/daily-digest.log", divergence_window="20m")
def daily_digest() -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=120)
    record_fire("daily_digest")
    if proc.returncode != 0:
        logger.warning("daily-digest rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    return {"rc": proc.returncode}
