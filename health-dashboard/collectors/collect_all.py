"""Run all collectors to gather health data from all sources."""

import argparse
import logging
import socket
import sys
import time

from .db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Garmin/Strava client libs wrap `requests` with no per-call timeout, so a hung
# connection would block until the job's 900s ceiling. A process-wide socket
# timeout bounds every network call.
NETWORK_TIMEOUT_SECONDS = 30
# A single transient blip shouldn't cost a whole day's data.
MAX_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 10


def _collect_with_retry(name: str, fn, *args) -> None:
    """Run one collector, retrying once on transient failure with backoff."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            fn(*args)
            return
        except Exception as e:
            if attempt < MAX_ATTEMPTS:
                logger.warning("%s collection attempt %d/%d failed: %s — retrying in %ds",
                               name, attempt, MAX_ATTEMPTS, e, RETRY_BACKOFF_SECONDS)
                time.sleep(RETRY_BACKOFF_SECONDS)
            else:
                logger.error("%s collection failed after %d attempts: %s", name, MAX_ATTEMPTS, e)


def main(days_back: int = 7):
    """Run all data collectors."""
    socket.setdefaulttimeout(NETWORK_TIMEOUT_SECONDS)
    init_db()

    from . import garmin_collector, strava_collector, apple_health
    _collect_with_retry("Garmin", garmin_collector.collect_all, days_back)
    _collect_with_retry("Strava", strava_collector.collect_all, days_back)
    _collect_with_retry("Apple Health", apple_health.collect_all, days_back)

    # NOTE: Suunto (via Intervals.icu) was retired 2026-05-30 — device gone.
    # Wellness/HRV/sleep-score data now comes from Garmin.

    # Strava mirrors every Garmin workout, so the same session lands twice.
    # Mark the cross-source duplicates (keeping the recording device's copy) so
    # totals, weekly load and TRIMP don't double-count. Cheap, local, idempotent.
    from . import dedupe
    _collect_with_retry("De-dup", dedupe.dedupe_activities)

    logger.info("All collection complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect health data from all sources")
    parser.add_argument("--days", type=int, default=7, help="Days of history to collect (default: 7)")
    args = parser.parse_args()
    main(args.days)
