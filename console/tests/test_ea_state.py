"""Unit tests for console/tabs/_ea_state.py — the read-only view into
event-aggregator state. Uses synthetic state only (no real message data)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from console.tabs import _ea_state


NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    """Point _ea_state at a tmp state.json / event_log.jsonl and return writers."""
    sp = tmp_path / "state.json"
    lp = tmp_path / "event_log.jsonl"
    monkeypatch.setattr(_ea_state, "STATE_PATH", sp)
    monkeypatch.setattr(_ea_state, "EVENT_LOG_PATH", lp)

    def write_state(data: dict) -> None:
        sp.write_text(json.dumps(data))

    def write_log(records: list[dict]) -> None:
        lp.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    return write_state, write_log, sp, lp


# ── _read_json_tolerant ───────────────────────────────────────────────────────

def test_read_missing_file_returns_empty(state_file):
    assert _ea_state.load_connector_health() == {}
    assert _ea_state.load_pending_items() == []
    assert _ea_state.load_written_events() == []


def test_read_empty_file_returns_empty(state_file):
    _, _, sp, _ = state_file
    sp.write_text("   ")
    assert _ea_state._read_json_tolerant(sp) == {}


def test_read_truncated_json_returns_empty(state_file):
    _, _, sp, _ = state_file
    sp.write_text('{"pending_proposals": [')  # truncated mid-write
    assert _ea_state._read_json_tolerant(sp) == {}


def test_legacy_schema_without_connector_health(state_file):
    write_state, _, _, _ = state_file
    write_state({"pending_proposals": []})  # old state, no connector_health key
    assert _ea_state.load_connector_health() == {}


def test_torn_read_retries_then_succeeds(state_file, monkeypatch):
    """Simulate the os.replace window: first read raises FileNotFoundError, retry wins."""
    write_state, _, sp, _ = state_file
    write_state({"connector_health": {"gmail": {"consecutive_errors": 0}}})
    real_read = sp.read_text
    calls = {"n": 0}

    def flaky_read(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError("inode swapped")
        return real_read(*a, **k)

    monkeypatch.setattr(type(sp), "read_text", lambda self, *a, **k: flaky_read())
    out = _ea_state._read_json_tolerant(sp)
    assert calls["n"] == 2
    assert out["connector_health"]["gmail"]["consecutive_errors"] == 0


# ── load_pending_items ─────────────────────────────────────────────────────────

def test_pending_items_only_pending_status(state_file):
    write_state, _, _, _ = state_file
    write_state({"pending_proposals": [
        {"created_at": _iso(2), "items": [
            {"num": 1, "status": "pending", "title": "A"},
            {"num": 2, "status": "approved", "title": "B"},
        ]},
        {"created_at": _iso(1), "items": [
            {"num": 3, "status": "pending", "title": "C"},
            {"num": 4, "status": "rejected", "title": "D"},
        ]},
    ]})
    items = _ea_state.load_pending_items()
    assert [it["title"] for it in items] == ["C", "A"]  # newest batch first
    assert all(it["status"] == "pending" for it in items)


# ── load_written_events ────────────────────────────────────────────────────────

def test_written_events_sorted_with_gcal_id(state_file):
    write_state, _, _, _ = state_file
    write_state({"written_events": {
        "g1": {"title": "Old", "created_at": _iso(5)},
        "g2": {"title": "New", "created_at": _iso(1)},
    }})
    out = _ea_state.load_written_events()
    assert [e["title"] for e in out] == ["New", "Old"]
    assert out[0]["gcal_id"] == "g2"


# ── load_recent_log ────────────────────────────────────────────────────────────

def test_recent_log_tail_newest_first_drops_bad_lines(state_file):
    _, write_log, _, lp = state_file
    lp.write_text(
        json.dumps({"action": "created", "title": "1"}) + "\n"
        + "this is not json\n"
        + json.dumps({"action": "cancelled", "title": "2"}) + "\n"
        + '{"partial":'  # crash mid-append, no newline
    )
    out = _ea_state.load_recent_log()
    assert [r["title"] for r in out] == ["2", "1"]


# ── health_badge ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("h,expected_icon", [
    ({"consecutive_errors": 0, "last_ok_at": _iso(0.1), "last_status_code": "ok"}, "🟢"),
    ({"consecutive_errors": 2, "last_ok_at": _iso(0.1), "last_status_code": "network_error"}, "🟡"),
    ({"consecutive_errors": 5, "last_ok_at": _iso(0.1), "last_status_code": "unknown_error"}, "🔴"),
    ({"consecutive_errors": 0, "last_ok_at": _iso(8), "last_status_code": "ok"}, "🔴"),  # stale
    ({"consecutive_errors": 0, "last_ok_at": None, "last_status_code": "no_credentials"}, "🟡"),  # never
])
def test_health_badge_icon(h, expected_icon):
    icon, caption = _ea_state.health_badge(h, now=NOW)
    assert icon == expected_icon
    assert "err" in caption


def test_health_badge_negative_age_clamped(state_file):
    # last_ok_at in the future (clock skew) → age clamps to 0, treated as fresh
    icon, caption = _ea_state.health_badge(
        {"consecutive_errors": 0, "last_ok_at": _iso(-2), "last_status_code": "ok"}, now=NOW
    )
    assert icon == "🟢"
    assert "0m ago" in caption


# ── is_unhealthy ───────────────────────────────────────────────────────────────

def test_is_unhealthy_reasons():
    assert _ea_state.is_unhealthy("gmail", {"consecutive_errors": 3}, now=NOW)
    assert _ea_state.is_unhealthy(
        "gmail", {"consecutive_errors": 0, "last_ok_at": _iso(10)}, now=NOW
    )
    assert _ea_state.is_unhealthy(
        "gmail", {"consecutive_errors": 0, "last_ok_at": _iso(0.1)}, now=NOW
    ) is None


def test_is_unhealthy_ignores_unconfigured_sources():
    # whatsapp / discord are intentionally unconfigured — never alarm
    assert _ea_state.is_unhealthy("whatsapp", {"consecutive_errors": 9999}, now=NOW) is None
    assert _ea_state.is_unhealthy("discord", {"consecutive_errors": 9999}, now=NOW) is None
