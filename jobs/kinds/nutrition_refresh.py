"""Fuel planner refresh — health-dashboard `python -m nutrition.refresh`.

Enqueued by nutrition_watch when a new Garmin activity syncs (or the dashboard's
"Refresh now" flag is set), plus a 07:30 and a 19:30 local refresh for Coach
re-plans and calendar changes. huey cron runs in UTC, so the daily kinds fire at
both possible UTC hours and gate on the local hour inside the body (DST-safe).
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from huey import crontab

from jobs import huey, requires

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parents[2] / "health-dashboard"
VENV_PYTHON = PROJECT / ".venv" / "bin" / "python3"
LOG_DIR = PROJECT / "logs"
FAILS_FILE = LOG_DIR / "nutrition_refresh.failures"
NTFY_TOPIC = "ian-health-dashboard"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
TIMEOUT_S = 600


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def _notify(title: str, body: str) -> None:
    try:
        subprocess.run(["curl", "-s", "-H", f"Title: {title}", "-H", "Tags: warning", "-d", body,
                        f"https://ntfy.sh/{NTFY_TOPIC}"], capture_output=True, timeout=15)
    except Exception:
        logger.exception("ntfy notification failed")


def _fail_count(delta: int | None) -> int:
    """delta=None resets; otherwise increments and returns the new count."""
    LOG_DIR.mkdir(exist_ok=True)
    if delta is None:
        FAILS_FILE.write_text("0")
        return 0
    try:
        n = int(FAILS_FILE.read_text().strip() or 0) + delta
    except (FileNotFoundError, ValueError):
        n = delta
    FAILS_FILE.write_text(str(n))
    return n


@huey.task(retries=1, retry_delay=300)
@requires(["db:health-dashboard/data/health.db", "fs:health-dashboard"])
def nutrition_refresh(reason: str = "manual", trigger: str = "job") -> dict:
    LOG_DIR.mkdir(exist_ok=True)
    proc = subprocess.run(
        [str(VENV_PYTHON), "-m", "nutrition.refresh", "--reason", reason, "--trigger", trigger, "--print"],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=TIMEOUT_S,
    )
    (LOG_DIR / "nutrition_refresh.log").write_text(
        f"{local_now().isoformat(timespec='seconds')} reason={reason} rc={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr[-6000:]}\n")
    if proc.returncode != 0:
        n = _fail_count(1)
        if n == 2:  # one blip is fine; two in a row means something is actually broken
            _notify("Fuel planner refresh failing", f"nutrition.refresh rc={proc.returncode} ({n}x in a row). "
                    f"See health-dashboard/logs/nutrition_refresh.log\n\n{proc.stderr[-500:]}")
        raise RuntimeError(f"nutrition_refresh failed rc={proc.returncode}")
    _fail_count(None)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    out = json.loads(lines[-1]) if lines else {"ok": True}
    if (out.get("sources") or {}).get("mfp") == "auth_expired":
        _notify("MyFitnessPal session expired", "The fuel planner can't read MFP. Re-run the mfp-mcp auth step on the Mac mini.")
    return {k: out.get(k) for k in ("ok", "run_id", "duration_ms", "sources", "skipped")}


@huey.periodic_task(crontab(minute="30", hour="14,15"))  # 07:30 local in PDT (14:30 UTC) and PST (15:30 UTC)
def nutrition_daily_refresh() -> dict:
    if local_now().hour != 7:
        return {"skipped": "not 07:30 local"}
    nutrition_refresh("daily", "daily")
    return {"enqueued": True}


@huey.periodic_task(crontab(minute="30", hour="2,3"))  # 19:30 local in PDT (02:30 UTC) and PST (03:30 UTC)
def nutrition_evening_refresh() -> dict:
    if local_now().hour != 19:
        return {"skipped": "not 19:30 local"}
    nutrition_refresh("evening", "daily")
    return {"enqueued": True}
