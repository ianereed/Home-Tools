"""
Event log — appends a JSONL entry for every event created/updated/cancelled.

The JSONL file (event_log.jsonl, gitignored) is the durable on-disk audit trail.
Slack notifications are sent via the channel thread notifier in main.py.

Log entry schema:
  {
    "ts": "ISO8601",
    "action": "created|updated|cancelled",
    "gcal_id": "...",
    "title": "...",
    "start": "YYYY-MM-DDTHH:MM:SS+HH:MM",
    "source": "gmail|slack|...",
    "fingerprint": "...",
    "confidence": 0.0,
    "confidence_band": "medium|high",
    "category": "work|personal|...",
    "suggested_attendees": [{"name": "...", "email": "..."}],
    "conflicts": ["Event title", ...]
  }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import WrittenEvent

logger = logging.getLogger(__name__)

LOG_PATH = Path(__file__).parent.parent / "event_log.jsonl"


def record(
    written: WrittenEvent,
    action: str = "created",
    conflicts: list[str] | None = None,
) -> None:
    """Append to JSONL log."""
    candidate = written.candidate
    entry: dict[str, Any] = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "action": action,
        "gcal_id": written.gcal_event_id,
        "title": candidate.title,
        "start": candidate.start_dt.isoformat(),
        "source": candidate.source,
        "fingerprint": written.fingerprint,
        "confidence": round(candidate.confidence, 3),
        "confidence_band": candidate.confidence_band,
        "category": candidate.category,
        "suggested_attendees": candidate.suggested_attendees,
        "conflicts": conflicts or [],
    }
    _append_to_log(entry)


def record_cancellation(gcal_id: str, title: str, source: str) -> None:
    """Append a cancellation entry to the JSONL log."""
    entry: dict[str, Any] = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "action": "cancelled",
        "gcal_id": gcal_id,
        "title": title,
        "source": source,
    }
    _append_to_log(entry)


def _append_to_log(entry: dict) -> None:
    try:
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        logger.error("event_log: failed to write to %s: %s", LOG_PATH, exc)
