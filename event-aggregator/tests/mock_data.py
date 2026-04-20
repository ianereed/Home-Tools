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


def slack_multifile_message(since: datetime) -> list[RawMessage]:
    """Mock a single Slack message with 3 file attachments (multi-page document)."""
    return [
        RawMessage(
            id="slack_file_multipage_001",
            source="slack_file",
            timestamp=_utcnow() - timedelta(hours=1),
            body_text="11-page patient agreement form",
            metadata={
                "channel": "ian-event-aggregator",
                "channel_id": "C_MOCK_NOTIFY",
                "sender_name": "Ian Reed",
                "msg_ts": "1713600100.000001",
                "files": [
                    {
                        "id": "F_MULTI_001",
                        "name": f"page_{i:02d}.jpg",
                        "mimetype": "image/jpeg",
                        "url_private_download": f"https://files.slack.com/mock/page_{i:02d}.jpg",
                        "size": 180000,
                    }
                    for i in range(1, 4)  # 3 pages for test
                ],
                "is_thread_collection": False,
                "auto_processed": False,
            },
        )
    ]


def slack_thread_collection_message(since: datetime) -> list[RawMessage]:
    """Mock a thread collection: 3 pages + 'done' keyword (already resolved by connector)."""
    return [
        RawMessage(
            id="thread_1713600200_000001",
            source="slack_file",
            timestamp=_utcnow() - timedelta(hours=2),
            body_text="Lab results across multiple pages",
            metadata={
                "channel": "ian-event-aggregator",
                "channel_id": "C_MOCK_NOTIFY",
                "sender_name": "Ian Reed",
                "msg_ts": "1713600200.000001",
                "thread_id": "thread_1713600200_000001",
                "files": [
                    {
                        "id": f"F_THREAD_{i:03d}",
                        "name": f"lab_result_page_{i}.png",
                        "mimetype": "image/png",
                        "url_private_download": f"https://files.slack.com/mock/lab_p{i}.png",
                        "size": 220000,
                    }
                    for i in range(1, 4)  # 3 pages from thread
                ],
                "is_thread_collection": True,
                "auto_processed": False,
            },
        )
    ]


def slack_file_messages(since: datetime) -> list[RawMessage]:
    """Mock Slack messages with image/PDF file attachments."""
    return [
        RawMessage(
            id="slack_file_f001",
            source="slack_file",
            timestamp=_utcnow() - timedelta(hours=1),
            body_text="After visit summary from today's appointment",
            metadata={
                "channel": "ian-event-aggregator",
                "channel_id": "C_MOCK_NOTIFY",
                "sender_name": "Ian Reed",
                "msg_ts": "1713600000.000001",
                "files": [
                    {
                        "id": "F_MOCK_001",
                        "name": "after_visit_summary.png",
                        "mimetype": "image/png",
                        "url_private_download": "https://files.slack.com/mock/after_visit_summary.png",
                        "size": 245000,
                    }
                ],
            },
        ),
        RawMessage(
            id="slack_file_f002",
            source="slack_file",
            timestamp=_utcnow() - timedelta(hours=2),
            body_text="Insurance EOB from last month",
            metadata={
                "channel": "ian-event-aggregator",
                "channel_id": "C_MOCK_NOTIFY",
                "sender_name": "Ian Reed",
                "msg_ts": "1713600000.000002",
                "files": [
                    {
                        "id": "F_MOCK_002",
                        "name": "eob_march_2026.pdf",
                        "mimetype": "application/pdf",
                        "url_private_download": "https://files.slack.com/mock/eob_march_2026.pdf",
                        "size": 520000,
                    }
                ],
            },
        ),
        RawMessage(
            id="slack_file_f003",
            source="slack_file",
            timestamp=_utcnow() - timedelta(hours=3),
            body_text="Receipt from Home Depot for deck materials",
            metadata={
                "channel": "ian-event-aggregator",
                "channel_id": "C_MOCK_NOTIFY",
                "sender_name": "Ian Reed",
                "msg_ts": "1713600000.000003",
                "files": [
                    {
                        "id": "F_MOCK_003",
                        "name": "receipt_home_depot.jpg",
                        "mimetype": "image/jpeg",
                        "url_private_download": "https://files.slack.com/mock/receipt_home_depot.jpg",
                        "size": 180000,
                    }
                ],
            },
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
