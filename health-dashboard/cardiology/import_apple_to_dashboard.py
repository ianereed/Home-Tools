#!/usr/bin/env python3
"""One-shot backfill of historical Apple Health data into the live dashboard DB.

Source: cardiology/apple_export.db (parsed from the 1.15GB Apple Health export.xml).
Target: the live dashboard health.db (data/health.db on homeserver).

The dashboard's live Apple feed (Health Auto Export -> receiver:8095) only began in
2025, so the dashboard has years of missing Apple history. This loads it, mapping each
table to EXACTLY how the live Apple collector writes it (collectors/apple_health_server.py):
  - sleep:    core -> light_minutes; asleep>=120 filter (drop bedtime-schedule artifacts)
  - heart_rate: daily resting HR, context='resting', source='apple'
  - wellness: steps only where currently empty (never clobber garmin)
  - vo2max:   source='apple' (dashboard had zero Apple vo2max)
  - activities: Apple workouts, deduped vs existing garmin/strava by same-date +
                duration-within-35% (matches flagged dup_of, Apple is lowest priority)
  - body_weight: ~3 historical weigh-ins (misc.weight_kg), source='apple', values
                already in kg — pre-Garmin-scale anchor points only, never staleness-arming

All writes are idempotent (INSERT OR IGNORE / guarded upserts), so re-running is safe.
Run ON homeserver against the live DB; do NOT pull-modify-push (would lose concurrent
collector writes).

Usage: python3 import_apple_to_dashboard.py <apple_export.db> <live_health.db>
"""
import sqlite3
import sys


def banner(msg):
    print(f"\n=== {msg} ===", flush=True)


def import_vo2max(src, dst):
    banner("vo2max (Apple)")
    rows = src.execute(
        "SELECT date, value FROM misc WHERE kind='vo2max'").fetchall()
    n = 0
    for date, value in rows:
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        cur = dst.execute(
            "INSERT OR IGNORE INTO vo2max(date, vo2max, source) VALUES(?,?,'apple')",
            (date, v))
        n += cur.rowcount
    print(f"  candidates={len(rows)} inserted={n}")
    return n


def import_sleep(src, dst):
    banner("sleep (Apple, pre-existing rows preserved)")
    rows = src.execute(
        "SELECT date, asleep_min, deep_min, rem_min, core_min, awake_min "
        "FROM sleep WHERE asleep_min >= 120").fetchall()
    n = 0
    for date, asleep, deep, rem, core, awake in rows:
        # core -> light_minutes, matching the live Apple collector.
        cur = dst.execute(
            "INSERT OR IGNORE INTO sleep(date, total_minutes, deep_minutes, "
            "rem_minutes, light_minutes, awake_minutes, source) "
            "VALUES(?,?,?,?,?,?,'apple')",
            (date, asleep, deep or 0, rem or 0, core or 0, awake or 0))
        n += cur.rowcount
    print(f"  candidates={len(rows)} inserted={n} (existing apple nights skipped)")
    return n


def import_resting_hr(src, dst):
    banner("resting HR (Apple daily, uncovered dates only)")
    # Dates already covered by per-sample Apple HR -> skip, so we don't double-count
    # a daily resting value alongside thousands of per-sample readings.
    covered = {r[0] for r in dst.execute(
        "SELECT DISTINCT substr(timestamp,1,10) FROM heart_rate WHERE source='apple'")}
    rows = src.execute("SELECT date, bpm FROM resting_hr").fetchall()
    n = skipped = 0
    for date, bpm in rows:
        if date in covered:
            skipped += 1
            continue
        ts = f"{date}T00:00:00"
        cur = dst.execute(
            "INSERT OR IGNORE INTO heart_rate(timestamp, bpm, context, source) "
            "VALUES(?,?, 'resting', 'apple')",
            (ts, int(round(float(bpm)))))
        n += cur.rowcount
    print(f"  candidates={len(rows)} inserted={n} "
          f"skipped(already covered)={skipped}")
    return n


def import_steps(src, dst):
    banner("daily steps (Apple -> wellness.steps, fill-only)")
    rows = src.execute("SELECT date, steps FROM daily_steps").fetchall()
    n = 0
    for date, steps in rows:
        if steps is None:
            continue
        # Insert a fresh apple wellness row, or fill steps only where currently NULL.
        # Never overwrites a garmin/suunto row that already carries steps.
        cur = dst.execute(
            "INSERT INTO wellness(date, steps, source) VALUES(?,?,'apple') "
            "ON CONFLICT(date) DO UPDATE SET steps=excluded.steps "
            "WHERE wellness.steps IS NULL",
            (date, int(steps)))
        n += cur.rowcount
    print(f"  candidates={len(rows)} affected={n} (rows with steps already set untouched)")
    return n


def import_weight(src, dst):
    banner("weight (Apple, historical anchor points into body_weight)")
    rows = src.execute(
        "SELECT date, value FROM misc WHERE kind='weight_kg'").fetchall()
    n = 0
    for date, value in rows:
        try:
            kg = float(value)  # already kg — Apple's export builder stores weight_kg pre-converted
        except (TypeError, ValueError):
            continue
        ts = f"{date}T00:00:00"
        cur = dst.execute(
            "INSERT OR IGNORE INTO body_weight(timestamp, weight_kg, source) "
            "VALUES(?,?,'apple')",
            (ts, kg))
        n += cur.rowcount
    print(f"  candidates={len(rows)} inserted={n}")
    return n


def import_workouts(src, dst):
    banner("workouts (Apple -> activities, deduped vs garmin/strava)")
    rows = src.execute(
        "SELECT start_ts, date, type, duration_min, kcal FROM workouts").fetchall()
    inserted = duped = 0
    for start_ts, date, wtype, dur, kcal in rows:
        # Already imported on a previous run? UNIQUE(source, source_id) guards re-runs;
        # detect explicitly so the dedup pass below isn't re-done pointlessly.
        existing = dst.execute(
            "SELECT id FROM activities WHERE source='apple' AND source_id=?",
            (start_ts,)).fetchone()
        if existing:
            continue
        # Dedup: same-date canonical (dup_of IS NULL) activity within 35% duration.
        dup_of = None
        if dur:
            for aid, adur in dst.execute(
                    "SELECT id, duration_minutes FROM activities "
                    "WHERE date=? AND source!='apple' AND dup_of IS NULL", (date,)):
                if adur and abs(adur - dur) / max(adur, dur) <= 0.35:
                    dup_of = aid
                    break
        dst.execute(
            "INSERT OR IGNORE INTO activities(date, type, duration_minutes, "
            "calories, source, source_id, start_time, dup_of) "
            "VALUES(?,?,?,?, 'apple', ?, ?, ?)",
            (date, wtype, dur, int(kcal) if kcal is not None else None,
             start_ts, start_ts, dup_of))
        inserted += 1
        if dup_of is not None:
            duped += 1
    print(f"  candidates={len(rows)} inserted={inserted} "
          f"of which flagged dup_of={duped} (net new canonical={inserted - duped})")
    return inserted


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: import_apple_to_dashboard.py <apple_export.db> <live_health.db>")
    src = sqlite3.connect(sys.argv[1])
    dst = sqlite3.connect(sys.argv[2])
    try:
        import_vo2max(src, dst)
        import_sleep(src, dst)
        import_resting_hr(src, dst)
        import_steps(src, dst)
        import_workouts(src, dst)
        import_weight(src, dst)
        dst.commit()
        print("\ncommitted.")
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    main()
