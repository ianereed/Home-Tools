"""Tier 3 — connector contract conformance.

Every connector must:
  - return a (list[RawMessage], ConnectorStatus) tuple
  - never raise (mock and live paths)
  - return ok() with mock=True
  - declare a non-empty source_name

These are smoke-level checks; per-connector behavior tests live in
their own files (test_extractor, test_dedup, etc.).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from connectors.base import ConnectorStatus, ConnectorStatusCode
from connectors.gmail import GmailConnector
from connectors.google_calendar import GoogleCalendarConnector
from connectors.slack import SlackConnector
from connectors.imessage import IMessageConnector
from connectors.whatsapp import WhatsAppConnector
from connectors.discord_conn import DiscordConnector
from connectors.notifications import NotificationCenterConnector


_CONNECTOR_CLASSES = [
    GmailConnector,
    GoogleCalendarConnector,
    SlackConnector,
    IMessageConnector,
    WhatsAppConnector,
    DiscordConnector,
    NotificationCenterConnector,
]


def _yesterday() -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(days=1)


@pytest.mark.parametrize(
    "cls", _CONNECTOR_CLASSES, ids=[c.__name__ for c in _CONNECTOR_CLASSES],
)
def test_connector_returns_tuple_with_mock(cls):
    connector = cls()
    result = connector.fetch(since=_yesterday(), mock=True)
    assert isinstance(result, tuple), f"{cls.__name__}.fetch did not return a tuple"
    assert len(result) == 2, f"{cls.__name__}.fetch did not return (messages, status)"
    messages, status = result
    assert isinstance(messages, list)
    assert isinstance(status, ConnectorStatus)
    assert status.code == ConnectorStatusCode.OK


@pytest.mark.parametrize(
    "cls", _CONNECTOR_CLASSES, ids=[c.__name__ for c in _CONNECTOR_CLASSES],
)
def test_connector_has_source_name(cls):
    connector = cls()
    assert connector.source_name, f"{cls.__name__} has empty source_name"
    assert isinstance(connector.source_name, str)


def _scrub_credentials(monkeypatch):
    """Force each connector down its no-creds / no-DB / unsupported_os branch."""
    import config
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "SLACK_MONITOR_CHANNELS", [], raising=False)
    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "DISCORD_MONITOR_CHANNELS", [], raising=False)
    monkeypatch.setattr(config, "GMAIL_TOKEN_JSON", "/nonexistent/gmail_token.json", raising=False)
    monkeypatch.setattr(config, "GCAL_TOKEN_JSON", "/nonexistent/gcal_token.json", raising=False)
    monkeypatch.setattr(config, "GMAIL_CREDENTIALS_JSON", "/nonexistent/oauth.json", raising=False)
    monkeypatch.setattr(config, "IMESSAGE_DB_PATH", "/nonexistent/chat.db", raising=False)
    # If a real IMESSAGE_EXPORT_FILE is set in the test host's .env, the
    # connector would happily read it and return OK — scrub it here so the
    # contract test forces the no-data branch.
    monkeypatch.setattr(config, "IMESSAGE_EXPORT_FILE", "", raising=False)
    monkeypatch.setattr(config, "WHATSAPP_DB_PATH", "/nonexistent/ChatStorage.sqlite", raising=False)
    # NotificationCenter: force the unsupported_os branch deterministically.
    from connectors import notifications as nc
    monkeypatch.setattr(nc, "_NC_DB_GLOB", "/nonexistent/NotificationCenter/*.db", raising=False)
    # google_auth: bypass keyring fallback by pointing the service name at a
    # junk key that won't have any tokens stashed locally. Otherwise the
    # laptop's live keyring tokens make Gmail/GCal succeed.
    from connectors import google_auth
    monkeypatch.setattr(
        google_auth, "_KEYRING_SERVICE",
        "home-tools-event-aggregator-test-nonexistent",
        raising=False,
    )


@pytest.mark.parametrize(
    "cls", _CONNECTOR_CLASSES, ids=[c.__name__ for c in _CONNECTOR_CLASSES],
)
def test_connector_does_not_raise_on_live_fetch(cls, monkeypatch):
    """Live (non-mock) fetch must never raise. With creds scrubbed, every
    connector hits a known no-creds / no-DB branch and returns a non-OK
    ConnectorStatus — verifying the no-raise contract end-to-end.
    """
    _scrub_credentials(monkeypatch)

    connector = cls()
    messages, status = connector.fetch(since=_yesterday(), mock=False)
    assert isinstance(messages, list)
    assert isinstance(status, ConnectorStatus)
    assert status.code in ConnectorStatusCode  # Enum membership
    # With credentials scrubbed, every connector should return NON-OK.
    assert status.code != ConnectorStatusCode.OK, (
        f"{cls.__name__} returned OK with all credentials scrubbed — likely a "
        f"missing branch in fetch()"
    )


# ── IMessageConnector JSONL export-file branch ──────────────────────────────

import json as _json
import os as _os
from datetime import timezone as _tz


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(_json.dumps(row) + "\n")


def _make_row(rowid, ts, body="hello", handle_id=42, is_from_me=False):
    return {
        "id": f"imessage_{rowid}",
        "source": "imessage",
        "timestamp": ts.astimezone(_tz.utc).isoformat(),
        "body_text": body,
        "metadata": {"handle_id": handle_id, "is_from_me": is_from_me},
    }


def test_imessage_jsonl_branch_returns_messages(tmp_path, monkeypatch):
    import config
    now = datetime.now(tz=_tz.utc)
    rows = [
        _make_row(1, now - timedelta(minutes=10)),
        _make_row(2, now - timedelta(minutes=5)),
        _make_row(3, now - timedelta(minutes=1)),
    ]
    jsonl = tmp_path / "imessage.jsonl"
    _write_jsonl(jsonl, rows)
    monkeypatch.setattr(config, "IMESSAGE_EXPORT_FILE", str(jsonl), raising=False)
    monkeypatch.setattr(config, "IMESSAGE_EXPORT_MAX_AGE_MIN", 120, raising=False)

    messages, status = IMessageConnector().fetch(since=now - timedelta(days=1))
    assert status.code == ConnectorStatusCode.OK
    assert len(messages) == 3
    assert messages[0].id == "imessage_1"
    assert messages[0].source == "imessage"
    assert messages[0].metadata == {"handle_id": 42, "is_from_me": False}


def test_imessage_jsonl_filters_by_since(tmp_path, monkeypatch):
    import config
    now = datetime.now(tz=_tz.utc)
    rows = [
        _make_row(1, now - timedelta(hours=4)),  # before since
        _make_row(2, now - timedelta(minutes=30)),  # after since
        _make_row(3, now - timedelta(minutes=5)),   # after since
    ]
    jsonl = tmp_path / "imessage.jsonl"
    _write_jsonl(jsonl, rows)
    monkeypatch.setattr(config, "IMESSAGE_EXPORT_FILE", str(jsonl), raising=False)
    monkeypatch.setattr(config, "IMESSAGE_EXPORT_MAX_AGE_MIN", 120, raising=False)

    messages, status = IMessageConnector().fetch(since=now - timedelta(hours=1))
    assert status.code == ConnectorStatusCode.OK
    assert {m.id for m in messages} == {"imessage_2", "imessage_3"}


def test_imessage_jsonl_missing_file_returns_permission_denied(monkeypatch):
    import config
    monkeypatch.setattr(
        config, "IMESSAGE_EXPORT_FILE", "/nonexistent/imessage.jsonl", raising=False,
    )
    messages, status = IMessageConnector().fetch(since=_yesterday())
    assert messages == []
    assert status.code == ConnectorStatusCode.PERMISSION_DENIED
    assert "missing" in status.message
    # Privacy invariant: status message must not contain bodies, contacts, or paths.
    assert "/nonexistent" not in status.message


def test_imessage_jsonl_stale_returns_messages_with_stale_status(tmp_path, monkeypatch):
    import config
    now = datetime.now(tz=_tz.utc)
    rows = [_make_row(1, now - timedelta(minutes=30))]
    jsonl = tmp_path / "imessage.jsonl"
    _write_jsonl(jsonl, rows)
    # Force mtime to 4 hours ago — well past the 120-min threshold.
    four_hours_ago = (now - timedelta(hours=4)).timestamp()
    _os.utime(jsonl, (four_hours_ago, four_hours_ago))
    monkeypatch.setattr(config, "IMESSAGE_EXPORT_FILE", str(jsonl), raising=False)
    monkeypatch.setattr(config, "IMESSAGE_EXPORT_MAX_AGE_MIN", 120, raising=False)

    messages, status = IMessageConnector().fetch(since=now - timedelta(days=1))
    # Stale status, but messages still returned so the worker can pick up
    # rows it hasn't seen yet.
    assert status.code == ConnectorStatusCode.PERMISSION_DENIED
    assert "stale" in status.message
    assert len(messages) == 1


def test_imessage_jsonl_malformed_returns_schema_error(tmp_path, monkeypatch):
    import config
    jsonl = tmp_path / "imessage.jsonl"
    jsonl.write_text("{not valid json\n", encoding="utf-8")
    monkeypatch.setattr(config, "IMESSAGE_EXPORT_FILE", str(jsonl), raising=False)
    monkeypatch.setattr(config, "IMESSAGE_EXPORT_MAX_AGE_MIN", 120, raising=False)

    messages, status = IMessageConnector().fetch(since=_yesterday())
    assert messages == []
    assert status.code == ConnectorStatusCode.SCHEMA_ERROR
    assert "parse" in status.message

