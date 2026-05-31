"""Periodic source-health watchdog → Decisions-tab card.

Slack is retired as the alert channel, so a silently-dead source (as Gmail was for
~25 days) must surface somewhere loud. Every 15 min this reads event-aggregator's
connector_health and, when a source crosses unhealthy AND hasn't already been
carded for this incident, posts ONE decision card to the cards.jsonl feed the
Decisions tab renders.

Reads state.json directly (read-only; EA's save() is an atomic os.replace, so the
read is never torn) — no EA venv / huey import needed.

Dedup: the card id is `ea-health-<src>-<last_ok_at>`. While a source stays down,
last_ok_at is frozen → the id is stable → scanning cards.jsonl + cards.resolved.jsonl
suppresses re-posting every tick AND after the user dismisses it. When the source
recovers then breaks again, last_ok_at has advanced → a new id → exactly one fresh
card per incident.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from huey import crontab

from jobs import huey, requires
from jobs.adapters.card import CARDS_PATH, post_card

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).resolve().parents[2] / "event-aggregator" / "state.json"
RESOLVED_PATH = CARDS_PATH.parent / "cards.resolved.jsonl"

# Keep in sync with console/tabs/_ea_state.py (ERR_THRESHOLD / STALE_HOURS).
ERR_THRESHOLD = 3
STALE_HOURS = 6.0
TTL_HOURS = 12
# Intentionally unconfigured on the mini — never alarm.
IGNORED_SOURCES = frozenset({"whatsapp", "discord"})


def _load_connector_health() -> dict:
    try:
        txt = STATE_PATH.read_text()
        return json.loads(txt).get("connector_health", {}) if txt.strip() else {}
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return {}


def _unhealthy_reason(src: str, h: dict, now: datetime) -> str | None:
    if src in IGNORED_SOURCES:
        return None
    errs = int(h.get("consecutive_errors", 0) or 0)
    if errs >= ERR_THRESHOLD:
        return f"{errs} consecutive errors ({h.get('last_status_code', '?')})"
    last_ok = h.get("last_ok_at")
    if last_ok:
        try:
            age_h = (now - datetime.fromisoformat(last_ok)).total_seconds() / 3600
        except ValueError:
            age_h = None
        if age_h is not None and age_h > STALE_HOURS:
            return f"no successful fetch in {age_h:.0f}h"
    return None


def _active_card_ids() -> set[str]:
    ids: set[str] = set()
    for path in (CARDS_PATH, RESOLVED_PATH):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                ids.add(json.loads(line).get("id"))
            except json.JSONDecodeError:
                continue
    return ids


@huey.periodic_task(crontab(minute="*/15"))
@requires(["fs:event-aggregator"])
def event_aggregator_health_card() -> dict:
    health = _load_connector_health()
    now = datetime.now(timezone.utc)
    seen = _active_card_ids()
    posted: list[str] = []
    for src, h in sorted(health.items()):
        reason = _unhealthy_reason(src, h, now)
        if not reason:
            continue
        card_id = f"ea-health-{src}-{h.get('last_ok_at') or 'never'}"
        if card_id in seen:
            continue
        post_card(
            {"target": "card"},
            {
                "id": card_id,
                "kind": "warning",
                "title": f"event-aggregator: {src} source unhealthy",
                "body": (
                    f"`{src}` — {reason}.\n\n"
                    f"Last status: `{h.get('last_status_code', '?')}` "
                    f"{str(h.get('last_status_message', ''))[:120]}\n\n"
                    f"Check the source-health strip on this tab; a connector likely needs re-auth."
                ),
                "actions": [{"label": "Acknowledge", "action_id": "ack"}],
                "ttl_hours": TTL_HOURS,
            },
        )
        posted.append(card_id)
    if posted:
        logger.info("event_aggregator_health_card: posted %d card(s): %s", len(posted), posted)
    return {"posted": posted, "checked": sorted(health.keys())}
