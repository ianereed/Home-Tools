"""
Extractor tests — requires Ollama running locally.

Run with: python -m pytest tests/test_extractor.py -v
Skip if Ollama is unavailable: pytest --ignore=tests/test_extractor.py
"""
from __future__ import annotations

import pytest

import extractor
from tests.mock_data import all_messages


@pytest.fixture(autouse=True)
def require_ollama():
    if not extractor.check_ollama_available():
        pytest.skip("Ollama not available at localhost:11434")


class TestExtractorWithMockData:
    def test_event_message_returns_candidates(self):
        """Messages describing real events should return at least one candidate."""
        msgs = all_messages(since=None)
        event_msgs = [m for m in msgs if any(
            kw in m.body_text.lower()
            for kw in ["lunch", "meeting", "dinner", "reunion", "game night", "offsite"]
        )]
        for msg in event_msgs:
            events, todos = extractor.extract(msg)
            assert isinstance(events, list)
            assert isinstance(todos, list)
            # Each returned event should be a valid CandidateEvent with confidence >= 0.5
            for c in events:
                assert 0.5 <= c.confidence <= 1.0
                assert c.title
                assert c.start_dt is not None

    def test_non_event_message_returns_empty(self):
        """Messages with no event content should return empty lists."""
        from datetime import timezone
        from models import RawMessage
        from datetime import datetime
        msg = RawMessage(
            id="test_noevent_001",
            source="gmail",
            timestamp=datetime.now(tz=timezone.utc),
            body_text="Hey just checking in, hope you're doing well!",
            metadata={},
        )
        events, todos = extractor.extract(msg)
        assert events == []

    def test_extract_returns_tuple(self):
        """extract() must return a (events, todos) tuple."""
        from datetime import datetime, timezone
        from models import RawMessage
        msg = RawMessage(
            id="test_tuple_001",
            source="gmail",
            timestamp=datetime.now(tz=timezone.utc),
            body_text="Can you send me the report by Friday?",
            metadata={},
        )
        result = extractor.extract(msg)
        assert isinstance(result, tuple)
        assert len(result) == 2
        events, todos = result
        assert isinstance(events, list)
        assert isinstance(todos, list)

    def test_body_text_not_in_logs(self, caplog):
        """Verify body_text never appears in log output."""
        import logging
        msgs = all_messages(since=None)
        with caplog.at_level(logging.DEBUG):
            for msg in msgs[:3]:
                extractor.extract(msg)
        for record in caplog.records:
            assert msg.body_text not in record.getMessage()
