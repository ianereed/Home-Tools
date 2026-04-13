"""Run all collectors to gather health data from all sources."""

import argparse
import logging
import sys

from .db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main(days_back: int = 7):
    """Run all data collectors."""
    init_db()

    # Garmin
    try:
        from . import garmin_collector
        garmin_collector.collect_all(days_back)
    except Exception as e:
        logger.error(f"Garmin collection failed: {e}")

    # Strava
    try:
        from . import strava_collector
        strava_collector.collect_all(days_back)
    except Exception as e:
        logger.error(f"Strava collection failed: {e}")

    # Apple Health
    try:
        from . import apple_health
        apple_health.collect_all(days_back)
    except Exception as e:
        logger.error(f"Apple Health collection failed: {e}")

    # Suunto (via Intervals.icu)
    try:
        from . import intervals_collector
        intervals_collector.collect_all(days_back)
    except Exception as e:
        logger.error(f"Intervals.icu (Suunto) collection failed: {e}")

    logger.info("All collection complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect health data from all sources")
    parser.add_argument("--days", type=int, default=7, help="Days of history to collect (default: 7)")
    args = parser.parse_args()
    main(args.days)
