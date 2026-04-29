"""
Tests for the gmail-aware status-tag lifecycle:
  • write tagged events directly (awaiting / proposed_by_me)
  • round-trip confirmation_status through schema + propose flow
  • Slack approve strips the tag; Slack reject deletes the event
  • title prefix helpers (status_prefix / strip_status_prefix / _display_title)
  • cross-thread confirmation via is_update + confirmed
  • cancellation via thread reply
  • native GCal invites land in invite_context (never written)
  • dashboard renders "On calendar — awaiting confirmation"

These tests use synthetic fixtures only — never hit the network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import config
import state as state_module
from models import CandidateEvent, RawMessage
from writers import google_calendar as gcal_writer


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_candidate(
    title="Coffee with Sarah",
    confirmation_status="awaiting_me",
    source="gmail",
    confidence=0.9,
    confidence_band="high",
    thread_id="thr_abc",
    is_update=False,
    is_cancellation=False,
    original_title_hint=None,
    gcal_event_id_to_update=None,
) -> CandidateEvent:
    return CandidateEvent(
        title=title,
        start_dt=_utcnow() + timedelta(days=2),
        end_dt=_utcnow() + timedelta(days=2, hours=1),
        location=None,
        confidence=confidence,
        source=source,
        source_id="gmail_test_1",
        source_url="https://mail.google.com/mail/u/0/#all/test_1",
        confidence_band=confidence_band,
        confirmation_status=confirmation_status,
        thread_id=thread_id,
        is_update=is_update,
        is_cancellation=is_cancellation,
        original_title_hint=original_title_hint,
        gcal_event_id_to_update=gcal_event_id_to_update,
    )


# ── writer: prefix helpers ───────────────────────────────────────────────────


class TestStatusPrefix:
    def test_status_prefix_awaiting(self):
        assert gcal_writer._status_prefix("awaiting_me") == "[awaiting] "

    def test_status_prefix_proposed(self):
        assert gcal_writer._status_prefix("proposed_by_me") == "[proposed by you] "

    def test_status_prefix_confirmed_is_empty(self):
        assert gcal_writer._status_prefix("confirmed") == ""

    def test_status_prefix_unknown_is_empty(self):
        assert gcal_writer._status_prefix("garbage") == ""

    def test_strip_awaiting_prefix(self):
        assert gcal_writer.strip_status_prefix("[awaiting] Coffee") == "Coffee"

    def test_strip_proposed_prefix(self):
        assert gcal_writer.strip_status_prefix("[proposed by you] Coffee") == "Coffee"

    def test_strip_low_confidence_prefix(self):
        assert gcal_writer.strip_status_prefix("[?] Coffee") == "Coffee"

    def test_strip_idempotent(self):
        assert gcal_writer.strip_status_prefix("Coffee") == "Coffee"

    def test_strip_no_known_prefix(self):
        assert gcal_writer.strip_status_prefix("[bogus] Coffee") == "[bogus] Coffee"

    def test_strip_handles_double_prefix(self):
        # Defensive: if a doubled prefix ever sneaks in, both should strip.
        assert gcal_writer.strip_status_prefix("[awaiting] [?] Coffee") == "Coffee"


class TestDisplayTitle:
    def test_awaiting_takes_precedence_over_low_confidence(self):
        c = _make_candidate(
            title="Coffee", confirmation_status="awaiting_me",
            confidence_band="medium",
        )
        assert gcal_writer._display_title(c) == "[awaiting] Coffee"

    def test_proposed_by_me_prefix(self):
        c = _make_candidate(
            title="Lunch", confirmation_status="proposed_by_me",
            confidence_band="high",
        )
        assert gcal_writer._display_title(c) == "[proposed by you] Lunch"

    def test_confirmed_high_no_prefix(self):
        c = _make_candidate(
            title="Standup", confirmation_status="confirmed",
            confidence_band="high",
        )
        assert gcal_writer._display_title(c) == "Standup"

    def test_confirmed_medium_keeps_question_mark(self):
        c = _make_candidate(
            title="Review", confirmation_status="confirmed",
            confidence_band="medium",
        )
        assert gcal_writer._display_title(c) == "[?] Review"


# ── state: pending_confirmations + invite_context ────────────────────────────


class TestPendingConfirmationsState:
    def test_add_and_find_by_num(self):
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_1",
            calendar_id="weekend",
            original_title="Coffee",
            current_tag="[awaiting]",
            fingerprint="fp_1",
            start_iso=_utcnow().isoformat(),
            num=42,
        )
        entry = s.find_pending_confirmation_by_num(42)
        assert entry is not None
        assert entry["gcal_event_id"] == "evt_1"
        assert entry["current_tag"] == "[awaiting]"

    def test_find_by_gcal_id(self):
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_x", calendar_id="weekend",
            original_title="Lunch", current_tag="[proposed by you]",
            fingerprint="fp_x", start_iso=_utcnow().isoformat(), num=7,
        )
        entry = s.find_pending_confirmation_by_gcal_id("evt_x")
        assert entry is not None
        assert entry["original_title"] == "Lunch"

    def test_remove_by_num(self):
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_1", calendar_id="cal", original_title="X",
            current_tag="[awaiting]", fingerprint="fp",
            start_iso=_utcnow().isoformat(), num=1,
        )
        removed = s.remove_pending_confirmation_by_num(1)
        assert removed["gcal_event_id"] == "evt_1"
        assert s.find_pending_confirmation_by_num(1) is None

    def test_find_by_thread_id(self):
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_1", calendar_id="cal", original_title="X",
            current_tag="[awaiting]", fingerprint="fp",
            start_iso=_utcnow().isoformat(), num=1, thread_id="thr_42",
        )
        entry = s.find_pending_confirmation_by_thread_id("thr_42")
        assert entry is not None
        assert entry["gcal_event_id"] == "evt_1"

    def test_expire_past_start(self):
        s = state_module.State({})
        past = (_utcnow() - timedelta(hours=2)).isoformat()
        future = (_utcnow() + timedelta(days=2)).isoformat()
        s.add_pending_confirmation(
            gcal_event_id="past", calendar_id="cal", original_title="Past",
            current_tag="[awaiting]", fingerprint="fp_past",
            start_iso=past, num=1,
        )
        s.add_pending_confirmation(
            gcal_event_id="fut", calendar_id="cal", original_title="Future",
            current_tag="[awaiting]", fingerprint="fp_fut",
            start_iso=future, num=2,
        )
        expired = s.expire_pending_confirmations()
        assert len(expired) == 1
        assert expired[0]["gcal_event_id"] == "past"
        assert s.find_pending_confirmation_by_num(2) is not None
        assert s.find_pending_confirmation_by_num(1) is None


class TestInviteContext:
    def test_record_and_retrieve(self):
        s = state_module.State({})
        s.record_invite_context(
            gcal_event_id="invite_1",
            title="Q3 Review",
            start_iso=_utcnow().isoformat(),
            attendees=["alice@example.com", "bob@example.com"],
            source_url="https://calendar.google.com/foo",
        )
        invites = s.invite_context()
        assert "invite_1" in invites
        assert invites["invite_1"]["title"] == "Q3 Review"
        assert invites["invite_1"]["attendees"] == ["alice@example.com", "bob@example.com"]

    def test_remove(self):
        s = state_module.State({})
        s.record_invite_context(
            gcal_event_id="i1", title="X", start_iso=_utcnow().isoformat(),
        )
        removed = s.remove_invite_context("i1")
        assert removed is not None
        assert "i1" not in s.invite_context()


# ── extractor: confirmation_status round-trip ────────────────────────────────


class TestExtractorConfirmationStatus:
    def test_validate_event_accepts_confirmation_status(self):
        from extractor import _validate_event
        future = (_utcnow() + timedelta(days=3)).isoformat()
        raw = {
            "title": "Coffee",
            "start": future,
            "confidence": 0.9,
            "confirmation_status": "confirmed",
            "category": "social",
            "date_certainty": "specific",
        }
        ev = _validate_event(raw)
        assert ev is not None
        assert ev.confirmation_status == "confirmed"

    def test_validate_event_invalid_status_falls_back_to_default(self):
        from extractor import _validate_event
        future = (_utcnow() + timedelta(days=3)).isoformat()
        raw = {
            "title": "Coffee", "start": future, "confidence": 0.9,
            "confirmation_status": "garbage",
        }
        ev = _validate_event(raw, default_confirmation_status="proposed_by_me")
        assert ev is not None
        assert ev.confirmation_status == "proposed_by_me"

    def test_validate_event_missing_status_uses_default(self):
        from extractor import _validate_event
        future = (_utcnow() + timedelta(days=3)).isoformat()
        raw = {"title": "Coffee", "start": future, "confidence": 0.9}
        ev = _validate_event(raw, default_confirmation_status="awaiting_me")
        assert ev is not None
        assert ev.confirmation_status == "awaiting_me"

    def test_outbound_gmail_default_is_proposed_by_me(self):
        # Verify the extract() helper picks proposed_by_me when is_from_me=True
        # and the LLM omits the field.
        from extractor import extract
        msg = RawMessage(
            id="m1", source="gmail",
            timestamp=_utcnow(),
            body_text="See you Thursday at 2pm",
            metadata={
                "from": "me@example.com",
                "subject": "Coffee?",
                "to": "them@example.com",
                "is_from_me": True,
                "thread_id": "thr_1",
                "thread_digest": [],
            },
        )
        future = (_utcnow() + timedelta(days=3)).isoformat()
        with patch("extractor._call_ollama", return_value={
            "events": [{
                "title": "Coffee",
                "start": future,
                "confidence": 0.9,
                "category": "social",
                "date_certainty": "specific",
                # confirmation_status omitted on purpose
            }],
            "todos": [],
        }):
            events, _ = extract(msg)
            assert len(events) == 1
            assert events[0].confirmation_status == "proposed_by_me"

    def test_inbound_gmail_default_is_awaiting_me(self):
        from extractor import extract
        msg = RawMessage(
            id="m2", source="gmail",
            timestamp=_utcnow(),
            body_text="Want to grab coffee Thursday?",
            metadata={
                "from": "them@example.com",
                "subject": "Coffee?",
                "to": "me@example.com",
                "is_from_me": False,
                "thread_id": "thr_2",
                "thread_digest": [],
            },
        )
        future = (_utcnow() + timedelta(days=3)).isoformat()
        with patch("extractor._call_ollama", return_value={
            "events": [{
                "title": "Coffee",
                "start": future,
                "confidence": 0.9,
                "category": "social",
                "date_certainty": "specific",
            }],
            "todos": [],
        }):
            events, _ = extract(msg)
            assert len(events) == 1
            assert events[0].confirmation_status == "awaiting_me"


class TestExtractorThreadDigest:
    def test_thread_digest_appears_in_prompt(self):
        from extractor import _build_prompt
        msg = RawMessage(
            id="m3", source="gmail",
            timestamp=_utcnow(),
            body_text="Want to grab coffee Thursday?",
            metadata={
                "from": "them@example.com",
                "subject": "Coffee?",
                "is_from_me": False,
                "thread_id": "thr_3",
                "thread_digest": [
                    {"from_me": False, "ts": "2026-04-25T10:00",
                     "subject": "Coffee?", "snippet": "Want to grab coffee Thursday?"},
                    {"from_me": True, "ts": "2026-04-25T11:00",
                     "subject": "Re: Coffee?", "snippet": "Sounds good, see you then"},
                ],
            },
        )
        prompt = _build_prompt(msg, calendar_context="")
        assert "[me]" in prompt
        assert "[them]" in prompt
        assert "Sounds good" in prompt
        assert "Direction: this message was sent to the user (inbound)" in prompt

    def test_outbound_marker_in_prompt(self):
        from extractor import _build_prompt
        msg = RawMessage(
            id="m4", source="gmail",
            timestamp=_utcnow(),
            body_text="How about Thursday at 2?",
            metadata={
                "from": "me@example.com",
                "is_from_me": True,
                "thread_id": "thr_4",
                "thread_digest": [],
            },
        )
        prompt = _build_prompt(msg, calendar_context="")
        assert "Direction: this message was sent by the user (outbound)" in prompt


# ── propose flow split ───────────────────────────────────────────────────────


class TestProposeEventsBranching:
    def test_gmail_awaiting_writes_tagged_and_registers(self):
        from main import _propose_events
        s = state_module.State({})
        cand = _make_candidate(confirmation_status="awaiting_me")

        fake_outcome = gcal_writer.Inserted(
            written=MagicMock(
                gcal_event_id="gcal_evt_99",
                fingerprint="fp_test",
                candidate=cand,
            ),
            conflicts=[],
        )
        with patch.object(gcal_writer, "write_event", return_value=fake_outcome) as m_write:
            counts = _propose_events([cand], s, snapshot={}, dry_run=False, mock=True)

        # Mock mode logs but doesn't actually call write_event
        assert counts["proposed"] == 1
        # Mock branch records fingerprint but not gcal_event_id
        # (we test the write path via a real-mode unit test below)

    def test_gmail_awaiting_real_mode_writes_and_registers(self):
        from main import _write_tagged_event
        s = state_module.State({})
        cand = _make_candidate(confirmation_status="awaiting_me")

        fake_outcome = gcal_writer.Inserted(
            written=MagicMock(
                gcal_event_id="gcal_evt_99",
                fingerprint="fp_test",
                candidate=cand,
            ),
            conflicts=[],
        )
        with patch.object(gcal_writer, "write_event", return_value=fake_outcome):
            ok = _write_tagged_event(cand, s, snapshot={}, dry_run=False, mock=False)
        assert ok
        confirmations = s.pending_confirmations()
        assert len(confirmations) == 1
        assert confirmations[0]["gcal_event_id"] == "gcal_evt_99"
        assert confirmations[0]["current_tag"] == "[awaiting]"
        assert confirmations[0]["original_title"] == "Coffee with Sarah"
        assert confirmations[0]["thread_id"] == "thr_abc"

    def test_gmail_proposed_by_me_writes_with_proposed_tag(self):
        from main import _write_tagged_event
        s = state_module.State({})
        cand = _make_candidate(confirmation_status="proposed_by_me")
        fake_outcome = gcal_writer.Inserted(
            written=MagicMock(
                gcal_event_id="gcal_evt_outbound",
                fingerprint="fp_out",
                candidate=cand,
            ),
            conflicts=[],
        )
        with patch.object(gcal_writer, "write_event", return_value=fake_outcome):
            _write_tagged_event(cand, s, snapshot={}, dry_run=False, mock=False)
        confirmations = s.pending_confirmations()
        assert len(confirmations) == 1
        assert confirmations[0]["current_tag"] == "[proposed by you]"

    def test_gmail_confirmed_falls_through_to_propose(self):
        # A gmail event with status=confirmed should NOT auto-write tagged —
        # it goes through the legacy propose flow.
        from main import _propose_events
        s = state_module.State({})
        cand = _make_candidate(confirmation_status="confirmed")

        with patch.object(gcal_writer, "write_event") as m_write:
            counts = _propose_events([cand], s, snapshot={}, dry_run=False, mock=True)

        # No tagged-write was attempted (mock mode short-circuits anyway)
        assert counts["proposed"] == 1
        assert len(s.pending_confirmations()) == 0
        # Confirmed gmail event lands as a normal proposal
        assert any(
            i.get("title") == "Coffee with Sarah"
            for batch in s._data.get("pending_proposals", [])
            for i in batch.get("items", [])
        )

    def test_gcal_invite_records_context_no_write(self):
        from main import _propose_events
        s = state_module.State({})
        cand = CandidateEvent(
            title="Q3 Review",
            start_dt=_utcnow() + timedelta(days=5),
            end_dt=_utcnow() + timedelta(days=5, hours=1),
            location=None,
            confidence=0.95,
            source="gcal",
            source_id="gcal_invite_1",
            source_url="https://cal.example/foo",
            confirmation_status="awaiting_me",
        )
        with patch.object(gcal_writer, "write_event") as m_write:
            counts = _propose_events([cand], s, snapshot={}, dry_run=False, mock=True)

        m_write.assert_not_called()
        invites = s.invite_context()
        assert "invite_1" in invites
        assert invites["invite_1"]["title"] == "Q3 Review"
        # No new pending_confirmation, no proposal item
        assert s.pending_confirmations() == []
        assert counts["proposed"] == 0


# ── cli approve / reject route by num source ─────────────────────────────────


class TestCliApproveRejectRouting:
    def test_approve_pending_confirmation_strips_tag(self):
        from cli import _apply_approve
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_strip", calendar_id="weekend",
            original_title="Coffee with Sarah",
            current_tag="[awaiting]",
            fingerprint="fp_strip",
            start_iso=_utcnow().isoformat(),
            num=99,
            source="gmail",
        )
        with patch.object(gcal_writer, "confirm_event", return_value=True) as m_confirm, \
             patch("state.save"):
            approved, errors = _apply_approve(s, [99])
        assert approved == 1
        assert errors == []
        m_confirm.assert_called_once_with("weekend", "evt_strip", dry_run=False)
        # state mutated
        assert s.find_pending_confirmation_by_num(99) is None
        # written_events refreshed with stripped title
        assert "evt_strip" in s.get_written_events()
        assert s.get_written_events()["evt_strip"]["title"] == "Coffee with Sarah"

    def test_reject_pending_confirmation_deletes_event(self):
        from cli import _apply_reject
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_kill", calendar_id="weekend",
            original_title="Coffee", current_tag="[awaiting]",
            fingerprint="fp_kill",
            start_iso=_utcnow().isoformat(), num=88, source="gmail",
        )
        with patch.object(gcal_writer, "delete_event", return_value=True) as m_del, \
             patch("state.save"):
            rejected, errors = _apply_reject(s, [88])
        assert rejected == 1
        assert errors == []
        m_del.assert_called_once_with("weekend", "evt_kill", dry_run=False)
        # entry removed, fingerprint moved to rejected
        assert s.find_pending_confirmation_by_num(88) is None
        assert s.is_rejected("fp_kill")

    def test_approve_unknown_num_returns_error(self):
        from cli import _apply_approve
        s = state_module.State({})
        with patch("state.save"):
            approved, errors = _apply_approve(s, [12345])
        assert approved == 0
        assert any("not pending" in e for e in errors)


# ── cross-thread confirmation via _try_resolve_pending_confirmation ──────────


class TestThreadConfirmation:
    def test_confirmed_update_strips_tag(self):
        from main import _try_resolve_pending_confirmation
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_thread_conf", calendar_id="weekend",
            original_title="Coffee", current_tag="[awaiting]",
            fingerprint="fp_thr",
            start_iso=_utcnow().isoformat(), num=10,
            thread_id="thr_xyz", source="gmail",
        )
        cand = _make_candidate(
            title="Coffee", confirmation_status="confirmed",
            is_update=True, original_title_hint="Coffee",
            gcal_event_id_to_update="evt_thread_conf",
        )
        cand.gcal_calendar_id_to_update = "weekend"

        fake_written = MagicMock(
            gcal_event_id="evt_thread_conf",
            fingerprint="fp_thr",
            candidate=cand,
        )
        with patch.object(gcal_writer, "update_event", return_value=(fake_written, [])):
            action = _try_resolve_pending_confirmation(cand, s, dry_run=False, mock=False)
        assert action == "confirmed"
        assert s.find_pending_confirmation_by_gcal_id("evt_thread_conf") is None

    def test_cancellation_via_thread_deletes(self):
        from main import _try_resolve_pending_confirmation
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_cancel", calendar_id="weekend",
            original_title="Coffee", current_tag="[awaiting]",
            fingerprint="fp_cancel",
            start_iso=_utcnow().isoformat(), num=11,
            source="gmail",
        )
        cand = _make_candidate(
            title="Coffee", confirmation_status="awaiting_me",
            is_cancellation=True,
            original_title_hint="Coffee",
            gcal_event_id_to_update="evt_cancel",
        )
        cand.gcal_calendar_id_to_update = "weekend"

        with patch.object(gcal_writer, "delete_event", return_value=True):
            action = _try_resolve_pending_confirmation(cand, s, dry_run=False, mock=False)
        assert action == "cancelled"
        assert s.find_pending_confirmation_by_gcal_id("evt_cancel") is None
        assert s.is_rejected("fp_cancel")

    def test_no_match_returns_none(self):
        from main import _try_resolve_pending_confirmation
        s = state_module.State({})
        cand = _make_candidate(
            confirmation_status="confirmed",
            is_update=True,
            original_title_hint="Some Other Event",
            gcal_event_id_to_update="evt_does_not_exist",
        )
        action = _try_resolve_pending_confirmation(cand, s, dry_run=False, mock=False)
        assert action is None


# ── dashboard rendering ──────────────────────────────────────────────────────


class TestDashboardRendersConfirmations:
    def test_pending_confirmation_renders_section(self):
        from notifiers.slack_notifier import build_dashboard_blocks
        confirmations = [{
            "num": 7,
            "gcal_event_id": "evt_x",
            "calendar_id": "weekend",
            "original_title": "Coffee with Sarah",
            "current_tag": "[awaiting]",
            "start_dt": (_utcnow() + timedelta(days=2)).isoformat(),
            "source": "gmail",
            "source_url": "https://mail.example/x",
        }]
        blocks = build_dashboard_blocks(
            [], "2026-04-28", pending_confirmations=confirmations,
        )
        section_texts = [
            b["text"]["text"] for b in blocks if b["type"] == "section"
        ]
        assert any("On calendar" in t and "awaiting confirmation" in t for t in section_texts)
        assert any("[awaiting]" in t and "Coffee with Sarah" in t for t in section_texts)

    def test_dashboard_footer_shows_awaiting_count(self):
        from notifiers.slack_notifier import build_dashboard_blocks
        confirmations = [
            {"num": i, "gcal_event_id": f"e{i}", "calendar_id": "w",
             "original_title": "X", "current_tag": "[awaiting]",
             "start_dt": (_utcnow() + timedelta(days=2)).isoformat()}
            for i in range(3)
        ]
        blocks = build_dashboard_blocks(
            [], "2026-04-28", pending_confirmations=confirmations,
        )
        footer = blocks[-1]
        assert "3 awaiting" in footer["elements"][0]["text"]


# ── gmail connector: thread digest ───────────────────────────────────────────


class TestGmailThreadDigest:
    def test_is_from_me_via_sent_label(self):
        from connectors.gmail import _is_from_me
        msg = {"labelIds": ["SENT", "INBOX"], "payload": {"headers": []}}
        assert _is_from_me(msg, "ian@example.com") is True

    def test_is_from_me_via_from_header(self):
        from connectors.gmail import _is_from_me
        msg = {
            "labelIds": ["INBOX"],
            "payload": {"headers": [{"name": "From", "value": "Ian <ian@example.com>"}]},
        }
        assert _is_from_me(msg, "ian@example.com") is True

    def test_not_from_me(self):
        from connectors.gmail import _is_from_me
        msg = {
            "labelIds": ["INBOX"],
            "payload": {"headers": [{"name": "From", "value": "Other <other@example.com>"}]},
        }
        assert _is_from_me(msg, "ian@example.com") is False

    def test_thread_digest_marks_direction(self):
        from connectors.gmail import _build_thread_digest
        thread = {
            "messages": [
                {
                    "internalDate": "1700000000000",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "them@example.com"},
                            {"name": "Subject", "value": "Coffee?"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": ""},
                    },
                },
                {
                    "internalDate": "1700001000000",
                    "labelIds": ["SENT"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "ian@example.com"},
                            {"name": "Subject", "value": "Re: Coffee?"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": ""},
                    },
                },
            ],
        }
        digest = _build_thread_digest(thread, "ian@example.com")
        assert len(digest) == 2
        assert digest[0]["from_me"] is False
        assert digest[1]["from_me"] is True


# ── GCal-direct edit detection ───────────────────────────────────────────────


class TestGCalDirectEditDetection:
    def test_user_strips_tag_silent_confirms(self):
        from main import _process_pending_confirmations
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_strip", calendar_id="weekend",
            original_title="Coffee", current_tag="[awaiting]",
            fingerprint="fp_strip",
            start_iso=(_utcnow() + timedelta(days=2)).isoformat(),
            num=1,
        )
        # Simulate GCal showing the same event but with the tag stripped.
        fake_service = MagicMock()
        fake_service.events.return_value.get.return_value.execute.return_value = {
            "summary": "Coffee",  # no prefix anymore
            "status": "confirmed",
        }
        with patch("main.google_auth.get_credentials", return_value=MagicMock()), \
             patch("main.build", return_value=fake_service):
            counts = _process_pending_confirmations(s)
        assert counts["silent_confirmed"] == 1
        assert s.find_pending_confirmation_by_gcal_id("evt_strip") is None
        # written_events refreshed
        assert "evt_strip" in s.get_written_events()
        assert s.get_written_events()["evt_strip"]["title"] == "Coffee"

    def test_user_deletes_silent_rejects(self):
        from main import _process_pending_confirmations
        from googleapiclient.errors import HttpError

        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_gone", calendar_id="weekend",
            original_title="Lunch", current_tag="[awaiting]",
            fingerprint="fp_gone",
            start_iso=(_utcnow() + timedelta(days=2)).isoformat(),
            num=2, source="gmail",
        )
        fake_service = MagicMock()
        fake_resp = MagicMock(status=404)
        http_err = HttpError(fake_resp, b"")
        fake_service.events.return_value.get.return_value.execute.side_effect = http_err
        with patch("main.google_auth.get_credentials", return_value=MagicMock()), \
             patch("main.build", return_value=fake_service):
            counts = _process_pending_confirmations(s)
        assert counts["silent_rejected"] == 1
        assert s.find_pending_confirmation_by_gcal_id("evt_gone") is None
        assert s.is_rejected("fp_gone")

    def test_unchanged_tagged_event_no_op(self):
        from main import _process_pending_confirmations
        s = state_module.State({})
        s.add_pending_confirmation(
            gcal_event_id="evt_same", calendar_id="weekend",
            original_title="Coffee", current_tag="[awaiting]",
            fingerprint="fp_same",
            start_iso=(_utcnow() + timedelta(days=2)).isoformat(),
            num=3,
        )
        fake_service = MagicMock()
        fake_service.events.return_value.get.return_value.execute.return_value = {
            "summary": "[awaiting] Coffee",
            "status": "confirmed",
        }
        with patch("main.google_auth.get_credentials", return_value=MagicMock()), \
             patch("main.build", return_value=fake_service):
            counts = _process_pending_confirmations(s)
        assert counts["silent_confirmed"] == 0
        assert counts["silent_rejected"] == 0
        # entry still present
        assert s.find_pending_confirmation_by_gcal_id("evt_same") is not None
