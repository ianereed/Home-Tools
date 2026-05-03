"""Migration of Mac-mini/scripts/weekly-ssh-digest.sh — Mon 09:00."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, requires
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parents[2] / "Mac-mini" / "scripts" / "weekly-ssh-digest.sh"
_LOG = Path(__file__).resolve().parents[2] / "logs" / "weekly-ssh-digest.log"


@huey.periodic_task(crontab(minute="0", hour="9", day_of_week="1"))
@requires(["secret:SLACK_BOT_TOKEN", "bin:bash"])
@baseline(metric="file-mtime:logs/weekly-ssh-digest.log", divergence_window="20m", cadence="7d")
def weekly_ssh_digest() -> dict:
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=300)
    record_fire("weekly_ssh_digest")
    if proc.returncode != 0:
        logger.warning("weekly-ssh-digest rc=%d stderr=%s", proc.returncode, proc.stderr[:200])
    _LOG.write_text(f"rc={proc.returncode}\n")
    return {"rc": proc.returncode}
