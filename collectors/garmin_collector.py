"""Collect sleep, heart rate, and activity data from Garmin Connect."""

import logging
from datetime import date, timedelta

import keyring

from .db import get_connection

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "health-dashboard-garmin"
TOKEN_DIR = "~/.garminconnect"


def _get_garmin_client():
    """Create and authenticate a Garmin Connect client."""
    from garminconnect import Garmin

    email = keyring.get_password(KEYRING_SERVICE, "email")
    password = keyring.get_password(KEYRING_SERVICE, "password")

    if not email or not password:
        raise RuntimeError(
            "Garmin credentials not found in keychain. Run setup.sh first."
        )

    client = Garmin(email=email, password=password)
    client.login(tokenstore=TOKEN_DIR)
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

            conn.execute(
                """INSERT OR REPLACE INTO activities
                   (date, type, duration_minutes, distance_km, avg_hr, max_hr, calories, source, source_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    act.get("startTimeLocal", "")[:10],
                    act.get("activityType", {}).get("typeKey", "unknown"),
                    round(duration_secs / 60, 1) if duration_secs else 0,
                    round(distance_m / 1000, 2) if distance_m else 0,
                    act.get("averageHR"),
                    act.get("maxHR"),
                    act.get("calories"),
                    "garmin",
                    activity_id,
                ),
            )

        conn.commit()
        logger.info(f"Saved {len(activities)} Garmin activities")
    except Exception as e:
        logger.error(f"Error collecting Garmin activities: {e}")
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

    collect_activities(client, start.isoformat(), today.isoformat())
    logger.info("Garmin collection complete.")
