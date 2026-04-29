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
    gcal_calendar_id_to_update: str | None = None  # which calendar the matched event lives on
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

    # Date certainty — "specific" (real ISO datetime), "approximate" (best-guess
    # ISO datetime, surfaced visually), or "unknown" (no date determinable;
    # routed to fuzzy_event proposal flow). Defaults to "specific" so the
    # legacy path doesn't break; extractor sets it explicitly.
    date_certainty: str = "specific"
    event_description: str | None = None  # required when date_certainty == "unknown"

    # Confirmation status — "confirmed" (both parties acknowledged), "awaiting_me"
    # (someone proposed; user hasn't acted), "proposed_by_me" (user proposed;
    # other party hasn't replied). Drives the bracketed prefix on calendar
    # titles and the pending_confirmations lifecycle. Inbound default is
    # awaiting_me; outbound default is proposed_by_me; LLM upgrades to
    # confirmed when a thread digest shows mutual agreement.
    confirmation_status: str = "awaiting_me"

    # Gmail thread id, copied from RawMessage.metadata so cross-message
    # confirmation can match by thread.
    thread_id: str | None = None

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


@dataclass
class CandidateTodo:
    title: str                 # short actionable description, max 200 chars
    source: str                # "gmail", "imessage", etc.
    source_id: str             # RawMessage.id — used for fingerprinting
    source_url: str | None     # deep link back to original message
    confidence: float          # 0.0–1.0
    context: str | None        # who/what/where context sentence
    due_date: str | None       # YYYY-MM-DD or None
    priority: str = "normal"   # "urgent" | "high" | "normal" | "low"

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.title = self.title[:200].strip()


@dataclass
class FileAnalysisResult:
    """Result of analyzing an image or PDF uploaded to Slack via Gemini vision.

    Privacy note: structured_text is PRIVATE — same treatment as RawMessage.body_text.
    """
    file_id: str                          # Slack file ID
    primary_category: str                 # NAS top-level folder (e.g. "Healthcare")
    subcategory: str | None               # NAS subfolder (e.g. "0-Ian Healthcare")
    confidence: float                     # 0.0–1.0
    title: str                            # AI-generated descriptive title
    date: str | None                      # YYYY-MM-DD if detected
    structured_text: str                  # extracted content — PRIVATE
    summary: str                          # one-line summary safe for Slack
    calendar_items: list[CandidateEvent] = field(default_factory=list)
    document_type: str = ""               # e.g. "medical_form", "receipt", "insurance_eob"
    original_filename: str = ""
    source_slack_ts: str = ""             # Slack message ts for threading replies

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.title = self.title[:200].strip()
