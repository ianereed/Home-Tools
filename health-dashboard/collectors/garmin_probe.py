"""Read-only probe: confirm the Garmin token works and find how far back data goes.

Does NOT write to the DB. Run on homeserver where the token store lives:
  KEYCHAIN_PATH=.../login.keychain-db .venv/bin/python3 -m collectors.garmin_probe

--cardio mode probes the blood-pressure / weigh-in / body-composition endpoints
that back the cardio project (see health-dashboard/CARDIO_PLAN.md Appendix D) —
it never writes to the DB either, just reports payload shapes.
"""
import argparse
import datetime
import importlib.metadata
import logging
import time

from .garmin_collector import _get_garmin_client

logging.basicConfig(level=logging.WARNING)

PROBE_DATES = ["2019-06-15", "2020-06-15", "2021-06-15", "2022-06-15",
               "2023-06-15", "2024-06-15", "2025-06-15"]

CARDIO_METHODS = ["get_blood_pressure", "get_weigh_ins", "get_daily_weigh_ins",
                   "get_body_composition"]


def _shape(value, depth=0, max_depth=6):
    """Describe a payload's structure — keys and types, values truncated.

    Never prints a raw leaf value: numbers/strings collapse to their type and
    (for strings) length, so nothing PHI-shaped (a real BP/weight reading)
    survives into probe output copy-pasted into a journal.
    """
    if depth >= max_depth:
        return "..."
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return "[] (empty)"
        return [f"list[{len(value)}] of ->", _shape(value[0], depth + 1, max_depth)]
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return type(value).__name__
    if isinstance(value, str):
        return f"str(len={len(value)})"
    return type(value).__name__


def cardio_main():
    try:
        version = importlib.metadata.version("garminconnect")
    except importlib.metadata.PackageNotFoundError:
        version = "UNKNOWN"
    print(f"garminconnect version: {version}")

    c = _get_garmin_client()
    print("LOGIN OK")

    print("\nhasattr checks:")
    for m in CARDIO_METHODS:
        print(f"  {m}: {hasattr(c, m)}")

    end = datetime.date.today()
    start = end - datetime.timedelta(days=365)
    start_s, end_s = start.isoformat(), end.isoformat()
    print(f"\nrange queried: {start_s} .. {end_s}")

    print("\nget_blood_pressure(startdate, enddate) shape:")
    bp = safe("bp", lambda: c.get_blood_pressure(start_s, end_s))
    print(" ", _shape(bp) if not isinstance(bp, str) else bp)

    print("\nget_body_composition(startdate, enddate) shape:")
    comp = safe("comp", lambda: c.get_body_composition(start_s, end_s))
    print(" ", _shape(comp) if not isinstance(comp, str) else comp)


def safe(label, fn):
    try:
        return fn()
    except Exception as e:
        return f"ERR({type(e).__name__}: {str(e)[:60]})"


def main():
    c = _get_garmin_client()
    print("LOGIN OK")
    try:
        print("user:", c.get_full_name())
    except Exception as e:
        print("user: ERR", e)

    # earliest activity overall — one range query is what the backfill will use.
    try:
        acts = c.get_activities_by_date("2018-01-01", "2026-12-31") or []
        if acts:
            dates = sorted(a.get("startTimeLocal", "")[:10] for a in acts if a.get("startTimeLocal"))
            byyear = {}
            for d in dates:
                byyear[d[:4]] = byyear.get(d[:4], 0) + 1
            print(f"activities: {len(acts)} total, earliest={dates[0]}, latest={dates[-1]}")
            print("  per-year:", dict(sorted(byyear.items())))
        else:
            print("activities: none")
    except Exception as e:
        print("activities: ERR", e)

    print("\nper-date availability (sleep secs / resting HR / VO2max):")
    for d in PROBE_DATES:
        sleep = safe("sleep", lambda: (c.get_sleep_data(d) or {}).get("dailySleepDTO", {}).get("sleepTimeSeconds"))
        time.sleep(0.8)
        rhr = safe("rhr", lambda: (c.get_heart_rates(d) or {}).get("restingHeartRate"))
        time.sleep(0.8)
        vo2 = safe("vo2", lambda: _vo2(c, d))
        time.sleep(0.8)
        print(f"  {d}: sleep_secs={sleep}  resting_hr={rhr}  vo2max={vo2}")


def _vo2(c, d):
    m = c.get_max_metrics(d)
    if isinstance(m, list) and m:
        gen = (m[0].get("generic") or {})
        return gen.get("vo2MaxPreciseValue") or gen.get("vo2MaxValue")
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cardio", action="store_true",
                         help="probe BP / weigh-in / body-composition endpoints")
    args = parser.parse_args()
    if args.cardio:
        cardio_main()
    else:
        main()
