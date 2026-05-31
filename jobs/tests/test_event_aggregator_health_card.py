"""event_aggregator_health_card — unhealthy detection + deterministic-id dedup.

Synthetic connector_health only; cards.jsonl/resolved redirected to tmp via $HOME
(the jobs conftest already points HOME at a tmpdir)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from jobs.kinds import event_aggregator_health_card as mod
from jobs.lib import get_requires


NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _stub_requires(monkeypatch):
    import jobs.lib
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])


@pytest.fixture
def fresh_feed():
    """Start each test with empty cards.jsonl / resolved (under the conftest tmp HOME)."""
    for p in (mod.CARDS_PATH, mod.RESOLVED_PATH):
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            p.unlink()
    yield


def _set_health(monkeypatch, health: dict):
    monkeypatch.setattr(mod, "_load_connector_health", lambda: health)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _cards() -> list[dict]:
    if not mod.CARDS_PATH.exists():
        return []
    return [json.loads(l) for l in mod.CARDS_PATH.read_text().splitlines() if l.strip()]


# ── structural ───────────────────────────────────────────────────────────────

def test_requires_event_aggregator():
    req = get_requires(mod.event_aggregator_health_card)
    assert req is not None and "fs:event-aggregator" in req.items


# ── detection ────────────────────────────────────────────────────────────────

def test_healthy_sources_post_no_card(monkeypatch, fresh_feed):
    _set_health(monkeypatch, {
        "gmail": {"consecutive_errors": 0, "last_ok_at": _iso(0.1), "last_status_code": "ok"},
        "gcal": {"consecutive_errors": 0, "last_ok_at": _iso(0.05), "last_status_code": "ok"},
    })
    out = mod.event_aggregator_health_card.func()
    assert out["posted"] == []
    assert _cards() == []


def test_error_threshold_posts_one_card(monkeypatch, fresh_feed):
    _set_health(monkeypatch, {
        "gmail": {"consecutive_errors": 9, "last_ok_at": _iso(50), "last_status_code": "unknown_error"},
    })
    out = mod.event_aggregator_health_card.func()
    assert len(out["posted"]) == 1
    cards = _cards()
    assert len(cards) == 1
    assert cards[0]["id"] == f"ea-health-gmail-{_iso(50)}"
    assert cards[0]["kind"] == "warning"


def test_stale_last_ok_posts_card(monkeypatch, fresh_feed):
    _set_health(monkeypatch, {
        "gcal": {"consecutive_errors": 0, "last_ok_at": _iso(10), "last_status_code": "ok"},
    })
    out = mod.event_aggregator_health_card.func()
    assert len(out["posted"]) == 1


def test_ignored_sources_never_card(monkeypatch, fresh_feed):
    _set_health(monkeypatch, {
        "whatsapp": {"consecutive_errors": 9999, "last_ok_at": None, "last_status_code": "permission_denied"},
        "discord": {"consecutive_errors": 9999, "last_ok_at": None, "last_status_code": "no_credentials"},
    })
    out = mod.event_aggregator_health_card.func()
    assert out["posted"] == []


# ── dedup (the core requirement) ──────────────────────────────────────────────

def test_run_twice_posts_only_one_card(monkeypatch, fresh_feed):
    _set_health(monkeypatch, {
        "gmail": {"consecutive_errors": 9, "last_ok_at": _iso(50), "last_status_code": "unknown_error"},
    })
    mod.event_aggregator_health_card.func()
    second = mod.event_aggregator_health_card.func()
    assert second["posted"] == []        # suppressed by existing card id
    assert len(_cards()) == 1


def test_dismissed_card_not_reposted(monkeypatch, fresh_feed):
    health = {"gmail": {"consecutive_errors": 9, "last_ok_at": _iso(50), "last_status_code": "unknown_error"}}
    _set_health(monkeypatch, health)
    mod.event_aggregator_health_card.func()
    card_id = _cards()[0]["id"]
    # simulate the Decisions tab dismissing it
    mod.RESOLVED_PATH.write_text(json.dumps({"id": card_id, "action": "dismissed"}) + "\n")
    again = mod.event_aggregator_health_card.func()
    assert again["posted"] == []

def test_recover_then_rebreak_posts_new_card(monkeypatch, fresh_feed):
    # break #1 at last_ok = 50h ago
    _set_health(monkeypatch, {"gmail": {"consecutive_errors": 9, "last_ok_at": _iso(50), "last_status_code": "x"}})
    mod.event_aggregator_health_card.func()
    # recovered (last_ok advanced) then broke again — different last_ok_at → new id
    _set_health(monkeypatch, {"gmail": {"consecutive_errors": 5, "last_ok_at": _iso(7), "last_status_code": "y"}})
    out = mod.event_aggregator_health_card.func()
    assert len(out["posted"]) == 1
    assert len(_cards()) == 2
