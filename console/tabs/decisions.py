"""Decisions tab — the single decision surface for event-aggregator (Slack retired).

Four stacked sections:
  1. Source-health strip   — per-connector green/yellow/red from connector_health
  2. Pending decisions     — proposals awaiting Approve/Reject (→ decide job kind)
  3. Auto-added events      — events already written to GCal, with Undo/Delete
  4. Cards                  — the generic cards.jsonl feed (approve/reject/dismiss)

Reads event-aggregator state read-only via _ea_state (atomic-rename safe, no flock).
Mutations are enqueued over HTTP to the jobs service (event_aggregator_decide kind);
the console never imports huey in-process.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

from console import jobs_client
from console.tabs import _ea_state

CARDS_PATH = Path.home() / "Home-Tools" / "run" / "cards.jsonl"
RESOLVED_PATH = Path.home() / "Home-Tools" / "run" / "cards.resolved.jsonl"

_DECIDE_KIND = "event_aggregator_decide"
_POLL_SECONDS = 0.5
_POLL_MAX = 24  # ~12s — decide/undo shells out to the EA CLI, typically 1–3s


# ── shared: enqueue a mutation and wait briefly for the result ────────────────


def _do_action(label: str, **params) -> None:
    """Enqueue an event_aggregator_decide job, poll briefly, surface the outcome,
    then rerun so the lists reload from the freshly-mutated state.json."""
    try:
        task_id = jobs_client.enqueue(_DECIDE_KIND, params)
    except Exception as exc:
        st.error(f"{label}: could not enqueue — {exc}")
        return

    result = None
    with st.spinner(f"{label}…"):
        for _ in range(_POLL_MAX):
            result = jobs_client.result(task_id)
            if result is not None:
                break
            time.sleep(_POLL_SECONDS)

    if result is None:
        st.warning(f"{label}: still running — refresh in a moment.")
    else:
        rc = result.get("rc")
        if rc == 0:
            st.success(f"{label}: done")
        elif rc == 2:
            st.warning(f"{label}: partially applied — {result.get('summary', '')}")
        else:
            detail = result.get("error") or result.get("summary") or f"rc={rc}"
            st.error(f"{label}: failed — {detail}")
    st.rerun()


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return str(iso)
    return dt.strftime("%a %b %-d, %-I:%M%p").replace(":00", "")


# ── 1. health strip ───────────────────────────────────────────────────────────


def _render_health_strip() -> None:
    health = _ea_state.load_connector_health()
    if not health:
        return
    st.caption("Source health")
    cols = st.columns(len(health))
    for col, (src, h) in zip(cols, sorted(health.items())):
        icon, caption = _ea_state.health_badge(h)
        col.metric(label=f"{icon} {src}", value=str(h.get("last_status_code", "?")))
        col.caption(caption)
        if h.get("last_status_code") not in ("ok", None) and h.get("last_status_message"):
            col.caption(f"⚠ {str(h['last_status_message'])[:60]}")
    st.divider()


# ── 2. pending decisions ───────────────────────────────────────────────────────


def _pending_subtitle(item: dict) -> str:
    kind = item.get("kind", "event")
    if kind == "todo":
        bits = ["todo"]
        if item.get("due_date"):
            bits.append(f"due {item['due_date']}")
        if item.get("priority"):
            bits.append(str(item["priority"]))
        return " · ".join(bits)
    if kind == "fuzzy_event":
        return "event · no specific date"
    if kind == "merge":
        return f"merge → {item.get('matched_title', '?')}"
    # plain event
    start = _fmt_dt(item.get("start_dt"))
    return f"{start} · {item.get('source', '?')}"


def _render_pending() -> None:
    items = _ea_state.load_pending_items()
    st.subheader(f"Pending decisions ({len(items)})")
    if not items:
        st.info("No pending decisions.")
        return
    for item in items:
        num = item.get("num")
        tentative = "[?] " if item.get("confidence_band") == "medium" else ""
        with st.container(border=True):
            st.markdown(f"**{tentative}{item.get('title', '(untitled)')}**")
            st.caption(_pending_subtitle(item))
            conflicts = item.get("conflicts") or []
            if conflicts:
                st.caption("⚠ conflicts: " + ", ".join(str(c) for c in conflicts[:3]))
            c_ok, c_no, _ = st.columns([1, 1, 4])
            if c_ok.button("Approve", key=f"ea_approve_{num}", type="primary"):
                _do_action(f"Approve #{num}", approve=[num])
            if c_no.button("Reject", key=f"ea_reject_{num}"):
                _do_action(f"Reject #{num}", reject=[num])


# ── 3. auto-added events (undo/delete) ─────────────────────────────────────────


def _render_auto_added() -> None:
    events = _ea_state.load_written_events()
    st.subheader(f"On calendar ({len(events)})")
    st.caption("Auto-added or already-approved events. Undo deletes them from Google Calendar.")
    if not events:
        st.info("Nothing on the weekend calendar yet.")
        return
    for ev in events[:50]:  # cap the list; newest first
        gid = ev.get("gcal_id")
        tentative = "[?] " if ev.get("is_tentative") else ""
        with st.container(border=True):
            left, right = st.columns([5, 1])
            with left:
                st.markdown(f"**{tentative}{ev.get('title', '(untitled)')}**")
                st.caption(_fmt_dt(ev.get("start")))
            if right.button("Undo", key=f"ea_undo_{gid}", help="Delete from Google Calendar"):
                _do_action(f"Undo {ev.get('title', gid)}", undo_gcal_id=gid)
    if len(events) > 50:
        st.caption(f"… and {len(events) - 50} older (not shown)")


# ── 4. cards (generic cards.jsonl feed) ────────────────────────────────────────


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


def _render_cards() -> None:
    cards = _load_open_cards()
    st.subheader(f"Cards ({len(cards)})")
    if not cards:
        st.info("No open cards. (Cards expire after their TTL or resolution.)")
        return
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


# ── entry point ────────────────────────────────────────────────────────────────


def render() -> None:
    try:
        _render_health_strip()
        _render_pending()
        st.divider()
        _render_auto_added()
        st.divider()
        _render_cards()
    except Exception as exc:  # never crash the tab
        st.error("Decisions tab error — see traceback below")
        st.exception(exc)
