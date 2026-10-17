"""Fetch MyFitnessPal daily nutrition and print JSON. Runs in the MFP venv, not the dashboard venv.

    <mfp-venv>/bin/python -I collectors/mfp_fetch.py --start YYYY-MM-DD --end YYYY-MM-DD

Uses mfp-mcp's authenticated client (session cookie + Chrome TLS fingerprint). MFP's v2 diary
endpoint errors for this account, so day totals come from the web nutrition reports (one request
per nutrient for the whole range) and the diary page tells us which days actually have entries.
After each run the rotated session cookie is written back (see mfp_session.py); the headless
auto-refresh is deliberately not used. Standalone: stdlib + mfp-mcp only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    from . import mfp_session
except ImportError:  # run as a script under -I: the package isn't on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mfp_session

# MFP report name -> nutrition_daily column
REPORTS = {
    "Calories": "calories_kcal", "Protein": "protein_g", "Carbs": "carbs_g", "Fat": "fat_g",
    "Saturated Fat": "saturated_fat_g", "Fiber": "fiber_g", "Sugar": "sugar_g",
    "Sodium": "sodium_mg", "Potassium": "potassium_mg", "Cholesterol": "cholesterol_mg",
}
SWEAT_NAME_MARK = "Sweat sodium loss"  # the planner's negative-sodium entries (mfp_sweat.py)
# Nutrients many MFP database foods simply omit: a 0 total means "unknown", not zero.
ZERO_MEANS_UNKNOWN = {"potassium_mg"}


def build_days(reports: dict[str, dict[str, float]], entry_counts: dict[str, int],
               sweat_mg: dict[str, float] | None = None) -> dict[str, dict]:
    """Combine per-nutrient report series with diary entry counts.

    Days with no diary entries are 'not logged' (MFP reports them as 0, which would
    fake a zero-intake day). A report that failed is absent -> column None.
    `entry_counts` excludes the planner's own sweat-sodium entries, so a ride day with no
    food logged stays 'not logged'; `sweat_mg` is those entries' sodium as a positive number
    (the Sodium report is already net of it).
    """
    sweat_mg = sweat_mg or {}
    days = {}
    for ds, n in sorted(entry_counts.items()):
        if not n:
            days[ds] = {"logged": False, "entries": 0}
            continue
        row = {"logged": True, "entries": n, "sweat_sodium_mg": sweat_mg.get(ds)}
        for name, col in REPORTS.items():
            series = reports.get(name)
            val = None if series is None else series.get(ds)
            if val is not None and col in ZERO_MEANS_UNKNOWN and float(val) == 0:
                val = None
            row[col] = None if val is None else float(val)
        days[ds] = row
    return days


def split_sweat(entries) -> tuple[int, float]:
    """-> (food entry count without the sweat entries, sweat sodium mg as a positive number)."""
    n, mg = 0, 0.0
    for e in entries:
        if SWEAT_NAME_MARK in (getattr(e, "name", "") or ""):
            mg -= float((e.nutrition_information or {}).get("sodium") or 0)
        else:
            n += 1
    return n, mg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args(argv)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    try:
        from myfitnesspal_mcp.mfp_client import get_client, is_auth_error  # noqa
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"mfp-mcp not importable: {e}", "auth": False}))
        return 2
    rc, out = _fetch(start, end, get_client, is_auth_error)
    print(json.dumps(out))
    return rc


def _fetch(start, end, get_client, is_auth_error):
    """-> (rc, json_out). 'session' reports whether the rotated cookie was written back."""
    client = None
    try:
        client = get_client()
        counts, sweat = {}, {}
        d = start
        while d <= end:
            day = client.get_date(d)
            counts[d.isoformat()], mg = split_sweat([e for m in day.meals for e in m.entries])
            if mg:
                sweat[d.isoformat()] = mg
            d += timedelta(days=1)
        reports, errors = {}, {}
        for name in REPORTS:
            try:
                # lower_bound is exclusive in MFP's report API (probed 2026-10-14)
                rep = client.get_report(report_name=name, report_category="Nutrition",
                                        lower_bound=start - timedelta(days=1), upper_bound=end)
                reports[name] = {str(k): v for k, v in rep.items()}
            except Exception as e:  # one nutrient failing shouldn't lose the day
                errors[name] = f"{type(e).__name__}: {str(e)[:120]}"
        return 0, {"ok": True, "days": build_days(reports, counts, sweat), "report_errors": errors,
                   "session": mfp_session.persist_rotated(client)}
    except Exception as e:
        auth = "NotConnected" in type(e).__name__
        if not isinstance(e, (TypeError, AttributeError, KeyError, ValueError, NameError)):
            try:
                auth = auth or bool(is_auth_error(e))
            except Exception:
                pass
        session = mfp_session.persist_rotated(client) if client is not None else "no client"
        return 2, {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "auth": auth, "session": session}


if __name__ == "__main__":
    sys.exit(main())
