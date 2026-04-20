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
from datetime import datetime, timedelta, timezone

import requests

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

    # ── File upload detection (image/PDF intake pipeline) ───────────────────

    _IMAGE_PDF_MIMES = frozenset({
        "image/png", "image/jpeg", "image/jpg", "image/gif",
        "image/webp", "image/heic", "image/heif", "image/tiff",
        "application/pdf",
    })
    _TRIGGER_WORDS = frozenset({"done", "process"})
    _THREAD_AUTO_PROCESS_HOURS = 12
    # Always look back at least this far so threads awaiting "done" are rechecked
    _FILE_LOOKBACK_HOURS = 25

    def fetch_files(self, since: datetime, mock: bool = False) -> list[RawMessage]:
        """Fetch messages with image/PDF attachments from SLACK_NOTIFY_CHANNEL.

        Handles three cases:
        - Single file, no thread → process immediately (unchanged behavior)
        - Multiple files, no thread → treat as multi-page document
        - Files in a thread → collect all pages when "done"/"process" keyword
          is posted, or after _THREAD_AUTO_PROCESS_HOURS hours
        """
        if mock:
            from tests.mock_data import (
                slack_file_messages,
                slack_multifile_message,
                slack_thread_collection_message,
            )
            return (
                slack_file_messages(since)
                + slack_multifile_message(since)
                + slack_thread_collection_message(since)
            )

        if not config.SLACK_BOT_TOKEN:
            logger.warning("SLACK_BOT_TOKEN not set — skipping file fetch")
            return []

        notify_channel = config.SLACK_NOTIFY_CHANNEL
        if not notify_channel:
            logger.debug("SLACK_NOTIFY_CHANNEL not set — skipping file fetch")
            return []

        try:
            from slack_sdk import WebClient
            client = WebClient(token=config.SLACK_BOT_TOKEN)

            if not self._workspace_url:
                try:
                    auth = client.auth_test()
                    self._workspace_url = auth.get("url", "").rstrip("/")
                except Exception:
                    pass

            channel_id = self._resolve_channel_id(client, notify_channel)
            if not channel_id:
                return []

            # Always look back at least _FILE_LOOKBACK_HOURS to catch threads
            # awaiting "done" that were posted before the last run.
            lookback_cutoff = datetime.now(timezone.utc) - timedelta(hours=self._FILE_LOOKBACK_HOURS)
            oldest = min(since, lookback_cutoff)

            result = client.conversations_history(
                channel=channel_id,
                oldest=str(oldest.timestamp()),
                limit=200,
            )

            messages = []
            for msg in result.get("messages", []):
                files = msg.get("files", [])
                if not files:
                    continue
                # Filter to image/PDF files only
                matching_files = [
                    {
                        "id": f["id"],
                        "name": f.get("name", "unknown"),
                        "mimetype": f.get("mimetype", ""),
                        "url_private_download": f.get("url_private_download", ""),
                        "size": f.get("size", 0),
                    }
                    for f in files
                    if f.get("mimetype", "") in self._IMAGE_PDF_MIMES
                ]
                if not matching_files:
                    continue

                ts = datetime.fromtimestamp(float(msg["ts"]), tz=timezone.utc)
                text = msg.get("text", "").strip()
                sender_name = self._get_sender_name(client, msg.get("user", ""))
                msg_ts = msg["ts"]
                has_replies = msg.get("reply_count", 0) > 0

                if has_replies:
                    # Thread detected — check if ready to process
                    ready, auto_processed, thread_files = self._check_thread_ready(
                        client, channel_id, msg_ts
                    )
                    if not ready:
                        logger.debug(
                            "Thread %s has files but is not ready yet (no keyword, < %dh old)",
                            msg_ts, self._THREAD_AUTO_PROCESS_HOURS,
                        )
                        continue
                    # Use all files collected from the thread
                    thread_id = f"thread_{msg_ts.replace('.', '_')}"
                    messages.append(
                        RawMessage(
                            id=thread_id,
                            source="slack_file",
                            timestamp=ts,
                            body_text=text,
                            metadata={
                                "channel": notify_channel,
                                "channel_id": channel_id,
                                "sender_name": sender_name,
                                "msg_ts": msg_ts,
                                "files": thread_files,
                                "is_thread_collection": True,
                                "auto_processed": auto_processed,
                                "thread_id": thread_id,
                            },
                        )
                    )
                else:
                    # No thread — single or multi-file message
                    messages.append(
                        RawMessage(
                            id=f"slack_file_{msg_ts.replace('.', '_')}",
                            source="slack_file",
                            timestamp=ts,
                            body_text=text,
                            metadata={
                                "channel": notify_channel,
                                "channel_id": channel_id,
                                "sender_name": sender_name,
                                "msg_ts": msg_ts,
                                "files": matching_files,
                                "is_thread_collection": False,
                                "auto_processed": False,
                            },
                        )
                    )

            logger.debug("slack: fetched %d file message(s)", len(messages))
            return messages

        except Exception as exc:
            logger.warning("slack file fetch error: %s", exc)
            return []

    def _check_thread_ready(
        self, client, channel_id: str, parent_ts: str
    ) -> tuple[bool, bool, list[dict]]:
        """Check if a thread is ready to process as a multi-page document.

        Returns (is_ready, is_auto_processed, all_files_in_order).
        - is_ready: True if thread should be processed now
        - is_auto_processed: True if processing due to 12hr timeout (not keyword)
        - all_files_in_order: list of file dicts from parent + replies in page order
        """
        try:
            result = client.conversations_replies(channel=channel_id, ts=parent_ts)
        except Exception as exc:
            logger.warning("Failed to fetch thread replies for %s: %s", parent_ts, exc)
            return False, False, []

        thread_messages = result.get("messages", [])
        if not thread_messages:
            return False, False, []

        # Scan replies for trigger keyword; record the ts where it appears
        keyword_found = False
        keyword_msg_ts: str | None = None
        for reply in thread_messages[1:]:  # skip parent (index 0)
            text = reply.get("text", "").strip().lower()
            if text in self._TRIGGER_WORDS:
                keyword_found = True
                keyword_msg_ts = reply["ts"]
                break

        # Check 12-hour auto-process timeout (measured from parent message)
        parent_age_hours = (datetime.now(timezone.utc).timestamp() - float(parent_ts)) / 3600
        auto_process = not keyword_found and parent_age_hours >= self._THREAD_AUTO_PROCESS_HOURS

        if not keyword_found and not auto_process:
            return False, False, []

        # Collect files from parent + replies, stopping at keyword if present
        all_files: list[dict] = []
        for msg in thread_messages:
            if keyword_found and msg["ts"] == keyword_msg_ts:
                break  # don't include files from the "done" message itself
            for f in msg.get("files", []):
                if f.get("mimetype", "") in self._IMAGE_PDF_MIMES:
                    all_files.append({
                        "id": f["id"],
                        "name": f.get("name", "unknown"),
                        "mimetype": f.get("mimetype", ""),
                        "url_private_download": f.get("url_private_download", ""),
                        "size": f.get("size", 0),
                    })

        if not all_files:
            logger.debug("Thread %s triggered but has no image/PDF files", parent_ts)
            return False, False, []

        return True, auto_process, all_files

    @staticmethod
    def download_file(url: str) -> bytes:
        """Download a Slack-hosted file using the bot token for auth."""
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {config.SLACK_BOT_TOKEN}"},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.content

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
