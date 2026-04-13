"""Parse Apple Health data exported via iOS Shortcut to iCloud Drive."""

import json
import logging
import os
from glob import glob
from pathlib import Path

from .db import get_connection

logger = logging.getLogger(__name__)

# iCloud Drive path on macOS
ICLOUD_HEALTH_DIR = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/HealthExport"
)


def _find_export_files():
    """Find all JSON export files in the iCloud HealthExport folder."""
    if not os.path.isdir(ICLOUD_HEALTH_DIR):
        logger.warning(
            f"iCloud HealthExport folder not found at {ICLOUD_HEALTH_DIR}. "
            "Set up the iOS Shortcut to export data there."
        )
        return []

    files = sorted(glob(os.path.join(ICLOUD_HEALTH_DIR, "*.json")))
    if not files:
        logger.warning(f"No JSON files found in {ICLOUD_HEALTH_DIR}")
    return files


def _parse_sleep_records(data: dict):
    """Parse sleep records from the export JSON."""
    records = data.get("sleep", [])
    if not records:
        return []

    parsed = []
    for rec in records:
        sleep_date = rec.get("date", "")[:10]
        if not sleep_date:
            continue
        parsed.append((
            sleep_date,
            rec.get("total_minutes", 0),
            rec.get("deep_minutes", 0),
            rec.get("rem_minutes", 0),
            rec.get("light_minutes", 0),
            rec.get("awake_minutes", 0),
            rec.get("source", "apple"),
        ))
    return parsed


def _parse_heart_rate_records(data: dict):
    """Parse heart rate records from the export JSON."""
    records = data.get("heart_rate", [])
    if not records:
        return []

    parsed = []
    for rec in records:
        timestamp = rec.get("timestamp", "")
        bpm = rec.get("bpm")
        if not timestamp or not bpm:
            continue
        parsed.append((
            timestamp,
            int(bpm),
            rec.get("context", "resting"),
            rec.get("source", "apple"),
        ))
    return parsed


def collect_all(days_back: int = 7):
    """Parse all Apple Health export files and store in database."""
    logger.info("Collecting Apple Health data from iCloud Drive...")

    files = _find_export_files()
    if not files:
        return

    conn = get_connection()
    total_sleep = 0
    total_hr = 0

    try:
        for filepath in files:
            try:
                with open(filepath) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error reading {filepath}: {e}")
                continue

            sleep_records = _parse_sleep_records(data)
            for rec in sleep_records:
                conn.execute(
                    """INSERT OR REPLACE INTO sleep
                       (date, total_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    rec,
                )
            total_sleep += len(sleep_records)

            hr_records = _parse_heart_rate_records(data)
            for rec in hr_records:
                conn.execute(
                    """INSERT OR REPLACE INTO heart_rate
                       (timestamp, bpm, context, source)
                       VALUES (?, ?, ?, ?)""",
                    rec,
                )
            total_hr += len(hr_records)

        conn.commit()
        logger.info(f"Saved {total_sleep} sleep records and {total_hr} HR records from Apple Health")
    except Exception as e:
        logger.error(f"Error collecting Apple Health data: {e}")
    finally:
        conn.close()

    logger.info("Apple Health collection complete.")
