"""
Deduplication logic for candidate events.

Two events are considered duplicates if:
  1. Fingerprints match (sha256 of normalized title + date), OR
  2. fuzz.ratio(title_a, title_b) > 85 AND start times within 60 minutes
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from thefuzz import fuzz

from models import CandidateEvent, CandidateTodo

_FUZZY_THRESHOLD = 85
_TIME_WINDOW = timedelta(minutes=60)


def fingerprint(event: CandidateEvent) -> str:
    """Stable fingerprint: sha256(normalized_title + YYYY-MM-DD)."""
    key = event.title.lower().strip() + event.start_dt.date().isoformat()
    return hashlib.sha256(key.encode()).hexdigest()


def todo_fingerprint(todo: CandidateTodo) -> str:
    """Stable fingerprint: sha256(normalized_title + source + source_id).
    Deduplicates the same todo extracted from the same message across runs."""
    key = todo.title.lower().strip() + todo.source + todo.source_id
    return hashlib.sha256(key.encode()).hexdigest()


def is_duplicate(
    candidate: CandidateEvent,
    existing_events: list[CandidateEvent],
) -> bool:
    """
    Return True if candidate is a duplicate of any event in existing_events.
    existing_events may come from the state file (fingerprints) or a live GCal scan.
    """
    fp = fingerprint(candidate)
    for existing in existing_events:
        time_diff = abs(candidate.start_dt - existing.start_dt)
        if fingerprint(existing) == fp and time_diff <= _TIME_WINDOW:
            return True
        if (
            fuzz.ratio(candidate.title.lower(), existing.title.lower()) > _FUZZY_THRESHOLD
            and time_diff <= _TIME_WINDOW
        ):
            return True
    return False
