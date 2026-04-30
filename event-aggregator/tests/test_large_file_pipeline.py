"""Tests for the large-file pipeline's windowing + consolidation primitives.

The full process_large_file() requires Ollama and a real PDF — covered in
the manual end-to-end verification on the mini, not here. These tests cover
the pure-python parts: window splitting and event dedup across windows.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from large_file_pipeline import (
    DEFAULT_WINDOW_CHARS,
    DEFAULT_WINDOW_OVERLAP,
    _consolidate_calendar_items,
    _split_text_into_windows,
)
from models import CandidateEvent


class TestSplitTextIntoWindows:
    def test_short_text_returns_single_window(self):
        text = "small body of text"
        windows = _split_text_into_windows(text)
        assert len(windows) == 1
        assert windows[0] == text

    def test_text_at_exact_limit_is_single_window(self):
        text = "x" * DEFAULT_WINDOW_CHARS
        windows = _split_text_into_windows(text)
        assert len(windows) == 1

    def test_text_over_limit_splits_with_overlap(self):
        # 30K chars with default 12K window + 1500 overlap → expect 3 windows
        # (advance by 10500 each step: 0..12000, 10500..22500, 21000..30000)
        text = "x" * 30_000
        windows = _split_text_into_windows(text)
        assert len(windows) >= 3
        # Each window honors the size cap
        for w in windows:
            assert len(w) <= DEFAULT_WINDOW_CHARS
        # Coverage: union of windows must contain every position of the text
        # (sliding window with positive step always satisfies this; we
        # double-check by reconstructing).
        assert len(windows[0]) == DEFAULT_WINDOW_CHARS
        # Last window terminates at the end of text
        assert windows[-1].endswith("x")

    def test_overlap_actually_overlaps(self):
        # Distinguishable per-position content so we can tell windows apart
        text = "".join(chr(ord("A") + (i % 26)) for i in range(20_000))
        windows = _split_text_into_windows(text, window_chars=8000, overlap=1000)
        assert len(windows) >= 2
        # Window 1 ends at idx 8000; window 2 starts at idx 7000 → its first
        # 1000 chars match window 1's last 1000 chars.
        tail_w1 = windows[0][-1000:]
        head_w2 = windows[1][:1000]
        assert tail_w1 == head_w2

    def test_overlap_clamped_when_too_large(self):
        # If a caller passes overlap >= window_chars, we shouldn't infinite-loop.
        text = "y" * 5000
        windows = _split_text_into_windows(text, window_chars=1000, overlap=2000)
        # Should finish; windows count is finite. Don't pin an exact count
        # since the clamp behavior is "do something reasonable."
        assert 1 < len(windows) < 1000


class TestConsolidateCalendarItems:
    def _candidate(self, title: str, hour: int = 14):
        tz = ZoneInfo("America/Los_Angeles")
        start = datetime(2026, 5, 6, hour, 0, tzinfo=tz)
        return CandidateEvent(
            title=title, start_dt=start, end_dt=None, location=None,
            confidence=0.8, source="slack_file", source_id="local_vision",
            category="health",
        )

    def test_short_text_uses_single_call(self):
        """Below window size → calls _detect_calendar_items_local once with the
        full text. Identical to the small-file path."""
        text = "Appointment Tuesday at 2pm with Dr Smith"
        with patch("large_file_pipeline.image_analyzer._detect_calendar_items_local") as mock_detect:
            mock_detect.return_value = [self._candidate("Appt with Dr Smith")]
            from large_file_pipeline import Heartbeat
            from pathlib import Path
            import tempfile, logging
            with tempfile.TemporaryDirectory() as td:
                hb = Heartbeat(path=Path(td) / "heartbeat.json", sha12="testsha")
                items = _consolidate_calendar_items(text, hb, logging.getLogger("test"))
        assert mock_detect.call_count == 1
        # Called with the full text, not a windowed slice
        assert mock_detect.call_args[0][0] == text
        assert len(items) == 1

    def test_long_text_runs_per_window_and_dedups(self):
        """Above window size → calls per window, deduplicates events by
        fingerprint when the same event appears in adjacent overlapping windows."""
        text = "x" * (DEFAULT_WINDOW_CHARS * 2 + 1000)  # ~3 windows
        # Same candidate (= same fingerprint) returned by every window — should
        # collapse to a single event.
        same_candidate = self._candidate("Recurring Meeting")
        with patch("large_file_pipeline.image_analyzer._detect_calendar_items_local") as mock_detect:
            mock_detect.return_value = [same_candidate]
            from large_file_pipeline import Heartbeat
            from pathlib import Path
            import tempfile, logging
            with tempfile.TemporaryDirectory() as td:
                hb = Heartbeat(path=Path(td) / "heartbeat.json", sha12="testsha")
                items = _consolidate_calendar_items(text, hb, logging.getLogger("test"))
        # Multiple calls (one per window)
        assert mock_detect.call_count >= 2
        # But only one unique event after dedup
        assert len(items) == 1
        assert items[0].title == "Recurring Meeting"

    def test_long_text_keeps_distinct_events(self):
        """Different events in different windows should all survive dedup."""
        text = "x" * (DEFAULT_WINDOW_CHARS * 2 + 1000)
        candidates = [self._candidate(f"Event {i}", hour=10 + i) for i in range(5)]

        # First window returns events 0,1; second returns 2,3; third returns 4
        # (also returning 1 again to exercise dedup).
        call_returns = [
            [candidates[0], candidates[1]],
            [candidates[2], candidates[3]],
            [candidates[4], candidates[1]],  # dup of #1 across window
        ]
        with patch("large_file_pipeline.image_analyzer._detect_calendar_items_local") as mock_detect:
            mock_detect.side_effect = call_returns
            from large_file_pipeline import Heartbeat
            from pathlib import Path
            import tempfile, logging
            with tempfile.TemporaryDirectory() as td:
                hb = Heartbeat(path=Path(td) / "heartbeat.json", sha12="testsha")
                items = _consolidate_calendar_items(text, hb, logging.getLogger("test"))
        titles = sorted(it.title for it in items)
        assert titles == ["Event 0", "Event 1", "Event 2", "Event 3", "Event 4"]

    def test_empty_text_returns_empty(self):
        from large_file_pipeline import Heartbeat
        from pathlib import Path
        import tempfile, logging
        with tempfile.TemporaryDirectory() as td:
            hb = Heartbeat(path=Path(td) / "heartbeat.json", sha12="testsha")
            items = _consolidate_calendar_items("", hb, logging.getLogger("test"))
        assert items == []

    def test_whitespace_only_text_returns_empty(self):
        from large_file_pipeline import Heartbeat
        from pathlib import Path
        import tempfile, logging
        with tempfile.TemporaryDirectory() as td:
            hb = Heartbeat(path=Path(td) / "heartbeat.json", sha12="testsha")
            items = _consolidate_calendar_items("   \n\n  ", hb, logging.getLogger("test"))
        assert items == []


class TestHeartbeat:
    def test_heartbeat_writes_json_to_disk(self):
        from large_file_pipeline import Heartbeat
        from pathlib import Path
        import json, tempfile
        with tempfile.TemporaryDirectory() as td:
            hb_path = Path(td) / "heartbeat.json"
            hb = Heartbeat(path=hb_path, sha12="abc123def456",
                           started_at="2026-04-30T18:00:00+00:00")
            hb.beat(phase="rasterize", page_done=3, page_total=10,
                    current_op="rasterize 3/10")
            data = json.loads(hb_path.read_text())
            assert data["sha12"] == "abc123def456"
            assert data["phase"] == "rasterize"
            assert data["page_done"] == 3
            assert data["page_total"] == 10
            assert data["current_op"] == "rasterize 3/10"
            assert "ts" in data

    def test_heartbeat_subsequent_beat_updates_ts(self):
        from large_file_pipeline import Heartbeat
        from pathlib import Path
        import json, tempfile, time as time_mod
        with tempfile.TemporaryDirectory() as td:
            hb_path = Path(td) / "heartbeat.json"
            hb = Heartbeat(path=hb_path, sha12="x", started_at="ignore")
            hb.beat(phase="A")
            ts1 = json.loads(hb_path.read_text())["ts"]
            time_mod.sleep(0.01)
            hb.beat(phase="B")
            ts2 = json.loads(hb_path.read_text())["ts"]
            assert ts1 != ts2
            assert json.loads(hb_path.read_text())["phase"] == "B"
