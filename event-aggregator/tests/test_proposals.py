"""
Tests for the event proposal and approval system.

Covers: proposal state management, reply parsing, expiry, fingerprint cleanup,
extractor calendar context injection, and run_summary with proposal counts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import state as state_module
from models import CandidateEvent


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _future_dt(hours: int = 24) -> datetime:
    return _utcnow() + timedelta(hours=hours)


def _make_candidate(title: str = "Team Standup", hours: int = 24, source: str = "gmail") -> CandidateEvent:
    return CandidateEvent(
        title=title,
        start_dt=_future_dt(hours),
        end_dt=_future_dt(hours + 1),
        location=None,
        confidence=0.85,
        source=source,
        source_id=f"{source}_001",
        source_url=None,
        confidence_band="high",
        suggested_attendees=[],
        category="work",
    )


def _make_proposal_item(num: int = 1, title: str = "Team Standup", hours_old: int = 0) -> dict:
    from dedup import fingerprint
    candidate = _make_candidate(title)
    ts = (_utcnow() - timedelta(hours=hours_old)).isoformat()
    return {
        "num": num,
        "status": "pending",
        "title": title,
        "start_dt": candidate.start_dt.isoformat(),
        "end_dt": candidate.end_dt.isoformat(),
        "location": None,
        "confidence": 0.85,
        "confidence_band": "high",
        "category": "work",
        "source": "gmail",
        "source_id": "gmail_001",
        "source_url": None,
        "fingerprint": fingerprint(candidate),
        "is_update": False,
        "is_cancellation": False,
        "original_title_hint": None,
        "gcal_event_id_to_update": None,
        "suggested_attendees": [],
        "conflicts": [],
    }


def _make_batch(num: int = 1, title: str = "Team Standup", hours_old: int = 0) -> dict:
    return {
        "batch_id": f"2026-04-20_{num:02d}:00",
        "slack_ts": f"17136000{num:02d}.000001",
        "created_at": (_utcnow() - timedelta(hours=hours_old)).isoformat(),
        "items": [_make_proposal_item(num, title, hours_old)],
    }


# ── State: proposal counter ──────────────────────────────────────────────────


class TestProposalCounter:
    def test_counter_starts_at_one(self):
        s = state_module.State({})
        assert s.next_proposal_num() == 1

    def test_counter_increments(self):
        s = state_module.State({})
        assert s.next_proposal_num() == 1
        assert s.next_proposal_num() == 2
        assert s.next_proposal_num() == 3

    def test_counter_resets_on_new_day(self):
        s = state_module.State({
            "proposal_counter": 5,
            "proposal_counter_date": "2026-04-19",  # yesterday
        })
        # Counter should reset because date changed
        assert s.next_proposal_num() == 1

    def test_counter_continues_same_day(self):
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        s = state_module.State({
            "proposal_counter": 5,
            "proposal_counter_date": today,
        })
        assert s.next_proposal_num() == 6


# ── State: add and retrieve proposals ────────────────────────────────────────


class TestProposalStorage:
    def test_add_and_get_pending(self):
        s = state_module.State({})
        batch = _make_batch(num=1)
        s.add_proposal_batch(batch)
        pending = s.get_pending_proposals()
        assert len(pending) == 1
        assert pending[0]["items"][0]["title"] == "Team Standup"

    def test_get_pending_excludes_fully_resolved_batches(self):
        s = state_module.State({})
        batch = _make_batch(num=1)
        batch["items"][0]["status"] = "approved"
        s.add_proposal_batch(batch)
        assert len(s.get_pending_proposals()) == 0

    def test_set_proposal_slack_ts(self):
        s = state_module.State({})
        batch = _make_batch(num=1)
        s.add_proposal_batch(batch)
        s.set_proposal_slack_ts(batch["batch_id"], "99999.000001")
        stored = s._data["pending_proposals"][0]
        assert stored["slack_ts"] == "99999.000001"


# ── State: approve and reject proposals ──────────────────────────────────────


class TestApproveReject:
    def test_approve_returns_item(self):
        s = state_module.State({})
        s.add_proposal_batch(_make_batch(num=1))
        item = s.approve_proposal(1)
        assert item is not None
        assert item["title"] == "Team Standup"
        assert item["status"] == "approved"

    def test_approve_nonexistent_returns_none(self):
        s = state_module.State({})
        assert s.approve_proposal(99) is None

    def test_reject_marks_status(self):
        s = state_module.State({})
        s.add_proposal_batch(_make_batch(num=1))
        item = s.reject_proposal(1)
        assert item is not None
        assert item["status"] == "rejected"
        # Verify stored in state
        stored = s._data["pending_proposals"][0]["items"][0]
        assert stored["status"] == "rejected"

    def test_cannot_approve_already_rejected(self):
        s = state_module.State({})
        s.add_proposal_batch(_make_batch(num=1))
        s.reject_proposal(1)
        # approve_proposal looks for "pending" status, so rejected item won't be found
        result = s.approve_proposal(1)
        assert result is None

    def test_multiple_items_in_batch(self):
        s = state_module.State({})
        batch = {
            "batch_id": "test",
            "slack_ts": "12345.0",
            "created_at": _utcnow().isoformat(),
            "items": [
                _make_proposal_item(1, "Event A"),
                _make_proposal_item(2, "Event B"),
                _make_proposal_item(3, "Event C"),
            ],
        }
        s.add_proposal_batch(batch)

        s.approve_proposal(1)
        s.reject_proposal(3)

        items = s._data["pending_proposals"][0]["items"]
        assert items[0]["status"] == "approved"
        assert items[1]["status"] == "pending"
        assert items[2]["status"] == "rejected"


# ── State: expiry ─────────────────────────────────────────────────────────────


class TestProposalExpiry:
    def test_expire_old_proposals(self):
        s = state_module.State({})
        old_batch = _make_batch(num=1, hours_old=50)
        s.add_proposal_batch(old_batch)

        expired = s.expire_old_proposals(hours=48)
        assert len(expired) == 1
        assert expired[0]["title"] == "Team Standup"
        assert expired[0]["status"] == "expired"

    def test_fresh_proposals_not_expired(self):
        s = state_module.State({})
        fresh_batch = _make_batch(num=1, hours_old=1)
        s.add_proposal_batch(fresh_batch)

        expired = s.expire_old_proposals(hours=48)
        assert len(expired) == 0

    def test_already_approved_not_re_expired(self):
        s = state_module.State({})
        batch = _make_batch(num=1, hours_old=50)
        batch["items"][0]["status"] = "approved"
        s.add_proposal_batch(batch)

        expired = s.expire_old_proposals(hours=48)
        assert len(expired) == 0


# ── State: fingerprint cleanup on reject/expire ───────────────────────────────


class TestFingerprintCleanup:
    def test_remove_fingerprint(self):
        s = state_module.State({})
        s.add_fingerprint("abc123")
        assert s.has_fingerprint("abc123")

        s.remove_proposal_fingerprint("abc123")
        assert not s.has_fingerprint("abc123")

    def test_remove_nonexistent_fingerprint_is_safe(self):
        s = state_module.State({})
        # Should not raise
        s.remove_proposal_fingerprint("does_not_exist")


# ── State: pruning ────────────────────────────────────────────────────────────


class TestProposalPruning:
    def test_prune_removes_old_resolved_batches(self):
        s = state_module.State({})
        # Old batch with all items resolved
        old_batch = _make_batch(num=1, hours_old=80)  # > 72h
        old_batch["items"][0]["status"] = "approved"
        s.add_proposal_batch(old_batch)

        # Fresh batch still pending
        fresh_batch = _make_batch(num=2, hours_old=1)
        s.add_proposal_batch(fresh_batch)

        s.prune()
        remaining = s._data.get("pending_proposals", [])
        assert len(remaining) == 1
        assert remaining[0]["items"][0]["num"] == 2

    def test_prune_keeps_old_batch_with_pending_items(self):
        s = state_module.State({})
        # Old batch but still pending — keep it
        old_batch = _make_batch(num=1, hours_old=80)
        s.add_proposal_batch(old_batch)

        s.prune()
        remaining = s._data.get("pending_proposals", [])
        assert len(remaining) == 1


# ── Reply parsing ─────────────────────────────────────────────────────────────


class TestParseNums:
    def test_comma_separated(self):
        from notifiers.slack_notifier import _parse_nums
        assert _parse_nums("1,3,5") == [1, 3, 5]

    def test_space_separated(self):
        from notifiers.slack_notifier import _parse_nums
        assert _parse_nums("1 3 5") == [1, 3, 5]

    def test_mixed_separators(self):
        from notifiers.slack_notifier import _parse_nums
        assert _parse_nums("1, 3, 5") == [1, 3, 5]

    def test_single_number(self):
        from notifiers.slack_notifier import _parse_nums
        assert _parse_nums("7") == [7]

    def test_empty_string(self):
        from notifiers.slack_notifier import _parse_nums
        assert _parse_nums("") == []


class TestCheckProposalReplies:
    def _mock_client(self, messages: list[dict]):
        client = MagicMock()
        client.conversations_replies.return_value = {"messages": messages}
        return client

    def test_approve_all(self):
        from notifiers import slack_notifier
        messages = [
            {"ts": "100.0", "text": "3 proposals posted"},  # the proposal itself
            {"ts": "101.0", "text": "approve"},
        ]
        with patch("notifiers.slack_notifier._client", return_value=self._mock_client(messages)):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    result = slack_notifier.check_proposal_replies("900.0", "100.0")
        assert result["approve_all"] is True

    def test_approve_specific(self):
        from notifiers import slack_notifier
        messages = [
            {"ts": "100.0", "text": "proposals"},
            {"ts": "101.0", "text": "approve 1,3"},
        ]
        with patch("notifiers.slack_notifier._client", return_value=self._mock_client(messages)):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    result = slack_notifier.check_proposal_replies("900.0", "100.0")
        assert result["approve_nums"] == [1, 3]
        assert result["approve_all"] is False

    def test_reject_specific(self):
        from notifiers import slack_notifier
        messages = [
            {"ts": "100.0", "text": "proposals"},
            {"ts": "101.0", "text": "reject 2"},
        ]
        with patch("notifiers.slack_notifier._client", return_value=self._mock_client(messages)):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    result = slack_notifier.check_proposal_replies("900.0", "100.0")
        assert result["reject_nums"] == [2]
        assert result["reject_all"] is False

    def test_reject_all(self):
        from notifiers import slack_notifier
        messages = [
            {"ts": "100.0", "text": "proposals"},
            {"ts": "101.0", "text": "Reject all"},
        ]
        with patch("notifiers.slack_notifier._client", return_value=self._mock_client(messages)):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    result = slack_notifier.check_proposal_replies("900.0", "100.0")
        assert result["reject_all"] is True

    def test_proposal_message_itself_ignored(self):
        from notifiers import slack_notifier
        messages = [
            {"ts": "100.0", "text": "approve"},  # this is the proposal ts itself, should be skipped
        ]
        with patch("notifiers.slack_notifier._client", return_value=self._mock_client(messages)):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    result = slack_notifier.check_proposal_replies("900.0", "100.0")
        # The "approve" text at ts=100.0 is the proposal message itself — skipped
        assert result["approve_all"] is False

    def test_irrelevant_replies_ignored(self):
        from notifiers import slack_notifier
        messages = [
            {"ts": "100.0", "text": "proposals"},
            {"ts": "101.0", "text": "ok sounds good"},
            {"ts": "102.0", "text": "when is the next run?"},
        ]
        with patch("notifiers.slack_notifier._client", return_value=self._mock_client(messages)):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    result = slack_notifier.check_proposal_replies("900.0", "100.0")
        assert not result["approve_all"]
        assert not result["approve_nums"]
        assert not result["reject_all"]
        assert not result["reject_nums"]


# ── Extractor: calendar context injection ─────────────────────────────────────


class TestCalendarContextInjection:
    def test_prompt_includes_context(self):
        from extractor import _build_prompt
        from models import RawMessage
        msg = RawMessage(
            id="test_001",
            source="gmail",
            timestamp=datetime.now(tz=timezone.utc),
            body_text="Can we move the meeting to Thursday?",
            metadata={"from": "alice@example.com"},
        )
        prompt = _build_prompt(msg, calendar_context="- Apr 22 10:00am-11:00am: Team Meeting")
        assert "Team Meeting" in prompt
        assert "Apr 22" in prompt

    def test_prompt_without_context(self):
        from extractor import _build_prompt
        from models import RawMessage
        msg = RawMessage(
            id="test_002",
            source="gmail",
            timestamp=datetime.now(tz=timezone.utc),
            body_text="Let's meet Friday at 3pm",
            metadata={},
        )
        prompt = _build_prompt(msg, calendar_context="")
        assert "Your calendar" not in prompt

    def test_context_truncated_at_limit(self):
        from extractor import _build_prompt, _CALENDAR_CONTEXT_MAX_CHARS
        from models import RawMessage
        msg = RawMessage(
            id="test_003",
            source="slack",
            timestamp=datetime.now(tz=timezone.utc),
            body_text="See you there",
            metadata={},
        )
        long_context = "- Apr 22 10:00am: Event\n" * 200  # way over limit
        prompt = _build_prompt(msg, calendar_context=long_context)
        # The context block in the prompt shouldn't exceed the limit
        assert len(long_context[:_CALENDAR_CONTEXT_MAX_CHARS]) <= _CALENDAR_CONTEXT_MAX_CHARS

    def test_extract_accepts_calendar_context_param(self):
        """extract() should accept calendar_context without error (mock mode)."""
        from extractor import extract
        from models import RawMessage
        msg = RawMessage(
            id="test_004",
            source="gmail",
            timestamp=datetime.now(tz=timezone.utc),
            body_text="Meeting at 3pm tomorrow",
            metadata={},
        )
        with patch("extractor._call_ollama", return_value={"events": [], "todos": []}):
            events, todos = extract(msg, calendar_context="- Apr 22 10am: Something")
        assert isinstance(events, list)
        assert isinstance(todos, list)


# ── post_run_summary: propose mode ────────────────────────────────────────────


class TestRunSummaryProposalMode:
    def test_summary_includes_proposed_count(self):
        from notifiers import slack_notifier
        client_mock = MagicMock()
        client_mock.chat_postMessage.return_value = {"ok": True}

        with patch("notifiers.slack_notifier._client", return_value=client_mock):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    slack_notifier.post_run_summary(
                        thread_ts="12345.0",
                        created=0, updated=0, cancelled=0,
                        skipped_low_confidence=0, skipped_recurring=0, skipped_duplicate=0,
                        proposed=3, pending_proposals=3,
                    )

        call_text = client_mock.chat_postMessage.call_args[1]["text"]
        assert "3 event" in call_text
        assert "proposed" in call_text
        assert "awaiting approval" in call_text

    def test_summary_not_posted_when_nothing_happened(self):
        from notifiers import slack_notifier
        client_mock = MagicMock()

        with patch("notifiers.slack_notifier._client", return_value=client_mock):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    slack_notifier.post_run_summary(
                        thread_ts="12345.0",
                        created=0, updated=0, cancelled=0,
                        skipped_low_confidence=0, skipped_recurring=0, skipped_duplicate=0,
                        proposed=0, pending_proposals=0,
                    )

        client_mock.chat_postMessage.assert_not_called()


# ── post_file_result: proposed vs created ─────────────────────────────────────


class TestPostFileResultProposals:
    def _make_analysis(self):
        from models import FileAnalysisResult
        return FileAnalysisResult(
            file_id="f001",
            primary_category="Medical",
            subcategory="Labs",
            confidence=0.9,
            title="Lab Results",
            date="2026-04-15",
            structured_text="[private]",
            summary="Lab results from 2026-04-15",
            calendar_items=[],
            source_slack_ts="100.0",
            original_filename="labs.pdf",
        )

    def test_shows_proposed_when_events_proposed(self):
        from notifiers import slack_notifier
        client_mock = MagicMock()
        client_mock.chat_postMessage.return_value = {"ok": True}

        with patch("notifiers.slack_notifier._client", return_value=client_mock):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    slack_notifier.post_file_result(
                        thread_ts="100.0",
                        analysis=self._make_analysis(),
                        nas_path="/NAS/Medical/Labs/labs.pdf",
                        events_created=0,
                        events_proposed=2,
                    )

        call_text = client_mock.chat_postMessage.call_args[1]["text"]
        assert "proposed for approval" in call_text
        assert "created" not in call_text

    def test_shows_created_when_no_proposals(self):
        from notifiers import slack_notifier
        client_mock = MagicMock()
        client_mock.chat_postMessage.return_value = {"ok": True}

        with patch("notifiers.slack_notifier._client", return_value=client_mock):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    slack_notifier.post_file_result(
                        thread_ts="100.0",
                        analysis=self._make_analysis(),
                        nas_path="/NAS/Medical/Labs/labs.pdf",
                        events_created=1,
                        events_proposed=0,
                    )

        call_text = client_mock.chat_postMessage.call_args[1]["text"]
        assert "created" in call_text
        assert "proposed for approval" not in call_text

    def test_neither_shown_when_no_events(self):
        from notifiers import slack_notifier
        client_mock = MagicMock()
        client_mock.chat_postMessage.return_value = {"ok": True}

        with patch("notifiers.slack_notifier._client", return_value=client_mock):
            with patch("config.SLACK_BOT_TOKEN", "xoxb-test"):
                with patch("config.SLACK_NOTIFY_CHANNEL", "ian-event-aggregator"):
                    slack_notifier.post_file_result(
                        thread_ts="100.0",
                        analysis=self._make_analysis(),
                        nas_path="/NAS/Medical/Labs/labs.pdf",
                        events_created=0,
                        events_proposed=0,
                    )

        call_text = client_mock.chat_postMessage.call_args[1]["text"]
        assert "proposed for approval" not in call_text
        assert ":calendar:" not in call_text


# ── image_pipeline: candidate_to_proposal_item ───────────────────────────────


class TestImagePipelineCandidateToProposalItem:
    def test_serializes_candidate_correctly(self):
        from image_pipeline import _candidate_to_proposal_item
        from dedup import fingerprint
        candidate = _make_candidate("Doctor Appointment", hours=72, source="image")
        item = _candidate_to_proposal_item(candidate, num=5, conflicts=["Other Event"])

        assert item["num"] == 5
        assert item["status"] == "pending"
        assert item["title"] == "Doctor Appointment"
        assert item["source"] == "image"
        assert item["conflicts"] == ["Other Event"]
        assert item["fingerprint"] == fingerprint(candidate)
        assert item["start_dt"] == candidate.start_dt.isoformat()

    def test_proposal_fingerprint_added_to_state(self):
        """When propose mode is on, fingerprints should be added at proposal time."""
        from image_pipeline import _candidate_to_proposal_item
        from dedup import fingerprint
        candidate = _make_candidate("MRI Scan", hours=48, source="image")
        fp = fingerprint(candidate)
        state = state_module.State.__new__(state_module.State)
        state._data = {"written_fingerprints": []}
        state.add_fingerprint(fp)
        assert state.has_fingerprint(fp)
