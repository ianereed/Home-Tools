"""Collect sleep, heart rate, wellness, and activity data from Garmin Connect."""

import logging
import os
from datetime import date, datetime, timedelta, timezone

from .db import get_connection

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "health-dashboard-garmin"
TOKEN_DIR = os.path.expanduser("~/.garminconnect")


def _get_garmin_client():
    """Authenticate to Garmin by resuming from the saved OAuth token store.

    Garmin enforces MFA + IP rate-limits on fresh email/password logins, which a
    headless launchd/jobs context cannot satisfy. We therefore resume from the
    token store seeded by a one-time interactive login
    (``python -m collectors.seed_garmin_token``). Tokens last ~1 year and
    refresh silently. If the store is missing/expired we raise a clear error
    telling the operator to re-seed rather than triggering an MFA prompt that
    can never be answered headlessly.
    """
    from garminconnect import Garmin

    client = Garmin()
    try:
        client.login(tokenstore=TOKEN_DIR)
    except Exception as e:
        raise RuntimeError(
            f"Garmin token login failed ({e}). The OAuth token store at "
            f"{TOKEN_DIR} is missing or expired. Re-seed it interactively:\n"
            "  ssh -t homeserver@homeserver "
            "'cd ~/Home-Tools/health-dashboard && "
            ".venv/bin/python3 -m collectors.seed_garmin_token'"
        ) from e
    return client


def collect_sleep(client, target_date: str):
    """Collect sleep data for a given date (YYYY-MM-DD)."""
    conn = get_connection()
    try:
        data = client.get_sleep_data(target_date)
        if not data:
            logger.info(f"No sleep data for {target_date}")
            return

        daily = data.get("dailySleepDTO", {})
        if not daily:
            logger.info(f"No dailySleepDTO for {target_date}")
            return

        # Garmin returns an empty dailySleepDTO shell for days the watch wasn't
        # worn (the user has a separate Apple Watch). Skip those — writing a
        # 0-minute row would mask the real Apple Health data for that night.
        if not daily.get("sleepTimeSeconds"):
            logger.info(f"No tracked Garmin sleep for {target_date} (watch not worn)")
            return

        # Sleep durations are in seconds from Garmin
        def secs_to_mins(val):
            return round(val / 60, 1) if val else 0

        conn.execute(
            """INSERT OR REPLACE INTO sleep
               (date, total_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                target_date,
                secs_to_mins(daily.get("sleepTimeSeconds")),
                secs_to_mins(daily.get("deepSleepSeconds")),
                secs_to_mins(daily.get("remSleepSeconds")),
                secs_to_mins(daily.get("lightSleepSeconds")),
                secs_to_mins(daily.get("awakeSleepSeconds")),
                "garmin",
            ),
        )
        conn.commit()
        logger.info(f"Saved Garmin sleep for {target_date}")
    except Exception as e:
        logger.error(f"Error collecting Garmin sleep for {target_date}: {e}")
    finally:
        conn.close()


def collect_heart_rate(client, target_date: str):
    """Collect resting heart rate for a given date."""
    conn = get_connection()
    try:
        data = client.get_heart_rates(target_date)
        if not data:
            logger.info(f"No HR data for {target_date}")
            return

        resting_hr = data.get("restingHeartRate")
        if resting_hr:
            conn.execute(
                """INSERT OR REPLACE INTO heart_rate
                   (timestamp, bpm, context, source)
                   VALUES (?, ?, ?, ?)""",
                (f"{target_date}T00:00:00", resting_hr, "resting", "garmin"),
            )
            conn.commit()
            logger.info(f"Saved Garmin resting HR {resting_hr} for {target_date}")
    except Exception as e:
        logger.error(f"Error collecting Garmin HR for {target_date}: {e}")
    finally:
        conn.close()


def collect_wellness(client, target_date: str):
    """Collect daily wellness metrics (HRV, sleep score, sleeping HR, SpO2, steps).

    Replaces the retired Suunto/Intervals.icu wellness feed. Garmin syncs are
    intermittent (only when the watch is worn overnight), so a day with no data
    is skipped rather than written as an all-null row.
    """
    conn = get_connection()
    try:
        hrv = sleep_score = avg_sleeping_hr = spo2 = steps = None

        try:
            sd = (client.get_sleep_data(target_date) or {}).get("dailySleepDTO") or {}
            overall = (sd.get("sleepScores") or {}).get("overall") or {}
            sleep_score = overall.get("value")
            avg_sleeping_hr = sd.get("avgHeartRate")
            spo2 = sd.get("averageSpO2Value")
        except Exception as e:
            logger.debug(f"Garmin sleep-score {target_date}: {e}")

        try:
            summary = (client.get_hrv_data(target_date) or {}).get("hrvSummary") or {}
            hrv = summary.get("lastNightAvg")
        except Exception as e:
            logger.debug(f"Garmin HRV {target_date}: {e}")

        try:
            daily_steps = client.get_daily_steps(target_date, target_date) or []
            if daily_steps:
                steps = daily_steps[0].get("totalSteps")
        except Exception as e:
            logger.debug(f"Garmin steps {target_date}: {e}")

        if all(v is None for v in (hrv, sleep_score, avg_sleeping_hr, spo2, steps)):
            return  # nothing synced for this day

        # Garmin is the authoritative HRV source (it also syncs into Apple Health,
        # so a value present in both originated here). COALESCE lets a real Garmin
        # HRV win while NOT nulling an Apple-Watch-only value on days Garmin has none.
        conn.execute(
            """INSERT INTO wellness
               (date, hrv, sleep_score, avg_sleeping_hr, spo2, steps, source)
               VALUES (?, ?, ?, ?, ?, ?, 'garmin')
               ON CONFLICT(date) DO UPDATE SET
                 hrv = COALESCE(excluded.hrv, wellness.hrv),
                 sleep_score = excluded.sleep_score,
                 avg_sleeping_hr = excluded.avg_sleeping_hr,
                 spo2 = excluded.spo2,
                 steps = excluded.steps,
                 source = 'garmin'""",
            (target_date, hrv, sleep_score, avg_sleeping_hr, spo2, steps),
        )
        conn.commit()
        logger.info(
            f"Saved Garmin wellness for {target_date} "
            f"(hrv={hrv}, sleep_score={sleep_score}, steps={steps})"
        )
    except Exception as e:
        logger.error(f"Error collecting Garmin wellness for {target_date}: {e}")
    finally:
        conn.close()


def collect_activities(client, start_date: str, end_date: str):
    """Collect activities between two dates."""
    conn = get_connection()
    try:
        activities = client.get_activities_by_date(start_date, end_date)
        if not activities:
            logger.info(f"No Garmin activities from {start_date} to {end_date}")
            return

        for act in activities:
            activity_id = str(act.get("activityId", ""))
            if not activity_id:
                continue

            duration_secs = act.get("duration", 0)
            distance_m = act.get("distance", 0)

            start_local = act.get("startTimeLocal", "")
            conn.execute(
                """INSERT OR REPLACE INTO activities
                   (date, type, duration_minutes, distance_km, avg_hr, max_hr, calories, source, source_id, start_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    start_local[:10],
                    act.get("activityType", {}).get("typeKey", "unknown"),
                    round(duration_secs / 60, 1) if duration_secs else 0,
                    round(distance_m / 1000, 2) if distance_m else 0,
                    act.get("averageHR"),
                    act.get("maxHR"),
                    act.get("calories"),
                    "garmin",
                    activity_id,
                    start_local or None,
                ),
            )

        conn.commit()
        logger.info(f"Saved {len(activities)} Garmin activities")
    except Exception as e:
        logger.error(f"Error collecting Garmin activities: {e}")
    finally:
        conn.close()


# Garmin's detail payload downsamples to maxchart points; 4000 keeps ~1 sample
# every 2s even for a multi-hour activity, which is ample resolution for TRIMP.
_DETAIL_MAX_CHART = 4000


def collect_hr_streams(client, days_back: int = 7):
    """Fetch per-activity HR time-series from Garmin — the device that recorded it.

    HR streams used to come only from Strava (the historical activity source),
    which meant the canonical Garmin row leaned on its Strava mirror for the raw
    data that originated on the watch. Garmin's get_activity_details exposes the
    same series (directHeartRate vs sumElapsedDuration, in seconds), so we pull it
    straight from the source. Strava stream collection stays as a fallback for
    activities never recorded on the watch.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT source_id FROM activities
               WHERE source = 'garmin' AND avg_hr IS NOT NULL
               AND date >= date('now', ?)
               AND source_id NOT IN (SELECT DISTINCT activity_id FROM activity_streams)""",
            (f"-{days_back} days",),
        ).fetchall()

        stream_count = 0
        for row in rows:
            activity_id = row[0]
            try:
                details = client.get_activity_details(int(activity_id), maxchart=_DETAIL_MAX_CHART)
                descriptors = details.get("metricDescriptors", []) or []
                metrics = details.get("activityDetailMetrics", []) or []
                idx = {d.get("key"): d.get("metricsIndex") for d in descriptors}
                hr_i = idx.get("directHeartRate")
                # sumElapsedDuration is seconds-since-start — matches the Strava
                # `time` stream and the activity_streams.timestamp_offset contract.
                t_i = idx.get("sumElapsedDuration")
                if hr_i is None or t_i is None:
                    logger.debug(f"Garmin activity {activity_id} has no HR/elapsed metric; skipping stream")
                    continue

                n = 0
                for m in metrics:
                    vals = m.get("metrics", [])
                    if hr_i >= len(vals) or t_i >= len(vals):
                        continue
                    hr, t = vals[hr_i], vals[t_i]
                    if hr is None or t is None:
                        continue
                    # Guard per-sample: a single NaN/inf would otherwise raise and
                    # abort the rest of this activity's stream, not just the sample.
                    try:
                        offset, bpm = int(t), int(hr)
                    except (ValueError, TypeError):
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO activity_streams
                           (activity_id, timestamp_offset, bpm)
                           VALUES (?, ?, ?)""",
                        (str(activity_id), offset, bpm),
                    )
                    n += 1

                conn.commit()
                if n:
                    stream_count += 1
                    logger.info(f"Saved {n} HR points for Garmin activity {activity_id}")
            except Exception as e:
                logger.warning(f"Could not fetch Garmin stream for activity {activity_id}: {e}")

        logger.info(f"Collected HR streams for {stream_count} Garmin activities")
    except Exception as e:
        logger.error(f"Error collecting Garmin HR streams: {e}")
    finally:
        conn.close()


def _bp_rows(payload: dict | None) -> list[tuple]:
    """Map a get_blood_pressure(...) payload into blood_pressure insert rows.

    Real shape (Phase 0 probe, Appendix D): a day-summary wrapper around a
    nested per-reading list. The day-level aggregates (highSystolic/
    lowSystolic/categoryStats/etc.) are pre-aggregated and NOT written here —
    blood_pressure is one row per reading, so this iterates
    measurementSummaries[*].measurements[*], not the top level.
    """
    rows = []
    for day in (payload or {}).get("measurementSummaries") or []:
        for m in day.get("measurements") or []:
            systolic = m.get("systolic")
            diastolic = m.get("diastolic")
            if systolic is None or diastolic is None:
                continue
            # measurementTimestampLocal observed as len=21 ISO-ish with
            # fractional seconds ("YYYY-MM-DDTHH:MM:SS.f"); truncate to the
            # local-ISO convention the rest of the schema uses.
            raw_ts = m.get("measurementTimestampLocal") or ""
            ts = raw_ts[:19]
            if not ts:
                continue
            version = m.get("version")
            source_id = str(version) if version is not None else None
            # Observed payload used "" for no note, not None — falsy-empty
            # means no note, not just `is None`.
            notes = m.get("notes") or None
            rows.append((ts, systolic, diastolic, m.get("pulse"), "garmin", source_id, notes))
    return rows


def _weight_rows(payload: dict | None) -> tuple[list[tuple], list[tuple]]:
    """Map a get_body_composition(...) payload into (body_weight, body_composition) rows.

    Garmin reports weight/muscleMass/boneMass in grams; convert to kg here.
    Prefers local calendarDate over the GMT epoch fields (Appendix D gotcha);
    falls back to a date derived from timestampGMT with a T00:00:00 time-of-day
    when calendarDate is absent. A body_composition row is written only when at
    least one BIA field is present — the observed payload returns an
    all-BIA-None day-summary row for manual (non-scale) weigh-ins rather than
    omitting it, and that's not a composition reading.
    """
    weight_rows, comp_rows = [], []
    for row in (payload or {}).get("dateWeightList") or []:
        weight_g = row.get("weight")
        if weight_g is None:
            continue

        calendar_date = row.get("calendarDate")
        if calendar_date:
            ts = f"{calendar_date}T00:00:00"
        else:
            gmt_ms = row.get("timestampGMT")
            if gmt_ms is None:
                continue
            gmt_date = datetime.fromtimestamp(gmt_ms / 1000, tz=timezone.utc).date()
            ts = f"{gmt_date.isoformat()}T00:00:00"

        weight_kg = weight_g / 1000
        sample_pk = row.get("samplePk")
        source_id = str(sample_pk) if sample_pk is not None else None
        weight_rows.append((ts, weight_kg, row.get("bmi"), "garmin", source_id))

        bone_mass = row.get("boneMass")
        muscle_mass = row.get("muscleMass")
        bia_fields = (
            row.get("bodyFat"), row.get("bodyWater"), bone_mass,
            muscle_mass, row.get("visceralFat"), row.get("physiqueRating"),
        )
        if any(v is not None for v in bia_fields):
            comp_rows.append((
                ts,
                weight_kg,
                row.get("bodyFat"),
                muscle_mass / 1000 if muscle_mass is not None else None,
                None,  # fat_mass_kg: DEXA-direct only, never reported by Garmin
                bone_mass / 1000 if bone_mass is not None else None,
                row.get("visceralFat"),
                None,  # visceral_fat_mass_kg: DEXA only
                row.get("bodyWater"),
                None,  # note
                "garmin",
            ))
    return weight_rows, comp_rows


# mealNutritionContent field -> nutrition_daily column, in insert order after
# `date`. Shape verified live 2026-07-16 (journal-224; garmin_probe --nutrition).
_NUTRITION_FIELD_MAP = [
    ("calories", "calories_kcal"),
    ("protein", "protein_g"),
    ("carbs", "carbs_g"),
    ("fat", "fat_g"),
    ("saturatedFat", "saturated_fat_g"),
    ("fiber", "fiber_g"),
    ("sugar", "sugar_g"),
    ("sodium", "sodium_mg"),
    ("potassium", "potassium_mg"),
]


def _nutrition_row(payload: dict | None) -> tuple | None:
    """Map a get_nutrition_daily_food_log(date) payload into one nutrition_daily row.

    Real shape (journal-224 probe): per-meal pre-aggregated rollups live at
    mealDetails[*].mealNutritionContent (sodium/potassium/fiber/saturatedFat and
    the rest all present there); the top-level dailyNutritionContent rollup
    carries ONLY calories/carbs/fat/protein, so daily sodium etc. must be summed
    here across meals. None-aware sum: a nutrient absent from every meal stays
    NULL (unknown), not 0 — zero-filling would fake a perfect-sodium day.

    Returns (date, *nutrients, 'garmin') or None for an empty day — Garmin
    returns an empty-mealDetails shell (not an error) when nothing is logged,
    including for days before Connect+ nutrition was enabled.
    """
    mdate = (payload or {}).get("mealDate")
    if not mdate:
        return None
    meal_contents = [
        md["mealNutritionContent"]
        for md in (payload or {}).get("mealDetails") or []
        if md.get("mealNutritionContent")
    ]
    # A meal shell with no logged foods reports an all-zero/None rollup; only
    # meals that actually contain loggedFoods count toward "day has data".
    has_foods = any(
        md.get("loggedFoods")
        for md in (payload or {}).get("mealDetails") or []
    )
    if not meal_contents or not has_foods:
        return None

    sums = []
    for src_field, _col in _NUTRITION_FIELD_MAP:
        vals = [mc.get(src_field) for mc in meal_contents if mc.get(src_field) is not None]
        sums.append(round(sum(vals), 2) if vals else None)
    if all(v is None for v in sums):
        return None
    return (mdate, *sums, "garmin")


def collect_nutrition(client, target_date: str):
    """Collect Connect+ nutrition for one day into nutrition_daily.

    Per-day because the (unofficial) nutrition-service has no range endpoint —
    collect_all's per-day loop makes this days_back+1 extra API calls per run.
    Empty day (nothing logged / feature not yet enabled) = silent INFO return
    per Standing rule 6. INSERT OR REPLACE on PK(date, source) means a day
    re-collected after more meals are logged converges to the latest totals.
    """
    conn = get_connection()
    try:
        payload = client.get_nutrition_daily_food_log(target_date)
        row = _nutrition_row(payload)
        if row is None:
            logger.info(f"No Garmin nutrition logged for {target_date}")
            return

        cols = ", ".join(["date"] + [col for _f, col in _NUTRITION_FIELD_MAP] + ["source"])
        marks = ", ".join("?" for _ in range(len(_NUTRITION_FIELD_MAP) + 2))
        conn.execute(
            f"INSERT OR REPLACE INTO nutrition_daily ({cols}) VALUES ({marks})", row
        )
        conn.commit()
        logger.info(f"Saved Garmin nutrition for {target_date}")
    except Exception as e:
        logger.error(f"Error collecting Garmin nutrition for {target_date}: {e}")
    finally:
        conn.close()


def collect_blood_pressure(client, start_date: str, end_date: str):
    """Collect blood-pressure readings for a date range (single range call)."""
    conn = get_connection()
    try:
        payload = client.get_blood_pressure(start_date, end_date)
        rows = _bp_rows(payload)
        if not rows:
            logger.info(f"No Garmin BP readings from {start_date} to {end_date}")
            return

        conn.executemany(
            """INSERT OR REPLACE INTO blood_pressure
               (timestamp, systolic, diastolic, pulse, source, source_id, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        logger.info(f"Saved {len(rows)} Garmin BP readings")
    except Exception as e:
        logger.error(f"Error collecting Garmin blood pressure: {e}")
    finally:
        conn.close()


def collect_body_composition(client, start_date: str, end_date: str):
    """Collect weight + body-composition data for a date range (single range call).

    A Garmin weigh-in with BIA data writes BOTH a body_weight row and a
    body_composition row (same timestamp/source); a manual weigh-in with no
    BIA fields only gets the body_weight row.
    """
    conn = get_connection()
    try:
        payload = client.get_body_composition(start_date, end_date)
        weight_rows, comp_rows = _weight_rows(payload)
        if not weight_rows:
            logger.info(f"No Garmin weigh-ins from {start_date} to {end_date}")
            return

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
        conn.commit()
        logger.info(f"Saved {len(weight_rows)} Garmin weigh-ins ({len(comp_rows)} with BIA)")
    except Exception as e:
        logger.error(f"Error collecting Garmin body composition: {e}")
    finally:
        conn.close()


def collect_all(days_back: int = 7):
    """Collect all Garmin data for the past N days."""
    logger.info(f"Collecting Garmin data for past {days_back} days...")
    client = _get_garmin_client()

    today = date.today()
    start = today - timedelta(days=days_back)

    for i in range(days_back + 1):
        d = (start + timedelta(days=i)).isoformat()
        collect_sleep(client, d)
        collect_heart_rate(client, d)
        collect_wellness(client, d)
        collect_nutrition(client, d)

    collect_activities(client, start.isoformat(), today.isoformat())
    collect_hr_streams(client, days_back)
    collect_blood_pressure(client, start.isoformat(), today.isoformat())
    collect_body_composition(client, start.isoformat(), today.isoformat())
    logger.info("Garmin collection complete.")
