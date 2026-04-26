"""
Tests for the Tier 2.4 worker / queue plumbing.

Covers state.enqueue_*/pop_*/depth, dedup-on-enqueue, swap-decision lifecycle,
and stale-decision auto-resolution. Does NOT call Ollama or hit GCal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import state as state_module


def _state():
    return state_module.State({})


class TestTextQueue:
    def test_enqueue_and_depth(self):
        s = _state()
        assert s.text_queue_depth() == 0
        s.enqueue_text_job(
            source="gmail", msg_id="m1", body_text="hi",
            metadata={"subject": "Test"}, timestamp_iso="2026-04-26T12:00:00+00:00",
        )
        assert s.text_queue_depth() == 1

    def test_enqueue_dedup(self):
        s = _state()
        s.enqueue_text_job(
            source="gmail", msg_id="m1", body_text="hi",
            metadata={}, timestamp_iso="2026-04-26T12:00:00+00:00",
        )
        s.enqueue_text_job(
            source="gmail", msg_id="m1", body_text="hi (dup)",
            metadata={}, timestamp_iso="2026-04-26T12:00:00+00:00",
        )
        assert s.text_queue_depth() == 1

    def test_pop_fifo(self):
        s = _state()
        for i in range(3):
            s.enqueue_text_job(
                source="gmail", msg_id=f"m{i}", body_text="x",
                metadata={}, timestamp_iso="2026-04-26T12:00:00+00:00",
            )
        first = s.pop_text_job()
        assert first["id"] == "m0"
        assert s.text_queue_depth() == 2

    def test_pop_empty(self):
        s = _state()
        assert s.pop_text_job() is None


class TestOcrQueue:
    def test_enqueue_and_depth(self):
        s = _state()
        s.enqueue_ocr_job("/tmp/passport.pdf")
        assert s.ocr_queue_depth() == 1

    def test_enqueue_dedup(self):
        s = _state()
        s.enqueue_ocr_job("/tmp/passport.pdf")
        s.enqueue_ocr_job("/tmp/passport.pdf")
        assert s.ocr_queue_depth() == 1

    def test_peek_does_not_pop(self):
        s = _state()
        s.enqueue_ocr_job("/tmp/a.pdf")
        peek = s.peek_ocr_job()
        assert peek["file_path"] == "/tmp/a.pdf"
        assert s.ocr_queue_depth() == 1

    def test_pop_fifo(self):
        s = _state()
        s.enqueue_ocr_job("/tmp/a.pdf")
        s.enqueue_ocr_job("/tmp/b.pdf")
        first = s.pop_ocr_job()
        assert first["file_path"] == "/tmp/a.pdf"
        assert s.ocr_queue_depth() == 1


class TestSwapDecisions:
    def test_add_and_resolve(self):
        s = _state()
        did = s.add_swap_decision("/tmp/x.pdf", text_queue_depth=4)
        assert s.get_swap_decision(did)["decision"] == "pending"
        assert s.resolve_swap_decision(did, "interrupt") is True
        assert s.get_swap_decision(did)["decision"] == "interrupt"

    def test_resolve_unknown(self):
        s = _state()
        assert s.resolve_swap_decision("nope", "wait") is False

    def test_stale_auto_wait(self):
        from worker import _expire_stale_swap_decisions
        s = _state()
        did = s.add_swap_decision("/tmp/x.pdf", text_queue_depth=2)
        # Backdate the decision past the timeout.
        s._data["swap_decisions"][did]["created_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        _expire_stale_swap_decisions(s)
        assert s.get_swap_decision(did)["decision"] == "wait"
        assert s.get_swap_decision(did)["auto_resolved"] is True


class TestWorkerStatus:
    def test_status_round_trip(self):
        s = _state()
        s.update_worker_status(text_queue=3, ocr_queue=1, current_model="qwen3:14b")
        st = s.worker_status()
        assert st["text_queue"] == 3
        assert st["ocr_queue"] == 1
        assert "updated_at" in st


class TestPreClassifier:
    def test_disabled_returns_maybe(self, monkeypatch):
        # When disabled, pre_classify shouldn't even hit Ollama — it returns
        # "maybe" so the caller falls through to full extraction.
        import extractor
        import config
        from models import RawMessage
        from datetime import datetime, timezone
        monkeypatch.setattr(config, "PRE_CLASSIFIER_ENABLED", False)
        msg = RawMessage(
            id="x", source="gmail",
            timestamp=datetime.now(timezone.utc),
            body_text="anything",
        )
        verdict, reason = extractor.pre_classify(msg)
        assert verdict == "maybe"
        assert "disabled" in reason

    def test_classifier_error_falls_open(self, monkeypatch):
        # Ollama unreachable → "maybe", never "no" — never drop a real event.
        import extractor
        import config
        from models import RawMessage
        from datetime import datetime, timezone
        import requests as _requests

        monkeypatch.setattr(config, "PRE_CLASSIFIER_ENABLED", True)

        def boom(*a, **kw):
            raise _requests.ConnectionError("no ollama")
        monkeypatch.setattr(extractor.requests, "post", boom)

        msg = RawMessage(
            id="x", source="gmail",
            timestamp=datetime.now(timezone.utc),
            body_text="meet me at 3pm tomorrow",
        )
        verdict, _reason = extractor.pre_classify(msg)
        assert verdict == "maybe"

    def test_classifier_yes_no_maybe(self, monkeypatch):
        import extractor
        import config
        from models import RawMessage
        from datetime import datetime, timezone

        monkeypatch.setattr(config, "PRE_CLASSIFIER_ENABLED", True)

        class FakeResp:
            def __init__(self, payload):
                self._payload = payload
            def raise_for_status(self): pass
            def json(self): return {"response": self._payload}

        for verdict in ("yes", "no", "maybe"):
            payload = '{"verdict": "' + verdict + '", "reason": "test"}'
            monkeypatch.setattr(
                extractor.requests, "post",
                lambda *a, _p=payload, **kw: FakeResp(_p),
            )
            msg = RawMessage(
                id="x", source="gmail",
                timestamp=datetime.now(timezone.utc),
                body_text="x",
            )
            v, _r = extractor.pre_classify(msg)
            assert v == verdict
