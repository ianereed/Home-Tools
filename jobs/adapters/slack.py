"""
Slack adapter — posts text or blocks to a channel.

Reuses the dispatcher's existing Slack client when available; falls back to
a thin slack_sdk WebClient otherwise. The adapter's contract is intentionally
narrow (channel + text/blocks); richer interactive flows stay in slack_notifier.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def send(output_config: dict, payload: dict) -> dict:
    """POST a message to Slack.

    output_config:
        target: "slack"
        channel: "#ian-event-aggregator"  (required)
        thread_ts: "..."                  (optional — reply in thread)
    payload:
        text: "..."     (required when no blocks)
        blocks: [...]   (optional Slack Block Kit)
    """
    channel = output_config.get("channel")
    if not channel:
        raise ValueError("slack adapter: output_config missing 'channel'")

    text = payload.get("text", "")
    blocks = payload.get("blocks")
    thread_ts = output_config.get("thread_ts")

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "SLACK_BOT_TOKEN not set in environment. "
            "On the mini, the consumer LaunchAgent unlocks the login keychain "
            "and exports SLACK_BOT_TOKEN before huey starts."
        )

    from slack_sdk import WebClient  # imported lazily so tests don't pull it in
    client = WebClient(token=token)
    kwargs: dict[str, Any] = {"channel": channel, "text": text or " "}
    if blocks:
        kwargs["blocks"] = blocks
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    result = client.chat_postMessage(**kwargs)
    if not result.get("ok"):
        raise RuntimeError(f"slack post failed: {result.get('error')}")
    return {"ts": result.get("ts"), "channel": result.get("channel")}
