"""
Tests for the image/PDF intake pipeline.

Covers: Slack file detection (mock), Gemini analyzer (mock), file writer
(staging/NAS), state tracking, and end-to-end --mock --dry-run flow.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from models import CandidateEvent, FileAnalysisResult


# ── Multi-page document mock data tests ─────────────────────────────────────


class TestMultiFileMockData:
    def test_multifile_message_returns_one_message(self):
        from tests.mock_data import slack_multifile_message
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        msgs = slack_multifile_message(since)
        assert len(msgs) == 1

    def test_multifile_message_has_multiple_files(self):
        from tests.mock_data import slack_multifile_message
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        msg = slack_multifile_message(since)[0]
        assert len(msg.metadata["files"]) == 3

    def test_thread_collection_is_flagged(self):
        from tests.mock_data import slack_thread_collection_message
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        msg = slack_thread_collection_message(since)[0]
        assert msg.metadata["is_thread_collection"] is True
        assert len(msg.metadata["files"]) == 3


# ── Gemini analyzer multi-document tests ────────────────────────────────────


class TestAnalyzeDocumentMock:
    def test_analyze_document_mock_returns_result(self):
        from analyzers.image_analyzer import analyze_document
        pages = [
            (b"FAKE_PAGE_1", "page_01.jpg", "image/jpeg"),
            (b"FAKE_PAGE_2", "page_02.jpg", "image/jpeg"),
            (b"FAKE_PAGE_3", "page_03.jpg", "image/jpeg"),
        ]
        result = analyze_document(pages=pages, mock=True)
        assert result is not None
        assert isinstance(result, FileAnalysisResult)
        assert result.primary_category == "Healthcare"
        assert result.confidence > 0

    def test_analyze_document_empty_pages_returns_none(self):
        from analyzers.image_analyzer import analyze_document
        result = analyze_document(pages=[], mock=True)
        assert result is None


# ── Image compression tests ──────────────────────────────────────────────────


class TestPreparePages:
    def _make_real_jpeg(self, width: int = 100, height: int = 100) -> bytes:
        """Create a real JPEG image bytes using Pillow."""
        import io
        from PIL import Image
        img = Image.new("RGB", (width, height), color=(128, 64, 32))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    def test_under_budget_unchanged(self):
        from analyzers.image_analyzer import _prepare_pages
        pages = [
            (b"small" * 100, "p1.jpg", "image/jpeg"),
            (b"small" * 100, "p2.jpg", "image/jpeg"),
        ]
        result = _prepare_pages(pages, max_total_mb=100.0)
        assert result is pages  # same object returned — no copy

    def test_pdfs_not_compressed(self):
        from analyzers.image_analyzer import _prepare_pages
        pdf_bytes = b"PDF" * (1024 * 1024)  # ~3MB
        pages = [(pdf_bytes, "doc.pdf", "application/pdf")]
        result = _prepare_pages(pages, max_total_mb=0.001)
        # PDF should be unchanged
        assert result[0][0] is pdf_bytes
        assert result[0][2] == "application/pdf"

    def test_large_jpeg_is_compressed(self):
        from analyzers.image_analyzer import _prepare_pages
        # Create a real JPEG at 2500x2500 with noise (resists compression)
        import io
        import random
        from PIL import Image
        random.seed(42)
        pixels = [(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                  for _ in range(2500 * 2500)]
        img = Image.new("RGB", (2500, 2500))
        img.putdata(pixels)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        large_jpeg = buf.getvalue()

        # Use a budget tighter than the actual total size
        total_mb = len(large_jpeg) * 3 / (1024 * 1024)
        budget_mb = total_mb * 0.3  # 30% of actual size forces compression

        pages = [(large_jpeg, "big.jpg", "image/jpeg")] * 3
        result = _prepare_pages(pages, max_total_mb=budget_mb)

        total_before = len(large_jpeg) * 3
        total_after = sum(len(b) for b, _, _ in result)
        assert total_after < total_before


# ── File writer multi-page staging tests ────────────────────────────────────


class TestStageDocumentLocally:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.result = FileAnalysisResult(
            file_id="thread_001",
            primary_category="Healthcare",
            subcategory="0-Ian Healthcare",
            confidence=0.92,
            title="Multi-Page Lab Results",
            date="2026-04-20",
            structured_text="--- PAGE 1 ---\nLab results page 1\n--- PAGE 2 ---\nContinued",
            summary="Lab results across 2 pages",
            original_filename="2-page document",
        )

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_page_files(self):
        from writers.file_writer import stage_document_locally
        pages = [
            (b"PAGE_1_CONTENT", "scan_p1.jpg", "image/jpeg"),
            (b"PAGE_2_CONTENT", "scan_p2.png", "image/png"),
            (b"PAGE_3_CONTENT", "scan_p3.jpg", "image/jpeg"),
        ]
        with patch("config.LOCAL_STAGING_DIR", self.tmpdir):
            path = stage_document_locally(self.result, pages, "thread_001")
            staging = Path(path)
            assert (staging / "page_01.jpg").exists()
            assert (staging / "page_02.png").exists()
            assert (staging / "page_03.jpg").exists()
            assert (staging / "extraction.txt").exists()
            meta = json.loads((staging / "_metadata.json").read_text())
            assert meta["page_count"] == 3

    def test_metadata_has_page_count(self):
        from writers.file_writer import stage_document_locally
        pages = [(b"DATA", f"p{i}.jpg", "image/jpeg") for i in range(5)]
        with patch("config.LOCAL_STAGING_DIR", self.tmpdir):
            path = stage_document_locally(self.result, pages, "thread_005")
            meta = json.loads((Path(path) / "_metadata.json").read_text())
            assert meta["page_count"] == 5


# ── Pipeline routing tests ───────────────────────────────────────────────────


class TestPipelineRouting:
    def test_single_file_message_not_thread(self):
        """Single file, no thread flag → should use single-file path."""
        from tests.mock_data import slack_file_messages
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        msgs = slack_file_messages(since)
        single = [m for m in msgs if len(m.metadata["files"]) == 1]
        assert len(single) > 0
        for msg in single:
            assert not msg.metadata.get("is_thread_collection")

    def test_multifile_message_has_multiple_files(self):
        """Multi-file message → pipeline should use document path."""
        from tests.mock_data import slack_multifile_message
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        msgs = slack_multifile_message(since)
        assert len(msgs[0].metadata["files"]) > 1

    def test_thread_collection_flagged(self):
        """Thread collection → pipeline should use document path."""
        from tests.mock_data import slack_thread_collection_message
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        msg = slack_thread_collection_message(since)[0]
        assert msg.metadata["is_thread_collection"] is True


# ── FileAnalysisResult model tests ──────────────────────────────────────────


class TestFileAnalysisResult:
    def test_confidence_clamped(self):
        r = FileAnalysisResult(
            file_id="F1", primary_category="Healthcare", subcategory=None,
            confidence=1.5, title="Test", date=None, structured_text="",
            summary="Test summary",
        )
        assert r.confidence == 1.0

    def test_confidence_clamped_low(self):
        r = FileAnalysisResult(
            file_id="F1", primary_category="Healthcare", subcategory=None,
            confidence=-0.3, title="Test", date=None, structured_text="",
            summary="Test summary",
        )
        assert r.confidence == 0.0

    def test_title_truncated(self):
        r = FileAnalysisResult(
            file_id="F1", primary_category="Healthcare", subcategory=None,
            confidence=0.9, title="A" * 300, date=None, structured_text="",
            summary="Test summary",
        )
        assert len(r.title) == 200


# ── Mock data tests ─────────────────────────────────────────────────────────


class TestSlackFileMockData:
    def test_returns_messages(self):
        from tests.mock_data import slack_file_messages
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        msgs = slack_file_messages(since)
        assert len(msgs) == 3

    def test_message_structure(self):
        from tests.mock_data import slack_file_messages
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        msg = slack_file_messages(since)[0]
        assert msg.source == "slack_file"
        assert "files" in msg.metadata
        assert len(msg.metadata["files"]) > 0
        f = msg.metadata["files"][0]
        assert "id" in f
        assert "name" in f
        assert "mimetype" in f
        assert "url_private_download" in f

    def test_pdf_mock_present(self):
        from tests.mock_data import slack_file_messages
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        msgs = slack_file_messages(since)
        mimetypes = [m.metadata["files"][0]["mimetype"] for m in msgs]
        assert "application/pdf" in mimetypes
        assert "image/png" in mimetypes


# ── Gemini analyzer mock tests ──────────────────────────────────────────────


class TestImageAnalyzerMock:
    def test_mock_returns_result(self):
        from analyzers.image_analyzer import analyze_file
        result = analyze_file(
            file_bytes=b"fake_image_data",
            filename="test.png",
            mimetype="image/png",
            mock=True,
        )
        assert result is not None
        assert isinstance(result, FileAnalysisResult)
        assert result.primary_category == "Healthcare"
        assert result.confidence > 0
        assert result.title
        assert result.summary
        assert result.structured_text

    def test_mock_has_calendar_items(self):
        from analyzers.image_analyzer import analyze_file
        result = analyze_file(
            file_bytes=b"fake", filename="test.png", mimetype="image/png", mock=True,
        )
        assert len(result.calendar_items) > 0
        assert isinstance(result.calendar_items[0], CandidateEvent)


# ── State tracking tests ────────────────────────────────────────────────────


class TestStateFileProcessing:
    def test_round_trip(self):
        import state as state_module
        s = state_module.State({})
        assert not s.is_file_processed("F_TEST_001")
        s.mark_file_processed("F_TEST_001", {"filename": "test.png"})
        assert s.is_file_processed("F_TEST_001")

    def test_prune_respects_recent(self):
        import state as state_module
        s = state_module.State({})
        s.mark_file_processed("F_RECENT", {"filename": "recent.png"})
        s.prune()
        assert s.is_file_processed("F_RECENT")


# ── File writer tests ───────────────────────────────────────────────────────


class TestFileWriter:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.result = FileAnalysisResult(
            file_id="F_TEST_WRITE",
            primary_category="Healthcare",
            subcategory="0-Ian Healthcare",
            confidence=0.92,
            title="Test Document",
            date="2026-04-20",
            structured_text="SOURCE: Test\nDATE: 2026-04-20\n\nTest content here.",
            summary="Test document for unit tests",
            original_filename="test.png",
        )

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_stage_locally_creates_files(self):
        from writers.file_writer import stage_locally
        with patch("config.LOCAL_STAGING_DIR", self.tmpdir):
            path = stage_locally(self.result, b"FAKE_IMAGE_DATA", ".png")
            staging = Path(path)
            assert staging.exists()
            assert (staging / "original.png").exists()
            assert (staging / "extraction.txt").exists()
            assert (staging / "_metadata.json").exists()
            # PDF might fail if reportlab not installed, but txt and original must exist
            meta = json.loads((staging / "_metadata.json").read_text())
            assert meta["file_id"] == "F_TEST_WRITE"
            assert meta["primary_category"] == "Healthcare"

    def test_copy_to_nas_dry_run(self):
        from writers.file_writer import stage_locally, copy_to_nas
        with patch("config.LOCAL_STAGING_DIR", self.tmpdir):
            staging_path = stage_locally(self.result, b"FAKE", ".png")
            nas_dir = tempfile.mkdtemp()
            try:
                with patch("config.NAS_ROOT", nas_dir):
                    # Create expected category directory
                    (Path(nas_dir) / "Healthcare" / "0-Ian Healthcare").mkdir(parents=True)
                    nas_path = copy_to_nas(staging_path, self.result, dry_run=True)
                    assert nas_path is not None
                    assert "Healthcare" in nas_path
                    assert "0-Ian Healthcare" in nas_path
            finally:
                shutil.rmtree(nas_dir, ignore_errors=True)

    def test_copy_to_nas_and_purge(self):
        from writers.file_writer import stage_locally, copy_to_nas, purge_staging
        with patch("config.LOCAL_STAGING_DIR", self.tmpdir):
            staging_path = stage_locally(self.result, b"FAKE_DATA", ".png")
            nas_dir = tempfile.mkdtemp()
            try:
                with patch("config.NAS_ROOT", nas_dir):
                    (Path(nas_dir) / "Healthcare" / "0-Ian Healthcare").mkdir(parents=True)
                    nas_path = copy_to_nas(staging_path, self.result, dry_run=False)
                    assert nas_path is not None
                    # Verify files were copied
                    nas = Path(nas_path)
                    assert (nas / "original.png").exists()
                    assert (nas / "extraction.txt").exists()
                    # _metadata.json should NOT be on NAS
                    assert not (nas / "_metadata.json").exists()
                    # Purge staging
                    purge_staging(staging_path)
                    assert not Path(staging_path).exists()
            finally:
                shutil.rmtree(nas_dir, ignore_errors=True)

    def test_nas_unavailable_returns_none(self):
        from writers.file_writer import stage_locally, copy_to_nas
        with patch("config.LOCAL_STAGING_DIR", self.tmpdir):
            staging_path = stage_locally(self.result, b"FAKE", ".png")
            with patch("config.NAS_ROOT", "/nonexistent/path/that/does/not/exist"):
                nas_path = copy_to_nas(staging_path, self.result)
                assert nas_path is None

    def test_flush_pending_staged(self):
        from writers.file_writer import stage_locally, flush_pending_staged
        with patch("config.LOCAL_STAGING_DIR", self.tmpdir):
            stage_locally(self.result, b"FAKE_DATA", ".png")
            nas_dir = tempfile.mkdtemp()
            try:
                with patch("config.NAS_ROOT", nas_dir):
                    (Path(nas_dir) / "Healthcare" / "0-Ian Healthcare").mkdir(parents=True)
                    flushed = flush_pending_staged(dry_run=False)
                    assert len(flushed) == 1
                    assert flushed[0][0] == "F_TEST_WRITE"
                    # Staging dir should be purged
                    assert not (Path(self.tmpdir) / "F_TEST_WRITE").exists()
            finally:
                shutil.rmtree(nas_dir, ignore_errors=True)
