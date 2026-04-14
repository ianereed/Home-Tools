"""
Google Calendar connector — Phase 6.

Reads pending invites (responseStatus == "needsAction") from the primary calendar
since `since`. Surfaces them as RawMessages so the extractor can create
CandidateEvents from them.

Reuses the same gcal_token / gmail_oauth.json credentials as the GCal writer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from googleapiclient.discovery import build

import config
from connectors import google_auth
from connectors.base import BaseConnector
from models import RawMessage

logger = logging.getLogger(__name__)

_GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class GoogleCalendarConnector(BaseConnector):
    source_name = "gcal"

    def fetch(self, since: datetime, mock: bool = False) -> list[RawMessage]:
        if mock:
            from tests.mock_data import gcal_messages
            return gcal_messages(since)

        try:
            creds = google_auth.get_credentials(
                scopes=_GCAL_SCOPES,
                token_path=config.GCAL_TOKEN_JSON,
                credentials_path=config.GMAIL_CREDENTIALS_JSON,
                keyring_key="gcal_token",
            )
            service = build("calendar", "v3", credentials=creds)

            # Query upcoming events only — no point processing past instances.
            # timeMin = start of today so today's events are included.
            # `since` is intentionally not used here: we want all future pending
            # invites regardless of when they were created or last updated.
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")

            # Paginate in case there are >250 upcoming events.
            all_items: list[dict] = []
            page_token: str | None = None
            while True:
                kwargs: dict = {
                    "calendarId": "primary",
                    "timeMin": now_str,
                    "singleEvents": True,
                    "orderBy": "startTime",
                    "maxResults": 250,
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                result = service.events().list(**kwargs).execute()
                all_items.extend(result.get("items", []))
                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            # Deduplicate recurring series: keep only the next (earliest) instance.
            seen_series: set[str] = set()

            messages: list[RawMessage] = []
            for event in all_items:
                # Only surface events where we have a pending invite (needsAction).
                # Skip events with no attendees — those are your own created events.
                attendees = event.get("attendees", [])
                self_status = next(
                    (a.get("responseStatus") for a in attendees if a.get("self")),
                    None,
                )
                if not attendees or self_status != "needsAction":
                    continue

                # For recurring events, only emit the next upcoming instance.
                series_key = event.get("recurringEventId") or event["id"]
                if series_key in seen_series:
                    continue
                seen_series.add(series_key)

                summary = event.get("summary", "(no title)")
                start = event.get("start", {})
                start_str = start.get("dateTime") or start.get("date", "")
                location = event.get("location", "")
                organizer = event.get("organizer", {}).get("displayName", "")
                description = event.get("description", "")
                event_link = event.get("htmlLink", "")

                # Build a human-readable body the extractor can parse
                parts = [f"Calendar invite: {summary}"]
                if start_str:
                    parts.append(f"When: {start_str}")
                if location:
                    parts.append(f"Where: {location}")
                if organizer:
                    parts.append(f"From: {organizer}")
                if description:
                    parts.append(f"Details: {description[:500]}")

                # Parse timestamp for RawMessage — use event start, fall back to now
                try:
                    ts = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError):
                    ts = datetime.now(timezone.utc)

                # Collect attendee emails (excluding self) for context enrichment
                attendee_emails = [
                    a.get("email", "")
                    for a in attendees
                    if not a.get("self") and a.get("email")
                ]

                messages.append(
                    RawMessage(
                        id=f"gcal_{event['id']}",
                        source=self.source_name,
                        timestamp=ts,
                        body_text="\n".join(parts),
                        metadata={
                            "event_id": event["id"],
                            "summary": summary,
                            "start": start_str,
                            "location": location,
                            "attendees": attendee_emails,
                            "source_url": event_link,
                        },
                    )
                )

            logger.debug(
                "gcal: found %d pending invite(s) since %s", len(messages), since.date()
            )
            return messages

        except Exception as exc:
            logger.warning("gcal connector error: %s", exc)
            return []
