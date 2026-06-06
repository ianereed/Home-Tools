"""Collect sleep, heart rate, wellness, and activity data from Garmin Connect."""

import logging
import os
from datetime import date, timedelta

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

    collect_activities(client, start.isoformat(), today.isoformat())
    collect_hr_streams(client, days_back)
    logger.info("Garmin collection complete.")
