"""Collect MyFitnessPal daily nutrition into nutrition_daily (source='mfp').

mfp-mcp (and its curl_cffi / Playwright dependencies) lives in its own venv; this module
runs collectors/mfp_fetch.py there and upserts the JSON it prints. If that venv isn't
installed the collector is a silent no-op (Standing rule 6).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from .db import get_connection

logger = logging.getLogger(__name__)

FETCH_SCRIPT = Path(__file__).with_name("mfp_fetch.py")
DEFAULT_MFP_PYTHON = "~/.fuel-planner/mfp-venv/bin/python"
NUTRIENT_COLS = ("calories_kcal", "protein_g", "carbs_g", "fat_g", "saturated_fat_g", "fiber_g",
                 "sugar_g", "sodium_mg", "potassium_mg", "cholesterol_mg")


class MFPAuthError(RuntimeError):
    """The saved MFP session no longer works; Ian has to re-run `mfp-mcp auth`."""


def mfp_python() -> Path:
    return Path(os.path.expanduser(os.environ.get("FUEL_MFP_PYTHON", DEFAULT_MFP_PYTHON)))


def fetch(start: str, end: str, timeout: int = 240) -> dict:
    proc = subprocess.run([str(mfp_python()), "-I", str(FETCH_SCRIPT), "--start", start, "--end", end],
                          capture_output=True, text=True, timeout=timeout)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"mfp_fetch printed no JSON (rc={proc.returncode}): {proc.stderr[-300:]}")
    out = json.loads(lines[-1])
    if not out.get("ok"):
        if out.get("auth"):
            raise MFPAuthError(out.get("error", "MFP session expired"))
        raise RuntimeError(out.get("error", "mfp_fetch failed"))
    return out


def upsert(conn, days: dict[str, dict], now: str | None = None) -> tuple[int, int]:
    """Write logged days; remove the mfp row for days that are no longer logged. Never zero-fills."""
    now = now or datetime.now().isoformat(timespec="seconds")
    written = deleted = 0
    for ds, row in days.items():
        if not row.get("logged"):
            cur = conn.execute("DELETE FROM nutrition_daily WHERE date = ? AND source = 'mfp'", (ds,))
            deleted += cur.rowcount
            continue
        cols = ", ".join(("date",) + NUTRIENT_COLS + ("source", "updated_at"))
        marks = ", ".join("?" for _ in range(len(NUTRIENT_COLS) + 3))
        conn.execute(f"INSERT OR REPLACE INTO nutrition_daily ({cols}) VALUES ({marks})",
                     (ds, *[row.get(c) for c in NUTRIENT_COLS], "mfp", now))
        written += 1
    conn.commit()
    return written, deleted


def collect_mfp(days_back: int = 7):
    if not mfp_python().exists():
        logger.info("MyFitnessPal venv not installed (%s); skipping", mfp_python())
        return
    end = date.today()
    start = end - timedelta(days=days_back)
    out = fetch(start.isoformat(), end.isoformat())
    conn = get_connection()
    try:
        written, deleted = upsert(conn, out["days"])
    finally:
        conn.close()
    if out.get("report_errors"):
        logger.warning("MFP report errors: %s", out["report_errors"])
    logger.info("Saved MFP nutrition: %d day(s) written, %d removed", written, deleted)
