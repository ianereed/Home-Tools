"""
Adapter dispatch routing + error contracts.

Adapters that touch external services (Slack, GCal, Todoist) are tested for
their *contract* — what they reject, what they require — not for end-to-end
calls. The card and nas adapters are local file writes and are tested
end-to-end against tmp.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jobs import adapters


def test_dispatch_unknown_target():
    with pytest.raises(ValueError, match="unknown adapter target"):
        adapters.dispatch({"target": "ouija_board"}, {})


def test_dispatch_missing_target():
    with pytest.raises(ValueError, match="missing 'target'"):
        adapters.dispatch({"foo": "bar"}, {})


def test_list_targets_includes_all_six():
    assert set(adapters.list_targets()) == {"slack", "gcal", "todoist", "card", "nas", "sheet"}


def test_card_adapter_writes_jsonl(tmp_path, monkeypatch):
    # Redirect CARDS_PATH (the module-level const) to tmp.
    from jobs.adapters import card
    monkeypatch.setattr(card, "CARDS_PATH", tmp_path / "cards.jsonl")

    out = card.post_card(
        {"target": "card"},
        {"title": "Approve?", "body": "do the thing", "kind": "decision"},
    )
    assert "id" in out
    line = (tmp_path / "cards.jsonl").read_text().strip()
    rec = json.loads(line)
    assert rec["title"] == "Approve?"
    assert rec["kind"] == "decision"


def test_sheet_adapter_is_strict_stub():
    with pytest.raises(NotImplementedError, match="Phase 13"):
        adapters.dispatch({"target": "sheet"}, {})


def test_nas_adapter_rejects_traversal(tmp_path, monkeypatch):
    from jobs.adapters import nas
    monkeypatch.setattr(nas, "NAS_ROOT", tmp_path)
    with pytest.raises(ValueError, match="non-traversing"):
        nas.write_file({"target": "nas", "relpath": "../escape.txt"}, {"content": "hi"})


def test_nas_adapter_rejects_absolute(tmp_path, monkeypatch):
    from jobs.adapters import nas
    monkeypatch.setattr(nas, "NAS_ROOT", tmp_path)
    with pytest.raises(ValueError, match="non-traversing"):
        nas.write_file({"target": "nas", "relpath": "/etc/passwd"}, {"content": "hi"})


def test_nas_adapter_writes_under_root(tmp_path, monkeypatch):
    from jobs.adapters import nas
    monkeypatch.setattr(nas, "NAS_ROOT", tmp_path)
    res = nas.write_file({"target": "nas", "relpath": "Reports/2026/x.md"}, {"content": "hello"})
    assert (tmp_path / "Reports" / "2026" / "x.md").read_text() == "hello"
    assert res["bytes"] == 5


def test_slack_adapter_requires_channel():
    from jobs.adapters import slack
    with pytest.raises(ValueError, match="channel"):
        slack.send({"target": "slack"}, {"text": "hi"})


def test_slack_adapter_requires_token(monkeypatch):
    from jobs.adapters import slack
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="SLACK_BOT_TOKEN"):
        slack.send({"target": "slack", "channel": "#x"}, {"text": "hi"})
