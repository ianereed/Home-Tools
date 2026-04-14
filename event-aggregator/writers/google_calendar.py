"""
Google Calendar writer.

Writes CandidateEvents to Google Calendar, and supports update and delete.
- Pre-write dedup: checks existing events ±1 day by title similarity, plus cross-calendar
  snapshot check to avoid duplicating events already on other calendars
- Source attribution: writes "[via event-aggregator | source: {source_type}]" to description
- Conflict detection: reports if another event exists within ±30 minutes at the same time
- Category color: applies GCal colorId based on event.category
- Timezone: uses config.USER_TIMEZONE for start/end objects
- OAuth2 token stored via keyring (macOS Keychain) with JSON file as fallback
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build
from thefuzz import fuzz

import config
import dedup
from connectors import google_auth
from models import CandidateEvent, WrittenEvent

logger = logging.getLogger(__name__)

_GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
_FUZZY_DEDUP_THRESHOLD = 85
_CROSS_CALENDAR_THRESHOLD = 80
_CONFLICT_WINDOW_MINUTES = 30


def _get_service():
    creds = google_auth.get_credentials(
        scopes=_GCAL_SCOPES,
        token_path=config.GCAL_TOKEN_JSON,
        credentials_path=config.GMAIL_CREDENTIALS_JSON,
        keyring_key="gcal_token",
    )
    return build("calendar", "v3", credentials=creds)


def _build_description(candidate: CandidateEvent, action: str = "created") -> str:
    source_part = candidate.source
    if candidate.source_url:
        source_part = f"{candidate.source} | {candidate.source_url}"
    return f"[{action} via event-aggregator | source: {source_part}]"


def _build_event_body(candidate: CandidateEvent, description: str) -> dict:
    """Build the GCal API event body dict."""
    end_dt = candidate.end_dt or (candidate.start_dt + timedelta(hours=1))
    body: dict = {
        "summary": _display_title(candidate),
        "start": {
            "dateTime": candidate.start_dt.isoformat(),
            "timeZone": config.USER_TIMEZONE,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": config.USER_TIMEZONE,
        },
        "description": description,
    }
    if candidate.location:
        body["location"] = candidate.location
    color_id = config.CATEGORY_COLORS.get(candidate.category)
    if color_id:
        body["colorId"] = color_id
    return body


def _display_title(candidate: CandidateEvent) -> str:
    """Returns title with [?] prefix for medium-confidence events."""
    if candidate.confidence_band == "medium":
        return f"[?] {candidate.title}"
    return candidate.title


def _check_conflicts(service, candidate: CandidateEvent) -> list[str]:
    """
    Query the target calendar for events within ±CONFLICT_WINDOW_MINUTES of start_dt.
    Returns list of conflicting event titles (may be empty).
    """
    window = timedelta(minutes=_CONFLICT_WINDOW_MINUTES)
    time_min = (candidate.start_dt - window).isoformat()
    time_max = (candidate.start_dt + window).isoformat()
    try:
        result = (
            service.events()
            .list(
                calendarId=config.GCAL_TARGET_CALENDAR_ID,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
            )
            .execute()
        )
        return [
            item.get("summary", "")
            for item in result.get("items", [])
            if item.get("summary")
        ]
    except Exception as exc:
        logger.debug("conflict check failed: %s", exc)
        return []


def _is_cross_calendar_duplicate(candidate: CandidateEvent, snapshot: dict) -> bool:
    """
    Check the calendar snapshot (all calendars) for a near-duplicate of this candidate.
    Returns True if a matching event is found — caller should skip creation.
    """
    cand_date = candidate.start_dt.date()
    for gcal_id, info in snapshot.items():
        existing_title = info.get("title", "")
        existing_start = info.get("start", "")
        if not existing_start:
            continue
        try:
            existing_dt = datetime.fromisoformat(existing_start)
            if existing_dt.date() != cand_date:
                continue
        except (ValueError, TypeError):
            continue
        if fuzz.ratio(candidate.title.lower(), existing_title.lower()) > _CROSS_CALENDAR_THRESHOLD:
            logger.debug(
                "cross-calendar dedup: %r matches snapshot event %r — skipping",
                candidate.title, existing_title,
            )
            return True
    return False


def write_event(
    candidate: CandidateEvent,
    dry_run: bool = False,
    snapshot: dict | None = None,
) -> tuple[WrittenEvent | None, list[str]]:
    """
    Write a CandidateEvent to Google Calendar.

    Returns (WrittenEvent | None, conflict_titles).
    WrittenEvent is None on failure, dry-run, or if deduped.
    conflict_titles is a list of event titles that overlap within ±30 minutes.
    """
    fp = dedup.fingerprint(candidate)

    if dry_run:
        logger.info(
            "DRY RUN — would create: %r on %s (confidence=%.2f, band=%s, source=%s)",
            candidate.title,
            candidate.start_dt.date(),
            candidate.confidence,
            candidate.confidence_band,
            candidate.source,
        )
        return None, []

    try:
        service = _get_service()

        # Cross-calendar dedup via snapshot
        if snapshot and _is_cross_calendar_duplicate(candidate, snapshot):
            return None, []

        # Pre-write dedup: scan target calendar ±1 day for title matches
        window_start = (candidate.start_dt - timedelta(days=1)).isoformat()
        window_end = (candidate.start_dt + timedelta(days=1)).isoformat()
        existing = (
            service.events()
            .list(
                calendarId=config.GCAL_TARGET_CALENDAR_ID,
                timeMin=window_start,
                timeMax=window_end,
                singleEvents=True,
            )
            .execute()
        )
        for item in existing.get("items", []):
            existing_title = item.get("summary", "")
            # Strip [?] prefix when comparing titles
            compare_title = candidate.title.lstrip("[?] ") if candidate.title.startswith("[?]") else candidate.title
            if fuzz.ratio(compare_title.lower(), existing_title.lstrip("[?] ").lower()) > _FUZZY_DEDUP_THRESHOLD:
                # Also require time proximity (±30 min) to avoid false positives for
                # same-name events at different times on the same day
                existing_start_str = item.get("start", {}).get("dateTime", "")
                if existing_start_str:
                    try:
                        existing_dt = datetime.fromisoformat(existing_start_str)
                        if existing_dt.tzinfo is None:
                            existing_dt = existing_dt.replace(tzinfo=timezone.utc)
                        if abs((candidate.start_dt - existing_dt).total_seconds()) > 1800:
                            continue
                    except (ValueError, TypeError):
                        pass  # unparseable — treat as time match (conservative)
                logger.debug(
                    "pre-write dedup: %r matches existing %r — skipping",
                    candidate.title, existing_title,
                )
                return None, []

        # Conflict detection (informational only — does not block write)
        conflicts = _check_conflicts(service, candidate)

        description = _build_description(candidate, action="created")
        event_body = _build_event_body(candidate, description)

        created = (
            service.events()
            .insert(calendarId=config.GCAL_TARGET_CALENDAR_ID, body=event_body)
            .execute()
        )
        logger.info(
            "gcal write: %r on %s → event id %s",
            candidate.title, candidate.start_dt.date(), created["id"],
        )
        return WrittenEvent(
            gcal_event_id=created["id"],
            fingerprint=fp,
            candidate=candidate,
        ), conflicts

    except FileNotFoundError as exc:
        logger.warning("gcal writer: credentials not set up — %s", exc)
        return None, []
    except Exception as exc:
        logger.warning("gcal writer error: %s", exc)
        return None, []


def update_event(
    gcal_event_id: str,
    candidate: CandidateEvent,
    dry_run: bool = False,
) -> tuple[WrittenEvent | None, list[str]]:
    """
    Patch an existing GCal event with new title, time, location, and category.

    Returns (WrittenEvent | None, conflict_titles).
    """
    fp = dedup.fingerprint(candidate)

    if dry_run:
        logger.info(
            "DRY RUN — would update gcal_id=%s: %r on %s (source=%s)",
            gcal_event_id, candidate.title, candidate.start_dt.date(), candidate.source,
        )
        return None, []

    try:
        service = _get_service()
        conflicts = _check_conflicts(service, candidate)
        description = _build_description(candidate, action="updated")
        event_body = _build_event_body(candidate, description)

        updated = (
            service.events()
            .patch(
                calendarId=config.GCAL_TARGET_CALENDAR_ID,
                eventId=gcal_event_id,
                body=event_body,
            )
            .execute()
        )
        logger.info(
            "gcal update: %r on %s → event id %s",
            candidate.title, candidate.start_dt.date(), updated["id"],
        )
        return WrittenEvent(
            gcal_event_id=updated["id"],
            fingerprint=fp,
            candidate=candidate,
        ), conflicts

    except FileNotFoundError as exc:
        logger.warning("gcal writer: credentials not set up — %s", exc)
        return None, []
    except Exception as exc:
        logger.warning("gcal update error for %s: %s", gcal_event_id, exc)
        return None, []


def delete_event(gcal_event_id: str, dry_run: bool = False) -> bool:
    """
    Delete a GCal event by ID.

    Returns True on success (or in dry-run mode).
    """
    if dry_run:
        logger.info("DRY RUN — would delete gcal_id=%s", gcal_event_id)
        return True

    try:
        service = _get_service()
        service.events().delete(
            calendarId=config.GCAL_TARGET_CALENDAR_ID,
            eventId=gcal_event_id,
        ).execute()
        logger.info("gcal delete: event id %s removed", gcal_event_id)
        return True
    except FileNotFoundError as exc:
        logger.warning("gcal writer: credentials not set up — %s", exc)
        return False
    except Exception as exc:
        logger.warning("gcal delete error for %s: %s", gcal_event_id, exc)
        return False
