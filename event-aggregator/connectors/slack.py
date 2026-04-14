"""
Slack connector — Phase 4.

Fetches messages from configured channels since `since` using slack_sdk.WebClient.
Reuses existing SLACK_BOT_TOKEN from .env.

Channel names in SLACK_MONITOR_CHANNELS are resolved to IDs once per connector
instance via conversations.list, then cached for subsequent fetches.

Supports public channels, private channels, and DMs:
- Public/private channels: use the channel name (e.g. "general") or ID (e.g. "C01ABC123")
- DMs: use the DM channel ID (starts with "D", e.g. "D01ABC123") — DMs have no name
- Any raw Slack ID (C/D/G/W prefix) is passed through directly without resolution.

Scopes required: channels:read, channels:history, groups:read, groups:history,
                 im:read, im:history
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import config
from connectors.base import BaseConnector
from models import RawMessage

logger = logging.getLogger(__name__)

# Slack channel/DM IDs always start with one of these letters followed by alphanumerics.
# Entries matching this pattern are passed through directly without name resolution.
_SLACK_ID_PREFIXES = frozenset("CDGW")


class SlackConnector(BaseConnector):
    source_name = "slack"

    def __init__(self) -> None:
        self._channel_id_cache: dict[str, str] = {}  # name → id
        self._workspace_url: str = ""  # e.g. "https://myworkspace.slack.com"
        self._user_name_cache: dict[str, str] = {}  # user_id → display_name

    def fetch(self, since: datetime, mock: bool = False) -> list[RawMessage]:
        if mock:
            from tests.mock_data import slack_messages
            return slack_messages(since)

        if not config.SLACK_BOT_TOKEN:
            logger.warning("SLACK_BOT_TOKEN not set — skipping Slack")
            return []

        if not config.SLACK_MONITOR_CHANNELS:
            logger.debug("SLACK_MONITOR_CHANNELS is empty — nothing to fetch")
            return []

        try:
            from slack_sdk import WebClient
            client = WebClient(token=config.SLACK_BOT_TOKEN)

            # Fetch workspace URL once for building permalinks
            if not self._workspace_url:
                try:
                    auth = client.auth_test()
                    self._workspace_url = auth.get("url", "").rstrip("/")
                except Exception:
                    pass

            messages: list[RawMessage] = []
            for channel_name in config.SLACK_MONITOR_CHANNELS:
                try:
                    messages.extend(self._fetch_channel(client, channel_name, since))
                except Exception as exc:
                    logger.warning("slack: failed to fetch channel %s: %s", channel_name, exc)

            logger.debug("slack: fetched %d message(s) since %s", len(messages), since.date())
            return messages

        except Exception as exc:
            logger.warning("slack connector error: %s", exc)
            return []

    def _resolve_channel_id(self, client, channel_name: str) -> str | None:
        """
        Resolve a channel name or ID to its Slack channel ID.

        - Raw Slack IDs (C/D/G/W prefix) are returned immediately without lookup.
        - Channel names (with or without #) are resolved via conversations.list,
          which includes public channels, private channels, and DMs (im type).
        - DMs are cached by the other user's ID (the `user` field from the API).
        """
        name = channel_name.lstrip("#")

        # Pass raw Slack IDs through directly — DMs and explicit IDs need no resolution
        if name and name[0].upper() in _SLACK_ID_PREFIXES and name[1:].isalnum():
            return name

        if name in self._channel_id_cache:
            return self._channel_id_cache[name]

        # Populate cache: public + private channels + DMs
        cursor: str | None = None
        while True:
            kwargs: dict = {
                "exclude_archived": True,
                "limit": 200,
                "types": "public_channel,private_channel,im",
            }
            if cursor:
                kwargs["cursor"] = cursor
            result = client.conversations_list(**kwargs)
            for ch in result.get("channels", []):
                if ch.get("name"):
                    self._channel_id_cache[ch["name"]] = ch["id"]
                # DMs: also cache by the other user's ID so "U01ABC" works as a name
                if ch.get("is_im") and ch.get("user"):
                    self._channel_id_cache[ch["user"]] = ch["id"]
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        resolved = self._channel_id_cache.get(name)
        if not resolved:
            logger.warning(
                "slack: channel %r not found — check the name/ID and that the bot is invited",
                channel_name,
            )
        return resolved

    def _fetch_channel(
        self, client, channel_name: str, since: datetime
    ) -> list[RawMessage]:
        channel_id = self._resolve_channel_id(client, channel_name)
        if not channel_id:
            return []

        result = client.conversations_history(
            channel=channel_id,
            oldest=str(since.timestamp()),
            limit=200,
        )
        messages = []
        for msg in result.get("messages", []):
            text = msg.get("text", "").strip()
            # Skip system/bot messages and empty text
            if not text or msg.get("subtype"):
                continue
            ts = datetime.fromtimestamp(float(msg["ts"]), tz=timezone.utc)
            # Slack permalink: workspace_url/archives/CHANNEL_ID/pTIMESTAMP (no dot)
            source_url = None
            if self._workspace_url:
                ts_nodot = msg["ts"].replace(".", "")
                source_url = f"{self._workspace_url}/archives/{channel_id}/p{ts_nodot}"
            sender_name = self._get_sender_name(client, msg.get("user", ""))
            messages.append(
                RawMessage(
                    id=f"slack_{msg['ts'].replace('.', '_')}",
                    source=self.source_name,
                    timestamp=ts,
                    body_text=text,
                    metadata={
                        "channel": channel_name,
                        "channel_id": channel_id,
                        "sender_name": sender_name,
                        "source_url": source_url,
                    },
                )
            )
        return messages

    def _get_sender_name(self, client, user_id: str) -> str:
        """Return display name for a Slack user ID. Cached per connector instance."""
        if not user_id:
            return ""
        if user_id in self._user_name_cache:
            return self._user_name_cache[user_id]
        try:
            result = client.users_info(user=user_id)
            profile = result.get("user", {}).get("profile", {})
            name = profile.get("display_name") or profile.get("real_name") or user_id
        except Exception:
            name = user_id
        self._user_name_cache[user_id] = name
        return name
