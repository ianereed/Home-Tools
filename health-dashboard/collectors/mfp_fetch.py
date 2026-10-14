"""Fetch MyFitnessPal daily nutrition and print JSON. Runs in the MFP venv, not the dashboard venv.

    <mfp-venv>/bin/python -I collectors/mfp_fetch.py --start YYYY-MM-DD --end YYYY-MM-DD

Uses mfp-mcp's authenticated client (session cookie + Chrome TLS fingerprint, optional
headless auto-refresh). MFP's v2 diary endpoint errors for this account, so day totals
come from the web nutrition reports (one request per nutrient for the whole range) and
the diary page tells us which days actually have entries. Standalone: stdlib + mfp-mcp only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

# MFP report name -> nutrition_daily column
REPORTS = {
    "Calories": "calories_kcal", "Protein": "protein_g", "Carbs": "carbs_g", "Fat": "fat_g",
    "Saturated Fat": "saturated_fat_g", "Fiber": "fiber_g", "Sugar": "sugar_g",
    "Sodium": "sodium_mg", "Potassium": "potassium_mg", "Cholesterol": "cholesterol_mg",
}
# Nutrients many MFP database foods simply omit: a 0 total means "unknown", not zero.
ZERO_MEANS_UNKNOWN = {"potassium_mg"}


def build_days(reports: dict[str, dict[str, float]], entry_counts: dict[str, int]) -> dict[str, dict]:
    """Combine per-nutrient report series with diary entry counts.

    Days with no diary entries are 'not logged' (MFP reports them as 0, which would
    fake a zero-intake day). A report that failed is absent -> column None.
    """
    days = {}
    for ds, n in sorted(entry_counts.items()):
        if not n:
            days[ds] = {"logged": False, "entries": 0}
            continue
        row = {"logged": True, "entries": n}
        for name, col in REPORTS.items():
            series = reports.get(name)
            val = None if series is None else series.get(ds)
            if val is not None and col in ZERO_MEANS_UNKNOWN and float(val) == 0:
                val = None
            row[col] = None if val is None else float(val)
        days[ds] = row
    return days


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
    for attempt in (1, 2):
        rc, out, retry = _fetch(start, end, get_client, is_auth_error, attempt == 1)
        if not retry:
            print(json.dumps(out))
            return rc
        from myfitnesspal_mcp import refresh  # noqa
        refresh.refresh_session()   # MFP rotated the session: renew it in the headless profile, retry once
    print(json.dumps(out))
    return rc


def _fetch(start, end, get_client, is_auth_error, can_retry):
    """-> (rc, json_out, retry_after_refresh)"""
    try:
        client = get_client()
        counts = {}
        d = start
        while d <= end:
            day = client.get_date(d)
            counts[d.isoformat()] = sum(len(m.entries) for m in day.meals)
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
        return 0, {"ok": True, "days": build_days(reports, counts), "report_errors": errors}, False
    except Exception as e:
        auth = "NotConnected" in type(e).__name__
        if not isinstance(e, (TypeError, AttributeError, KeyError, ValueError, NameError)):
            try:
                auth = auth or bool(is_auth_error(e))
            except Exception:
                pass
        if auth and can_retry:
            try:
                from myfitnesspal_mcp import refresh  # noqa
                if refresh.available() and refresh.profile_seeded():
                    return 2, None, True
            except Exception:
                pass
        return 2, {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "auth": auth}, False


if __name__ == "__main__":
    sys.exit(main())
