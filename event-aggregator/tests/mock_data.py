"""
Synthetic test data generators for all 8 message sources.

THIS IS THE ONLY SOURCE OF TEST DATA.
Real message content must NEVER appear here or anywhere in the test suite.
All names, dates, and locations are invented.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from models import RawMessage

_SOURCES = [
    "gmail", "gcal", "slack", "imessage",
    "whatsapp", "discord", "messenger", "instagram",
]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _msg(
    source: str,
    msg_id: str,
    body: str,
    offset_hours: int = 0,
    metadata: dict | None = None,
) -> RawMessage:
    return RawMessage(
        id=f"{source}_{msg_id}",
        source=source,
        timestamp=_utcnow() - timedelta(hours=offset_hours),
        body_text=body,
        metadata=metadata or {},
    )


# ── Per-source generators ────────────────────────────────────────────────────

def gmail_messages(since: datetime) -> list[RawMessage]:
    return [
        _msg("gmail", "001", "Hey, team lunch at Rosewood Cafe next Friday at noon?", 2),
        _msg("gmail", "002", "Reminder: board meeting on the 15th at 2pm, Conference Room B", 5),
        _msg("gmail", "003", "No events in this email, just catching up!", 1),
    ]


def gcal_messages(since: datetime) -> list[RawMessage]:
    return [
        _msg("gcal", "inv001", "You're invited: Q2 Planning Session on April 22 at 10am", 1),
    ]


def slack_messages(since: datetime) -> list[RawMessage]:
    return [
        _msg("slack", "s001", "Anyone up for a team standup Tuesday morning at 9?", 3,
             {"channel": "general"}),
        _msg("slack", "s002", "The offsite is confirmed for May 3rd at the Marriott downtown", 6,
             {"channel": "announcements"}),
    ]


def imessage_messages(since: datetime) -> list[RawMessage]:
    return [
        _msg("imessage", "i001", "Dinner Saturday at 7pm, La Paloma on 5th Ave?", 10),
        _msg("imessage", "i002", "Just saying hi, no plans here", 8),
    ]


def whatsapp_messages(since: datetime) -> list[RawMessage]:
    return [
        _msg("whatsapp", "w001", "Family reunion July 4th weekend at Grandma's place!", 24),
        _msg("whatsapp", "w002", "Don't forget dentist appt Thu 3pm", 12),
    ]


def discord_messages(since: datetime) -> list[RawMessage]:
    return [
        _msg("discord", "d001", "Game night at my place Friday 8pm, RSVP in the thread", 4,
             {"channel_id": "123456789"}),
    ]


def notification_messages(since: datetime) -> list[RawMessage]:
    return [
        RawMessage(
            id="messenger_notif_001",
            source="messenger",
            timestamp=_utcnow() - timedelta(hours=2),
            body_text="Alex: Movie night Sat 6pm — you in?",
            metadata={},
        ),
        RawMessage(
            id="instagram_notif_001",
            source="instagram",
            timestamp=_utcnow() - timedelta(hours=1),
            body_text="Jordan sent you a message",  # truncated — no event extractable
            metadata={},
        ),
    ]


# ── Combined generator ───────────────────────────────────────────────────────

def all_messages(since: datetime) -> list[RawMessage]:
    """Return mock messages from all sources combined."""
    generators: list[Callable[[datetime], list[RawMessage]]] = [
        gmail_messages,
        gcal_messages,
        slack_messages,
        imessage_messages,
        whatsapp_messages,
        discord_messages,
        notification_messages,
    ]
    results = []
    for gen in generators:
        results.extend(gen(since))
    return results
