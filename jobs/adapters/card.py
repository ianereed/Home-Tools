"""
Card adapter — emits a structured "card" record into the Mini Ops Decisions
tab feed. v1 is a JSONL append; the console reads the file directly.

Cards are how a Job surfaces a decision the user needs to make
(approve / reject / pick-one) without going through Slack. The Decisions
tab in :8503 polls this file.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CARDS_PATH = Path.home() / "Home-Tools" / "run" / "cards.jsonl"


def post_card(output_config: dict, payload: dict) -> dict:
    """Append a card to the Decisions feed.

    output_config:
        target: "card"
    payload:
        title (str)        — short headline
        body (str)         — markdown-friendly body
        actions (list)     — list of {label, action_id} dicts
        kind (str)         — "decision" | "info" | "warning"
        ttl_hours (int)    — auto-expire after N hours (default 24)
    """
    CARDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "title": payload.get("title", ""),
        "body": payload.get("body", ""),
        "actions": payload.get("actions", []),
        "kind": payload.get("kind", "info"),
        "ttl_hours": payload.get("ttl_hours", 24),
        "id": payload.get("id") or f"card_{int(datetime.now(timezone.utc).timestamp())}",
    }
    with CARDS_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return {"id": record["id"]}
