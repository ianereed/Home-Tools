"""
Discord connector — Phase 4.

Uses requests directly against the Discord REST API (no discord.py).
Bot requires OAuth2 scopes: bot + Read Messages + Read Message History only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from connectors.base import BaseConnector
import config
from models import RawMessage

logger = logging.getLogger(__name__)

_DISCORD_API = "https://discord.com/api/v10"
_MESSAGES_PER_REQUEST = 100  # Discord API max


def _snowflake_from_dt(dt: datetime) -> int:
    """Convert datetime to Discord snowflake ID for use as `after` parameter."""
    # Discord epoch: 2015-01-01T00:00:00Z = 1420070400000 ms
    discord_epoch_ms = 1420070400000
    ts_ms = int(dt.timestamp() * 1000)
    return (ts_ms - discord_epoch_ms) << 22


def _snowflake_to_dt(snowflake: int) -> datetime:
    discord_epoch_ms = 1420070400000
    ts_ms = (snowflake >> 22) + discord_epoch_ms
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


class DiscordConnector(BaseConnector):
    source_name = "discord"

    def __init__(self) -> None:
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bot {config.DISCORD_BOT_TOKEN}",
                "User-Agent": "HomeToolsEventAggregator/1.0",
            })
        return self._session

    def fetch(self, since: datetime, mock: bool = False) -> list[RawMessage]:
        if mock:
            from tests.mock_data import discord_messages
            return discord_messages(since)

        if not config.DISCORD_BOT_TOKEN:
            logger.warning("DISCORD_BOT_TOKEN not set — skipping Discord")
            return []

        messages = []
        session = self._get_session()

        for channel_id in config.DISCORD_MONITOR_CHANNELS:
            try:
                messages.extend(self._fetch_channel(session, channel_id, since))
            except Exception as exc:
                logger.warning("discord: failed to fetch channel %s: %s", channel_id, exc)

        logger.debug("discord: fetched %d messages since %s", len(messages), since.date())
        return messages

    def _fetch_channel(
        self, session: requests.Session, channel_id: str, since: datetime
    ) -> list[RawMessage]:
        after_snowflake = _snowflake_from_dt(since)
        results = []

        resp = session.get(
            f"{_DISCORD_API}/channels/{channel_id}/messages",
            params={"after": str(after_snowflake), "limit": _MESSAGES_PER_REQUEST},
            timeout=10,
        )
        resp.raise_for_status()

        for msg in resp.json():
            if not msg.get("content"):
                continue
            ts = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
            results.append(
                RawMessage(
                    id=f"discord_{msg['id']}",
                    source=self.source_name,
                    timestamp=ts,
                    body_text=msg["content"],
                    metadata={"channel_id": channel_id},
                )
            )
        return results
