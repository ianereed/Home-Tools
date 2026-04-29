"""
Google Calendar writer — two-calendar model.

- Reads from PRIMARY + WEEKEND for context and dedup.
- Writes new events ONLY to WEEKEND.
- Update / cancel can target either calendar (chosen by where the matched
  event lives). Spontaneous patches to PRIMARY are blocked here; they go
  through the proposal flow as a "merge" proposal and the caller invokes
  `merge_event(target_calendar_id=PRIMARY, ...)` after approval.
- Additive merges to WEEKEND happen silently (caller posts a notice).

Pre-write decision tree (write_event):
  1. exact fingerprint or fuzzy+window match in `state.written_events`/
     `pending_proposals` (Layer 2/3) — caller already filtered.
  2. cross-calendar snapshot match:
       a. matched on PRIMARY + candidate adds new fields → return
          `MergeRequired` with the additions diff (caller emits proposal).
       b. matched on PRIMARY + nothing new → skip silently.
       c. matched on WEEKEND + candidate adds new fields → silent patch
          on weekend, return `Merged` with the additions diff.
       d. matched on WEEKEND + nothing new → skip silently.
  3. live ±1d scan of WEEKEND only (catches drift the snapshot missed) —
     same logic, restricted to weekend.
  4. otherwise → insert new event on WEEKEND.

Source attribution (description):
  "[<action> via event-aggregator | source: <source>{ | <url>}]"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


# ── Outcomes returned by write_event ──────────────────────────────────────────

@dataclass
class Inserted:
    """A new event was inserted on weekend."""
    written: WrittenEvent
    conflicts: list[str] = field(default_factory=list)


@dataclass
class Merged:
    """An existing weekend event was silently patched with additive fields."""
    target_calendar_id: str
    gcal_event_id: str
    matched_title: str
    additions: dict  # {"location": "...", "attendees": [...], "description": "..."}


@dataclass
class MergeRequired:
    """A primary-calendar event matched; a proposal is required for any patch."""
    target_calendar_id: str
    gcal_event_id: str
    matched_title: str
    matched_start_dt: datetime
    additions: dict


@dataclass
class Skipped:
    """Match found but candidate brought no new info — nothing to do."""
    reason: str
    matched_title: str | None = None
    target_calendar_id: str | None = None


WriteOutcome = Inserted | Merged | MergeRequired | Skipped


# ── helpers ───────────────────────────────────────────────────────────────────

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


_STATUS_PREFIX_AWAITING = "[awaiting]"
_STATUS_PREFIX_PROPOSED = "[proposed by you]"
_CONFIDENCE_PREFIX_LOW = "[?]"

# All known leading brackets we may have to strip when comparing or rewriting.
_KNOWN_PREFIXES = (
    _STATUS_PREFIX_AWAITING + " ",
    _STATUS_PREFIX_PROPOSED + " ",
    _CONFIDENCE_PREFIX_LOW + " ",
)


def _status_prefix(confirmation_status: str) -> str:
    """Bracketed prefix (with trailing space) for non-confirmed events, or ''."""
    if confirmation_status == "awaiting_me":
        return _STATUS_PREFIX_AWAITING + " "
    if confirmation_status == "proposed_by_me":
        return _STATUS_PREFIX_PROPOSED + " "
    return ""


def strip_status_prefix(title: str) -> str:
    """Strip any leading status / confidence bracket. Idempotent."""
    if not title:
        return title
    changed = True
    out = title
    while changed:
        changed = False
        for prefix in _KNOWN_PREFIXES:
            if out.startswith(prefix):
                out = out[len(prefix):]
                changed = True
                break
    return out


def _display_title(candidate: CandidateEvent) -> str:
    """Compose the final GCal summary with at most one leading prefix.

    Status (awaiting / proposed_by_me) takes precedence over the legacy
    confidence-band prefix — a tagged event is implicitly tentative. Only
    confirmed-but-medium-confidence keeps `[?]`.
    """
    status_pref = _status_prefix(candidate.confirmation_status)
    if status_pref:
        return f"{status_pref}{candidate.title}"
    if candidate.confidence_band == "medium":
        return f"{_CONFIDENCE_PREFIX_LOW} {candidate.title}"
    return candidate.title


def _check_conflicts(service, candidate: CandidateEvent) -> list[str]:
    """
    Query the WEEKEND calendar for events within ±CONFLICT_WINDOW_MINUTES of start_dt.
    Returns list of conflicting event titles. (Conflicts are informational; the
    primary calendar is checked separately via the snapshot in the proposal flow.)
    """
    window = timedelta(minutes=_CONFLICT_WINDOW_MINUTES)
    time_min = (candidate.start_dt - window).isoformat()
    time_max = (candidate.start_dt + window).isoformat()
    try:
        result = (
            service.events()
            .list(
                calendarId=config.GCAL_WEEKEND_CALENDAR_ID,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
            )
            .execute()
        )
        return [
            item.get("summary", "")
            for item in result.get("items", [])
            if item.get("summary") and not item.get("start", {}).get("date")
        ]
    except Exception as exc:
        logger.debug("conflict check failed: %s", exc)
        return []


def _compute_merge_additions(candidate: CandidateEvent, existing: dict) -> dict:
    """
    Given a candidate and a snapshot dict for an existing event, return the
    additive fields the candidate would contribute. Empty dict = nothing new.

    We add fields conservatively — never replace, only fill in:
      - location: only if existing has no location and candidate has one
      - attendees: only emails the existing event doesn't already list
      - description: append a "[merged from <source> on <date>] <new info>"
        line if the candidate brought location/attendees we don't have
    """
    additions: dict = {}

    existing_location = (existing.get("location") or "").strip()
    if candidate.location and not existing_location:
        additions["location"] = candidate.location

    existing_attendees = existing.get("attendees") or []
    existing_emails = {
        (a.get("email") or "").lower()
        for a in existing_attendees
        if a.get("email")
    }
    new_attendees = []
    for a in candidate.suggested_attendees or []:
        email = (a.get("email") or "").lower()
        if email and email not in existing_emails:
            new_attendees.append({"name": a.get("name", ""), "email": a.get("email", "")})
    if new_attendees:
        additions["attendees"] = new_attendees

    return additions


def _patch_with_additions(
    service, calendar_id: str, gcal_event_id: str, additions: dict, candidate: CandidateEvent
) -> bool:
    """Apply additive fields to an existing event via events().patch."""
    if not additions:
        return False
    body: dict = {}
    if "location" in additions:
        body["location"] = additions["location"]
    if "attendees" in additions:
        # GCal patch replaces attendees — fetch existing and merge.
        try:
            existing = service.events().get(
                calendarId=calendar_id, eventId=gcal_event_id
            ).execute()
            merged_attendees = list(existing.get("attendees") or [])
            existing_emails = {(a.get("email") or "").lower() for a in merged_attendees}
            for a in additions["attendees"]:
                if a.get("email", "").lower() not in existing_emails:
                    merged_attendees.append(a)
            body["attendees"] = merged_attendees
        except Exception as exc:
            logger.warning("merge: couldn't fetch existing attendees, skipping merge: %s", exc)
            body.pop("attendees", None)

    note = (
        f"\n\n[merged via event-aggregator on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')} | source: {candidate.source}]"
    )
    try:
        existing = service.events().get(
            calendarId=calendar_id, eventId=gcal_event_id
        ).execute()
        body["description"] = (existing.get("description") or "") + note
    except Exception:
        body["description"] = note.lstrip()

    try:
        service.events().patch(
            calendarId=calendar_id, eventId=gcal_event_id, body=body
        ).execute()
        logger.info(
            "merged %s into gcal event %s on %s: keys=%s",
            list(additions.keys()), gcal_event_id, calendar_id, list(body.keys()),
        )
        return True
    except Exception as exc:
        logger.warning("merge patch failed for %s: %s", gcal_event_id, exc)
        return False


def _find_cross_calendar_match(
    candidate: CandidateEvent, snapshot: dict
) -> tuple[str, dict] | None:
    """Scan the snapshot for a same-date fuzzy match. Returns (gcal_id, info) or None."""
    cand_date = candidate.start_dt.date()
    cand_lower = strip_status_prefix(candidate.title).lower()
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
        if fuzz.ratio(cand_lower, strip_status_prefix(existing_title).lower()) > _CROSS_CALENDAR_THRESHOLD:
            return gcal_id, info
    return None


# ── public surface ───────────────────────────────────────────────────────────

def write_event(
    candidate: CandidateEvent,
    dry_run: bool = False,
    snapshot: dict | None = None,
) -> WriteOutcome:
    """
    Decide what to do with a CandidateEvent against the two-calendar model.

    Returns one of:
      Inserted        — event was created on weekend
      Merged          — silent additive patch applied to weekend
      MergeRequired   — primary calendar matched; caller must propose
      Skipped         — duplicate / nothing to do
    """
    fp = dedup.fingerprint(candidate)

    if dry_run:
        logger.info(
            "DRY RUN — would create on weekend: %r on %s (confidence=%.2f, band=%s, source=%s)",
            candidate.title,
            candidate.start_dt.date(),
            candidate.confidence,
            candidate.confidence_band,
            candidate.source,
        )
        return Skipped(reason="dry_run")

    try:
        service = _get_service()

        # Cross-calendar dedup via snapshot — branches by which calendar matched.
        if snapshot:
            match = _find_cross_calendar_match(candidate, snapshot)
            if match:
                matched_gcal_id, matched_info = match
                matched_calendar = matched_info.get("calendar_id", "") or config.GCAL_PRIMARY_CALENDAR_ID
                additions = _compute_merge_additions(candidate, matched_info)
                matched_title = matched_info.get("title", "")
                if matched_calendar == config.GCAL_WEEKEND_CALENDAR_ID:
                    if not additions:
                        logger.debug(
                            "weekend dup — nothing new to merge: %r ↔ %r",
                            candidate.title, matched_title,
                        )
                        return Skipped(
                            reason="weekend_duplicate",
                            matched_title=matched_title,
                            target_calendar_id=matched_calendar,
                        )
                    if _patch_with_additions(
                        service, matched_calendar, matched_gcal_id, additions, candidate
                    ):
                        return Merged(
                            target_calendar_id=matched_calendar,
                            gcal_event_id=matched_gcal_id,
                            matched_title=matched_title,
                            additions=additions,
                        )
                    return Skipped(reason="merge_failed", matched_title=matched_title)
                # primary (or any non-weekend calendar): merges require approval
                if not additions:
                    logger.debug(
                        "primary dup — nothing new to propose: %r ↔ %r",
                        candidate.title, matched_title,
                    )
                    return Skipped(
                        reason="primary_duplicate",
                        matched_title=matched_title,
                        target_calendar_id=matched_calendar,
                    )
                try:
                    matched_start_dt = datetime.fromisoformat(matched_info.get("start", ""))
                    if matched_start_dt.tzinfo is None:
                        matched_start_dt = matched_start_dt.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    matched_start_dt = candidate.start_dt
                return MergeRequired(
                    target_calendar_id=matched_calendar,
                    gcal_event_id=matched_gcal_id,
                    matched_title=matched_title,
                    matched_start_dt=matched_start_dt,
                    additions=additions,
                )

        # Pre-write live scan against WEEKEND ±1 day for drift the snapshot missed.
        window_start = (candidate.start_dt - timedelta(days=1)).isoformat()
        window_end = (candidate.start_dt + timedelta(days=1)).isoformat()
        existing = (
            service.events()
            .list(
                calendarId=config.GCAL_WEEKEND_CALENDAR_ID,
                timeMin=window_start,
                timeMax=window_end,
                singleEvents=True,
            )
            .execute()
        )
        for item in existing.get("items", []):
            existing_title = item.get("summary", "")
            compare_title = strip_status_prefix(candidate.title)
            if fuzz.ratio(compare_title.lower(), strip_status_prefix(existing_title).lower()) > _FUZZY_DEDUP_THRESHOLD:
                existing_start_str = item.get("start", {}).get("dateTime", "")
                if existing_start_str:
                    try:
                        existing_dt = datetime.fromisoformat(existing_start_str)
                        if existing_dt.tzinfo is None:
                            existing_dt = existing_dt.replace(tzinfo=timezone.utc)
                        if abs((candidate.start_dt - existing_dt).total_seconds()) > 1800:
                            continue
                    except (ValueError, TypeError):
                        pass
                logger.debug(
                    "pre-write dedup: %r matches existing weekend event %r — skipping",
                    candidate.title, existing_title,
                )
                return Skipped(
                    reason="weekend_live_duplicate",
                    matched_title=existing_title,
                    target_calendar_id=config.GCAL_WEEKEND_CALENDAR_ID,
                )

        # Conflict detection (informational only — does not block write)
        conflicts = _check_conflicts(service, candidate)

        description = _build_description(candidate, action="created")
        event_body = _build_event_body(candidate, description)

        created = (
            service.events()
            .insert(calendarId=config.GCAL_WEEKEND_CALENDAR_ID, body=event_body)
            .execute()
        )
        logger.info(
            "gcal write (weekend): %r on %s → event id %s",
            candidate.title, candidate.start_dt.date(), created["id"],
        )
        return Inserted(
            written=WrittenEvent(
                gcal_event_id=created["id"],
                fingerprint=fp,
                candidate=candidate,
            ),
            conflicts=conflicts,
        )

    except FileNotFoundError as exc:
        logger.warning("gcal writer: credentials not set up — %s", exc)
        return Skipped(reason="creds_missing")
    except Exception as exc:
        logger.warning("gcal writer error: %s", exc)
        return Skipped(reason="error")


def update_event(
    target_calendar_id: str,
    gcal_event_id: str,
    candidate: CandidateEvent,
    dry_run: bool = False,
) -> tuple[WrittenEvent | None, list[str]]:
    """
    Patch an existing GCal event (on the specified calendar) with new title,
    time, location, and category.

    Returns (WrittenEvent | None, conflict_titles).
    """
    fp = dedup.fingerprint(candidate)

    if dry_run:
        logger.info(
            "DRY RUN — would update gcal_id=%s on %s: %r on %s (source=%s)",
            gcal_event_id, target_calendar_id, candidate.title,
            candidate.start_dt.date(), candidate.source,
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
                calendarId=target_calendar_id,
                eventId=gcal_event_id,
                body=event_body,
            )
            .execute()
        )
        logger.info(
            "gcal update on %s: %r on %s → event id %s",
            target_calendar_id, candidate.title, candidate.start_dt.date(), updated["id"],
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
        logger.warning("gcal update error for %s on %s: %s", gcal_event_id, target_calendar_id, exc)
        return None, []


def confirm_event(
    target_calendar_id: str,
    gcal_event_id: str,
    dry_run: bool = False,
) -> bool:
    """Strip the leading status prefix from an event's summary.

    Used when a tagged calendar event is confirmed via Slack approve, GCal
    direct edit, or thread-confirmation reply — preserves all other fields,
    only patches the title. Idempotent.
    """
    if dry_run:
        logger.info(
            "DRY RUN — would strip status prefix from gcal_id=%s on %s",
            gcal_event_id, target_calendar_id,
        )
        return True
    try:
        service = _get_service()
        existing = service.events().get(
            calendarId=target_calendar_id, eventId=gcal_event_id,
        ).execute()
        current_title = existing.get("summary", "")
        stripped = strip_status_prefix(current_title)
        if stripped == current_title:
            return True  # nothing to strip
        service.events().patch(
            calendarId=target_calendar_id,
            eventId=gcal_event_id,
            body={"summary": stripped},
        ).execute()
        logger.info(
            "gcal confirm: stripped tag on %s (%r → %r)",
            gcal_event_id, current_title, stripped,
        )
        return True
    except FileNotFoundError as exc:
        logger.warning("gcal writer: credentials not set up — %s", exc)
        return False
    except Exception as exc:
        logger.warning(
            "gcal confirm error for %s on %s: %s",
            gcal_event_id, target_calendar_id, exc,
        )
        return False


def delete_event(
    target_calendar_id: str,
    gcal_event_id: str,
    dry_run: bool = False,
) -> bool:
    """Delete a GCal event by ID on the specified calendar."""
    if dry_run:
        logger.info("DRY RUN — would delete gcal_id=%s on %s", gcal_event_id, target_calendar_id)
        return True

    try:
        service = _get_service()
        service.events().delete(
            calendarId=target_calendar_id,
            eventId=gcal_event_id,
        ).execute()
        logger.info("gcal delete: event id %s removed from %s", gcal_event_id, target_calendar_id)
        return True
    except FileNotFoundError as exc:
        logger.warning("gcal writer: credentials not set up — %s", exc)
        return False
    except Exception as exc:
        logger.warning("gcal delete error for %s on %s: %s", gcal_event_id, target_calendar_id, exc)
        return False


def merge_event(
    target_calendar_id: str,
    gcal_event_id: str,
    candidate: CandidateEvent,
    additions: dict,
    dry_run: bool = False,
) -> bool:
    """
    Apply pre-computed additive fields to an existing event. Used by the
    proposal-approval path for primary-calendar merges and (internally) for
    silent weekend merges.
    """
    if dry_run:
        logger.info(
            "DRY RUN — would merge into gcal_id=%s on %s: keys=%s",
            gcal_event_id, target_calendar_id, list(additions.keys()),
        )
        return True
    try:
        service = _get_service()
        return _patch_with_additions(
            service, target_calendar_id, gcal_event_id, additions, candidate
        )
    except Exception as exc:
        logger.warning("merge_event failed: %s", exc)
        return False
