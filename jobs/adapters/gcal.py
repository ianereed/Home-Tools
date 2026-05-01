"""
Google Calendar adapter — writes an event using event-aggregator's existing
google_calendar writer. The Phase 12 framework imports it lazily.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def write_event(output_config: dict, payload: dict) -> dict:
    """Insert a GCal event.

    output_config:
        target: "gcal"
        calendar_id: "primary" | "<weekend-cal-id>" | ...  (required)
    payload:
        summary, start, end, description, location, etc. (passed to writer)
    """
    calendar_id = output_config.get("calendar_id")
    if not calendar_id:
        raise ValueError("gcal adapter: output_config missing 'calendar_id'")

    # Lazy import — event-aggregator's writer imports a chain of google libs.
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "event-aggregator"))
    try:
        from writers import google_calendar as gcal_writer
    finally:
        # Don't permanently mutate sys.path
        try:
            sys.path.remove(str(repo_root / "event-aggregator"))
        except ValueError:
            pass

    outcome = gcal_writer.insert_event(calendar_id=calendar_id, **payload, dry_run=False)
    return {"outcome": str(outcome.__class__.__name__), "details": str(outcome)}
