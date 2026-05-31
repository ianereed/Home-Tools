"""Migration of health-dashboard com.health-dashboard.collect — 07:00 + 07:20.

The original plist runs `python3 -m collectors.collect_all` from the
health-dashboard project directory using its own venv. We invoke that same
venv-python so dependencies (garmin/strava libs) come from the right place.
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
NTFY_TOPIC = "ian-health-dashboard"


def _notify(title: str, body: str) -> None:
    """Best-effort ntfy push. Collection failures are the primary 'something
    broke' signal now that the dashboard isn't watched daily."""
    try:
        subprocess.run(
            ["curl", "-s", "-H", f"Title: {title}", "-H", "Priority: high",
             "-H", "Tags: rotating_light", "-d", body, f"https://ntfy.sh/{NTFY_TOPIC}"],
            capture_output=True, timeout=15,
        )
    except Exception:
        logger.exception("ntfy notification failed")


@huey.periodic_task(crontab(minute="0,20", hour="7"))
@requires(["db:health-dashboard/data/health.db", "fs:health-dashboard"])
@baseline(metric="db-mtime:health-dashboard/data/health.db", divergence_window="35m", cadence="12h")
@migrates_from("com.health-dashboard.collect")
def health_collect() -> dict:
    LOG_DIR.mkdir(exist_ok=True)
    proc = subprocess.run(
        [str(VENV_PYTHON), "-m", "collectors.collect_all"],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=900,
    )
    # Persist the full run output — kinds otherwise discard it, which left us
    # blind when collection silently failed.
    (LOG_DIR / "collect.log").write_text(
        f"{proc.returncode=}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
    )
    if proc.returncode != 0:
        logger.warning("health.collect rc=%d stderr=%s", proc.returncode, proc.stderr)
        _notify("Health collection FAILED",
                f"collect_all exited rc={proc.returncode}.\n\n{proc.stderr[-800:]}")
        # Raise so huey records an error and record_fire is NOT called — the
        # verifier/daily-digest should see a failed run, not a successful one.
        raise RuntimeError(f"health_collect failed rc={proc.returncode}")
    record_fire("health_collect")
    return {"rc": proc.returncode}
