"""Decisions tab — reads cards.jsonl, lets the user approve/reject/dismiss."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

CARDS_PATH = Path.home() / "Home-Tools" / "run" / "cards.jsonl"
RESOLVED_PATH = Path.home() / "Home-Tools" / "run" / "cards.resolved.jsonl"


def _load_open_cards() -> list[dict]:
    if not CARDS_PATH.exists():
        return []
    cards: list[dict] = []
    resolved_ids = _resolved_ids()
    now = datetime.now(timezone.utc)
    for line in CARDS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("id") in resolved_ids:
            continue
        try:
            ts = datetime.fromisoformat(rec.get("ts", ""))
        except ValueError:
            ts = now
        ttl = rec.get("ttl_hours", 24)
        if now - ts > timedelta(hours=ttl):
            continue
        cards.append(rec)
    return cards


def _resolved_ids() -> set:
    if not RESOLVED_PATH.exists():
        return set()
    ids = set()
    for line in RESOLVED_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ids.add(rec.get("id"))
    return ids


def _resolve(card_id: str, action: str) -> None:
    RESOLVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "id": card_id,
        "action": action,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with RESOLVED_PATH.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def render() -> None:
    cards = _load_open_cards()
    if not cards:
        st.info("No open decisions. (Cards expire after their TTL or resolution.)")
        return
    st.caption(f"{len(cards)} open card(s)")
    for card in reversed(cards):  # newest first
        with st.container(border=True):
            kind_emoji = {"decision": ":raising_hand:", "info": ":information_source:", "warning": ":warning:"}.get(
                card.get("kind", "info"), ":card_index:"
            )
            st.markdown(f"### {kind_emoji} {card.get('title', '(untitled)')}")
            st.markdown(card.get("body", ""))
            col_actions = st.columns(max(1, len(card.get("actions", [])) + 1))
            actions = card.get("actions", [])
            if not actions:
                col_actions[0].caption(f"id: `{card.get('id', '?')}`")
            for i, action in enumerate(actions):
                if col_actions[i].button(action.get("label", "?"), key=f"{card['id']}-{i}"):
                    _resolve(card["id"], action.get("action_id", action.get("label", "")))
                    st.rerun()
            if col_actions[-1].button("Dismiss", key=f"{card['id']}-dismiss"):
                _resolve(card["id"], "dismissed")
                st.rerun()
