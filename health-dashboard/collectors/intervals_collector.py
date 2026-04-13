"""Collect Suunto sleep and wellness data via Intervals.icu API."""

import logging
from datetime import date, timedelta

import keyring
import requests

from .db import get_connection

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "health-dashboard-intervals"
API_BASE = "https://intervals.icu/api/v1"


def _get_credentials():
    """Load Intervals.icu API key and athlete ID from keychain."""
    api_key = keyring.get_password(KEYRING_SERVICE, "api_key")
    athlete_id = keyring.get_password(KEYRING_SERVICE, "athlete_id")

    if not api_key or not athlete_id:
        raise RuntimeError(
            "Intervals.icu credentials not found in keychain. Run setup.sh first."
        )
    return api_key, athlete_id


def _fetch_wellness(days_back: int):
    """Fetch wellness data from Intervals.icu API."""
    api_key, athlete_id = _get_credentials()

    oldest = (date.today() - timedelta(days=days_back)).isoformat()
    newest = date.today().isoformat()

    url = f"{API_BASE}/athlete/{athlete_id}/wellness"
    params = {"oldest": oldest, "newest": newest}

    response = requests.get(url, params=params, auth=("API_KEY", api_key))
    response.raise_for_status()
    return response.json()


def collect_all(days_back: int = 7):
    """Collect all Suunto data via Intervals.icu."""
    logger.info(f"Collecting Suunto data via Intervals.icu for past {days_back} days...")

    wellness_data = _fetch_wellness(days_back)
    if not wellness_data:
        logger.info("No wellness data from Intervals.icu")
        return

    conn = get_connection()
    sleep_count = 0
    hr_count = 0
    wellness_count = 0

    try:
        for entry in wellness_data:
            entry_date = entry.get("id", "")
            if not entry_date:
                continue

            # Sleep data
            sleep_secs = entry.get("sleepSecs")
            if sleep_secs and sleep_secs > 0:
                conn.execute(
                    """INSERT OR REPLACE INTO sleep
                       (date, total_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry_date,
                        round(sleep_secs / 60, 1),
                        0, 0, 0, 0,
                        "suunto",
                    ),
                )
                sleep_count += 1

            # Resting heart rate
            rhr = entry.get("restingHR")
            if rhr and rhr > 0:
                conn.execute(
                    """INSERT OR REPLACE INTO heart_rate
                       (timestamp, bpm, context, source)
                       VALUES (?, ?, ?, ?)""",
                    (f"{entry_date}T00:00:00", int(rhr), "resting", "suunto"),
                )
                hr_count += 1

            # Wellness metrics (HRV, sleep score, readiness, etc.)
            conn.execute(
                """INSERT OR REPLACE INTO wellness
                   (date, hrv, hrv_sdnn, sleep_score, sleep_quality, avg_sleeping_hr, readiness, spo2, steps, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_date,
                    entry.get("hrv"),
                    entry.get("hrvSDNN"),
                    entry.get("sleepScore"),
                    entry.get("sleepQuality"),
                    entry.get("avgSleepingHR"),
                    entry.get("readiness"),
                    entry.get("spO2"),
                    entry.get("steps"),
                    "suunto",
                ),
            )
            wellness_count += 1

        conn.commit()
        logger.info(
            f"Saved {sleep_count} sleep, {hr_count} HR, "
            f"{wellness_count} wellness records from Intervals.icu"
        )
    except Exception as e:
        logger.error(f"Error saving Intervals.icu data: {e}")
    finally:
        conn.close()

    logger.info("Intervals.icu collection complete.")


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Collect Suunto data via Intervals.icu")
    parser.add_argument("--days", type=int, default=365, help="Days of history (default: 365)")
    args = parser.parse_args()
    collect_all(args.days)
