"""
Core data models for the local event aggregator pipeline.

Privacy note: body_text on RawMessage is PRIVATE — never log, print, or surface it.
All development/testing uses synthetic data from tests/mock_data.py only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawMessage:
    id: str
    source: str  # "gmail"|"gcal"|"slack"|"imessage"|"whatsapp"|"discord"|"messenger"|"instagram"
    timestamp: datetime  # always UTC-aware; connectors normalize before returning
    body_text: str       # PRIVATE — never log or surface
    metadata: dict[str, Any] = field(default_factory=dict)  # sender, subject, channel only


@dataclass
class CandidateEvent:
    title: str
    start_dt: datetime       # UTC-aware
    end_dt: datetime | None
    location: str | None
    confidence: float        # 0.0–1.0
    source: str
    source_id: str
    source_url: str | None = None  # deep link back to the original message, if available

    # Confidence band — set by extractor based on per-source thresholds
    # "medium" = create with [?] prefix; "high" = create normally
    confidence_band: str = "high"

    # Update / cancel signals — set by extractor from LLM, resolved by main.py
    is_update: bool = False
    original_title_hint: str | None = None   # LLM's best guess at the existing event title
    gcal_event_id_to_update: str | None = None  # resolved by main.py from state/snapshot
    is_cancellation: bool = False

    # Recurrence — flagged by LLM; write is skipped to prevent duplicate creation
    is_recurring: bool = False
    recurrence_hint: str | None = None

    # Attendees suggested by LLM or extracted from message headers
    # Each dict: {"name": str, "email": str | None}
    # Not added to GCal invites yet — surfaced in Slack notification only
    suggested_attendees: list[dict] = field(default_factory=list)

    # Category for GCal color coding
    category: str = "other"

    def __post_init__(self) -> None:
        # Clamp confidence to valid range
        self.confidence = max(0.0, min(1.0, self.confidence))
        # Sanitize title
        self.title = self.title[:200].strip()


@dataclass
class WrittenEvent:
    gcal_event_id: str
    fingerprint: str  # sha256(title.lower() + date_str)
    candidate: CandidateEvent
