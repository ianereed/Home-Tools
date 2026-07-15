"""Backfill historical Garmin data (resting HR, sleep, HRV, VO2max, activities,
HR streams) from a start date to today.

Designed for a one-shot multi-year pull, which is thousands of API calls — so it:
  * throttles every call (THROTTLE_S) to stay under Garmin's per-IP rate limit,
  * backs off + retries on 429 (TooManyRequests) instead of dying,
  * checkpoints forward progress to a state file so an interrupted/banned run
    resumes without re-probing every empty "watch not worn" day,
  * is idempotent — INSERT OR REPLACE / OR IGNORE, safe to re-run.

Run on homeserver (where the token store + keychain shim live):
  KEYCHAIN_PATH=/Users/homeserver/Library/Keychains/login.keychain-db \
    .venv/bin/python3 -m collectors.garmin_backfill --start 2020-01-01

Resume (default picks up from the checkpoint automatically):
  ... -m collectors.garmin_backfill --start 2020-01-01
Restart from scratch:
  ... -m collectors.garmin_backfill --start 2020-01-01 --restart
"""
import argparse
import json
import logging
import os
import time
from datetime import date, datetime, timedelta

from .db import get_connection
from .garmin_collector import (
    _bp_rows,
    _get_garmin_client,
    _weight_rows,
    collect_activities,
    collect_hr_streams,
)

logger = logging.getLogger("garmin_backfill")

STATE_PATH = os.path.expanduser("~/.garmin_backfill_state.json")
THROTTLE_S = 0.5          # between every API call
BACKOFF_START_S = 30      # first 429 wait; doubles up to BACKOFF_MAX
BACKOFF_MAX_S = 600
PROGRESS_EVERY = 25       # log a heartbeat every N days
CARDIO_WINDOW_DAYS = 90   # BP/body-composition range calls, chunked like the live probe window


def _throttle():
    time.sleep(THROTTLE_S)


def _call(fn, label):
    """Call a Garmin endpoint with 429 backoff. Returns None on persistent failure."""
    wait = BACKOFF_START_S
    for attempt in range(6):
        try:
            r = fn()
            _throttle()
            return r
        except Exception as e:
            name = type(e).__name__
            if "TooManyRequests" in name or "429" in str(e):
                logger.warning(f"429 on {label}; backing off {wait}s (attempt {attempt+1})")
                time.sleep(wait)
                wait = min(wait * 2, BACKOFF_MAX_S)
                continue
            logger.debug(f"{label}: {name}: {str(e)[:80]}")
            _throttle()
            return None
    logger.error(f"{label}: gave up after repeated 429s")
    return None


def _load_state():
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE_PATH)


def _secs_to_mins(val):
    return round(val / 60, 1) if val else 0


def backfill_day(client, d: str, conn):
    """Pull sleep (+stages), resting HR, HRV for one day in <=3 API calls."""
    # --- sleep (one call powers both the sleep row and wellness sleep fields) ---
    sd_full = _call(lambda: client.get_sleep_data(d), f"sleep {d}") or {}
    daily = sd_full.get("dailySleepDTO") or {}
    sleep_secs = daily.get("sleepTimeSeconds")
    if sleep_secs:
        conn.execute(
            """INSERT OR REPLACE INTO sleep
               (date, total_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes, source)
               VALUES (?, ?, ?, ?, ?, ?, 'garmin')""",
            (d, _secs_to_mins(sleep_secs), _secs_to_mins(daily.get("deepSleepSeconds")),
             _secs_to_mins(daily.get("remSleepSeconds")), _secs_to_mins(daily.get("lightSleepSeconds")),
             _secs_to_mins(daily.get("awakeSleepSeconds"))),
        )
    sleep_score = ((daily.get("sleepScores") or {}).get("overall") or {}).get("value")
    avg_sleeping_hr = daily.get("avgHeartRate")
    spo2 = daily.get("averageSpO2Value")

    # --- resting HR ---
    hr = _call(lambda: client.get_heart_rates(d), f"rhr {d}") or {}
    rhr = hr.get("restingHeartRate")
    if rhr:
        conn.execute(
            """INSERT OR REPLACE INTO heart_rate (timestamp, bpm, context, source)
               VALUES (?, ?, 'resting', 'garmin')""",
            (f"{d}T00:00:00", rhr),
        )

    # --- HRV ---
    hrv_full = _call(lambda: client.get_hrv_data(d), f"hrv {d}") or {}
    hrv = (hrv_full.get("hrvSummary") or {}).get("lastNightAvg")

    if any(v is not None for v in (hrv, sleep_score, avg_sleeping_hr, spo2)):
        conn.execute(
            """INSERT INTO wellness (date, hrv, sleep_score, avg_sleeping_hr, spo2, source)
               VALUES (?, ?, ?, ?, ?, 'garmin')
               ON CONFLICT(date) DO UPDATE SET
                 hrv = COALESCE(excluded.hrv, wellness.hrv),
                 sleep_score = COALESCE(excluded.sleep_score, wellness.sleep_score),
                 avg_sleeping_hr = COALESCE(excluded.avg_sleeping_hr, wellness.avg_sleeping_hr),
                 spo2 = COALESCE(excluded.spo2, wellness.spo2),
                 source = 'garmin'""",
            (d, hrv, sleep_score, avg_sleeping_hr, spo2),
        )
    conn.commit()
    return bool(sleep_secs or rhr or hrv)


def backfill_vo2max(client, d: str, conn):
    """VO2max changes slowly, so it's sampled weekly (Mondays). Written to a
    dedicated `vo2max` side table (created on demand) to avoid health.db schema drift."""
    m = _call(lambda: client.get_max_metrics(d), f"vo2 {d}")
    vo2 = None
    if isinstance(m, list) and m:
        gen = (m[0].get("generic") or {})
        vo2 = gen.get("vo2MaxPreciseValue") or gen.get("vo2MaxValue")
    if vo2:
        conn.execute(
            "INSERT OR REPLACE INTO vo2max (date, vo2max, source) VALUES (?, ?, 'garmin')",
            (d, vo2),
        )
        conn.commit()
    return vo2


def backfill_cardio(client, conn, start: date, end: date, state: dict):
    """Pull BP + body composition from `start` to `end` in ~90-day windows.

    Checkpointed independently of the per-day loop (state keys `cardio_cursor`
    / `cardio_done`) since a multi-year backfill is a handful of range calls,
    not one per day — losing progress on interruption would mean redoing a
    handful of already-fetched windows, which INSERT OR REPLACE makes cheap but
    the checkpoint avoids anyway. `--restart` (via a fresh `state` dict) clears
    both keys the same way it clears `last_day`.
    """
    if state.get("cardio_done"):
        logger.info("Cardio (BP + body composition) backfill already done — skipping")
        return

    window_start = start
    cursor_str = state.get("cardio_cursor")
    if cursor_str:
        cursor_date = datetime.strptime(cursor_str, "%Y-%m-%d").date()
        if cursor_date >= start:
            window_start = cursor_date + timedelta(days=1)
            logger.info(f"Resuming cardio backfill from {window_start} (checkpoint {cursor_str})")

    while window_start <= end:
        window_end = min(window_start + timedelta(days=CARDIO_WINDOW_DAYS - 1), end)
        ws, we = window_start.isoformat(), window_end.isoformat()
        logger.info(f"Cardio backfill window {ws}..{we}")

        bp_payload = _call(lambda: client.get_blood_pressure(ws, we), f"bp {ws}..{we}")
        bp_rows = _bp_rows(bp_payload) if bp_payload else []
        if bp_rows:
            conn.executemany(
                """INSERT OR REPLACE INTO blood_pressure
                   (timestamp, systolic, diastolic, pulse, source, source_id, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                bp_rows,
            )
            conn.commit()
            logger.info(f"  BP: {len(bp_rows)} readings")

        comp_payload = _call(lambda: client.get_body_composition(ws, we), f"composition {ws}..{we}")
        if comp_payload:
            weight_rows, comp_rows = _weight_rows(comp_payload)
            if weight_rows:
                conn.executemany(
                    """INSERT OR REPLACE INTO body_weight
                       (timestamp, weight_kg, bmi, source, source_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    weight_rows,
                )
            if comp_rows:
                conn.executemany(
                    """INSERT OR REPLACE INTO body_composition
                       (timestamp, weight_kg, body_fat_pct, lean_mass_kg, fat_mass_kg,
                        bone_mass_kg, visceral_fat_rating, visceral_fat_mass_kg,
                        body_water_pct, note, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    comp_rows,
                )
            if weight_rows:
                conn.commit()
                logger.info(f"  weigh-ins: {len(weight_rows)} ({len(comp_rows)} with BIA)")

        state["cardio_cursor"] = window_end.isoformat()
        _save_state(state)
        window_start = window_end + timedelta(days=1)

    state["cardio_done"] = True
    _save_state(state)
    logger.info("Cardio backfill complete.")


def ensure_vo2_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS vo2max (
               date TEXT NOT NULL,
               vo2max REAL,
               source TEXT NOT NULL,
               PRIMARY KEY (date, source))"""
    )
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--restart", action="store_true", help="ignore checkpoint")
    ap.add_argument("--skip-streams", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    client = _get_garmin_client()
    logger.info("Garmin login OK")
    conn = get_connection()
    ensure_vo2_table(conn)

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    state = {} if args.restart else _load_state()
    resume = state.get("last_day")
    cursor = start
    if resume:
        rd = datetime.strptime(resume, "%Y-%m-%d").date()
        if rd >= start:
            cursor = rd + timedelta(days=1)
            logger.info(f"Resuming from checkpoint: {cursor} (last done {resume})")

    # --- activities (one range call) — only on a fresh run or if not yet done ---
    if not state.get("activities_done") or args.restart:
        logger.info(f"Fetching activities {args.start}..{args.end}")
        collect_activities(client, args.start, args.end)
        _throttle()
        state["activities_done"] = True
        _save_state(state)

    # --- cardio: BP + body composition (checkpointed range-call windows) ---
    backfill_cardio(client, conn, start, end, state)

    # --- per-day loop ---
    total_days = (end - cursor).days + 1
    done = 0
    d = cursor
    while d <= end:
        ds = d.isoformat()
        try:
            backfill_day(client, ds, conn)
            if d.weekday() == 0:  # Mondays: VO2max
                backfill_vo2max(client, ds, conn)
        except Exception as e:
            logger.error(f"day {ds} failed: {e}")
        state["last_day"] = ds
        _save_state(state)
        done += 1
        if done % PROGRESS_EVERY == 0:
            logger.info(f"progress: {ds}  ({done}/{total_days} days, "
                        f"{round(100*done/max(total_days,1))}%)")
        d += timedelta(days=1)

    conn.close()

    # --- HR streams for all backfilled activities (separate pass) ---
    if not args.skip_streams:
        logger.info("Collecting HR streams for historical Garmin activities...")
        # days_back large enough to cover the whole backfill window
        span = (date.today() - start).days + 30
        collect_hr_streams(client, days_back=span)

    logger.info("Backfill complete.")


if __name__ == "__main__":
    main()
