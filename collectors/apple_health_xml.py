"""Parse Apple Health XML export (from Settings > Health > Export All Health Data)."""

import logging
import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime

from .db import get_connection

logger = logging.getLogger(__name__)


def _parse_datetime(dt_str: str) -> str:
    """Parse Apple Health datetime format to ISO string."""
    # Format: "2026-03-15 23:14:00 -0700"
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S %z")
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return dt_str


def _parse_date(dt_str: str) -> str:
    """Extract just the date from Apple Health datetime."""
    return dt_str[:10]


def import_xml(xml_path: str, days_back: int = 90):
    """Import sleep and heart rate data from Apple Health XML export.

    Args:
        xml_path: Path to the export.xml file (inside the unzipped export folder)
        days_back: Only import data from the last N days
    """
    if not os.path.exists(xml_path):
        logger.error(f"File not found: {xml_path}")
        return

    logger.info(f"Parsing Apple Health XML (this may take a moment for large files)...")

    cutoff = datetime.now().strftime("%Y-%m-%d")
    from datetime import timedelta
    cutoff_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    conn = get_connection()
    hr_count = 0
    sleep_records = defaultdict(lambda: {"total": 0, "asleep": 0, "inbed": 0, "sources": set()})

    try:
        # Use iterparse to handle large files efficiently
        context = ET.iterparse(xml_path, events=("end",))

        for event, elem in context:
            if elem.tag != "Record":
                elem.clear()
                continue

            rec_type = elem.get("type", "")
            start = elem.get("startDate", "")
            end = elem.get("endDate", "")
            value = elem.get("value", "")
            source = elem.get("sourceName", "")

            # Skip records older than cutoff
            if start[:10] < cutoff_date:
                elem.clear()
                continue

            # Heart Rate
            if rec_type == "HKQuantityTypeIdentifierHeartRate" and value:
                try:
                    bpm = int(float(value))
                    timestamp = _parse_datetime(start)
                    conn.execute(
                        """INSERT OR IGNORE INTO heart_rate
                           (timestamp, bpm, context, source)
                           VALUES (?, ?, ?, ?)""",
                        (timestamp, bpm, "resting", "apple"),
                    )
                    hr_count += 1
                except (ValueError, TypeError):
                    pass

            # Sleep Analysis
            elif rec_type == "HKCategoryTypeIdentifierSleepAnalysis":
                sleep_date = _parse_date(start)
                try:
                    start_dt = datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S")
                    end_dt = datetime.strptime(end[:19], "%Y-%m-%d %H:%M:%S")
                    duration_mins = (end_dt - start_dt).total_seconds() / 60
                except ValueError:
                    duration_mins = 0

                sleep_records[sleep_date]["sources"].add(source)

                # Apple Health sleep values:
                # HKCategoryValueSleepAnalysisInBed = 0
                # HKCategoryValueSleepAnalysisAsleepUnspecified = 1
                # HKCategoryValueSleepAnalysisAwake = 2
                # HKCategoryValueSleepAnalysisAsleepCore = 3
                # HKCategoryValueSleepAnalysisAsleepDeep = 4
                # HKCategoryValueSleepAnalysisAsleepREM = 5
                if value == "HKCategoryValueSleepAnalysisInBed":
                    sleep_records[sleep_date]["inbed"] += duration_mins
                elif value == "HKCategoryValueSleepAnalysisAsleepUnspecified":
                    sleep_records[sleep_date]["total"] += duration_mins
                elif value == "HKCategoryValueSleepAnalysisAwake":
                    sleep_records[sleep_date]["awake"] = sleep_records[sleep_date].get("awake", 0) + duration_mins
                elif value == "HKCategoryValueSleepAnalysisAsleepCore":
                    sleep_records[sleep_date]["light"] = sleep_records[sleep_date].get("light", 0) + duration_mins
                    sleep_records[sleep_date]["total"] += duration_mins
                elif value == "HKCategoryValueSleepAnalysisAsleepDeep":
                    sleep_records[sleep_date]["deep"] = sleep_records[sleep_date].get("deep", 0) + duration_mins
                    sleep_records[sleep_date]["total"] += duration_mins
                elif value == "HKCategoryValueSleepAnalysisAsleepREM":
                    sleep_records[sleep_date]["rem"] = sleep_records[sleep_date].get("rem", 0) + duration_mins
                    sleep_records[sleep_date]["total"] += duration_mins

            # Free memory as we go
            elem.clear()

        # Commit heart rate in batches
        if hr_count > 0:
            conn.commit()
            logger.info(f"Saved {hr_count} heart rate records from Apple Health")

        # Insert sleep records
        sleep_count = 0
        for sleep_date, data in sleep_records.items():
            if data["total"] <= 0:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO sleep
                   (date, total_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    sleep_date,
                    round(data["total"], 1),
                    round(data.get("deep", 0), 1),
                    round(data.get("rem", 0), 1),
                    round(data.get("light", 0), 1),
                    round(data.get("awake", 0), 1),
                    "apple",
                ),
            )
            sleep_count += 1

        conn.commit()
        logger.info(f"Saved {sleep_count} sleep records from Apple Health")
        logger.info("Apple Health XML import complete.")

    except Exception as e:
        logger.error(f"Error parsing Apple Health XML: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Import Apple Health XML export")
    parser.add_argument("xml_path", help="Path to export.xml file")
    parser.add_argument("--days", type=int, default=90, help="Days of history to import (default: 90)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    import_xml(args.xml_path, args.days)
