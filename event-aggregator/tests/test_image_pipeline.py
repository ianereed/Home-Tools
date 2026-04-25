"""
Tests for the image/PDF intake pipeline (local-only, post-cloud-removal).

Covers: local analyzer (mock + unit), file writer (staging/NAS), state
tracking, ingest_local_file end-to-end in mock mode.
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


# ── analyze_document (mock) ───────────────────────────────────────────────────


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
        assert result.primary_category == "Healthcare"
        assert result.confidence > 0

    def test_analyze_document_empty_pages_returns_none(self):
        from analyzers.image_analyzer import analyze_document
        result = analyze_document(pages=[], mock=True)
        assert result is None


# ── File writer multi-page staging ────────────────────────────────────────────


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
            assert (staging / "multi-page-lab-results_page01.jpg").exists()
            assert (staging / "multi-page-lab-results_page02.png").exists()
            assert (staging / "multi-page-lab-results_page03.jpg").exists()
            assert (staging / "extraction.txt").exists()
            assert (staging / "summary.txt").exists()
            meta = json.loads((staging / "_metadata.json").read_text())
            assert meta["page_count"] == 3

    def test_metadata_has_page_count(self):
        from writers.file_writer import stage_document_locally
        pages = [(b"DATA", f"p{i}.jpg", "image/jpeg") for i in range(5)]
        with patch("config.LOCAL_STAGING_DIR", self.tmpdir):
            path = stage_document_locally(self.result, pages, "thread_005")
            meta = json.loads((Path(path) / "_metadata.json").read_text())
            assert meta["page_count"] == 5


# ── FileAnalysisResult model ─────────────────────────────────────────────────


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


# ── Local analyzer mock ──────────────────────────────────────────────────────


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


# ── State tracking ───────────────────────────────────────────────────────────


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


# ── File writer ──────────────────────────────────────────────────────────────


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
            assert (staging / "test-document.png").exists()
            assert (staging / "extraction.txt").exists()
            assert (staging / "summary.txt").exists()
            assert (staging / "_metadata.json").exists()
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
                    nas = Path(nas_path)
                    assert (nas / "test-document.png").exists()
                    assert (nas / "extraction.txt").exists()
                    assert (nas / "summary.txt").exists()
                    assert not (nas / "_metadata.json").exists()
                    assert "/2026/" in nas_path
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
                    assert not (Path(self.tmpdir) / "F_TEST_WRITE").exists()
            finally:
                shutil.rmtree(nas_dir, ignore_errors=True)


# ── Per-page merge ───────────────────────────────────────────────────────────


class TestMergePageResults:
    def _make_page_dict(self, confidence: float = 0.9, page_num: int = 1) -> dict:
        return {
            "classification": {
                "primary_category": "Healthcare/0-Ian Healthcare",
                "confidence": confidence,
                "reasoning": "Medical document",
            },
            "extraction": {
                "document_type": "medical_form",
                "title": f"Lab Results Page {page_num}",
                "date": "2026-04-20",
                "structured_text": f"--- SECTION ---\nPage {page_num} content here",
                "summary": f"Lab results page {page_num}",
            },
        }

    def test_highest_confidence_wins_classification(self):
        from analyzers.image_analyzer import _merge_page_results
        pages = [self._make_page_dict(0.95, 1), self._make_page_dict(0.60, 2)]
        result = _merge_page_results(pages, ["p1.jpg", "p2.jpg"])
        assert result is not None
        assert result.confidence == 0.95
        assert result.title == "Lab Results Page 1"

    def test_structured_text_concatenated(self):
        from analyzers.image_analyzer import _merge_page_results
        pages = [self._make_page_dict(0.9, 1), self._make_page_dict(0.8, 2)]
        result = _merge_page_results(pages, ["p1.jpg", "p2.jpg"])
        assert "Page 1 content" in result.structured_text
        assert "Page 2 content" in result.structured_text
        assert "PAGE 1" in result.structured_text
        assert "PAGE 2" in result.structured_text

    def test_first_non_null_date_wins(self):
        from analyzers.image_analyzer import _merge_page_results
        p1 = self._make_page_dict(0.9, 1)
        p1["extraction"]["date"] = None
        p2 = self._make_page_dict(0.8, 2)
        p2["extraction"]["date"] = "2026-03-15"
        result = _merge_page_results([p1, p2], ["p1.jpg", "p2.jpg"])
        assert result.date == "2026-03-15"

    def test_empty_list_returns_none(self):
        from analyzers.image_analyzer import _merge_page_results
        result = _merge_page_results([], [])
        assert result is None

    def test_category_split_correctly(self):
        from analyzers.image_analyzer import _merge_page_results
        result = _merge_page_results([self._make_page_dict()], ["p.jpg"])
        assert result.primary_category == "Healthcare"
        assert result.subcategory == "0-Ian Healthcare"


# ── Local vision fallback (Ollama unreachable) ───────────────────────────────


class TestAnalyzePageLocalFallback:
    def test_returns_none_when_ollama_unreachable(self):
        from analyzers.image_analyzer import _analyze_page_local
        with patch("config.OLLAMA_BASE_URL", "http://localhost:19999"):
            result = _analyze_page_local(b"fake", "test.jpg", "image/jpeg")
        assert result is None

    def test_returns_none_when_response_malformed(self):
        from analyzers.image_analyzer import _analyze_page_local
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "not valid json at all"}
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp):
            result = _analyze_page_local(b"fake", "test.jpg", "image/jpeg")
        assert result is None


class TestCheckLocalVisionAvailable:
    def test_returns_false_when_ollama_unreachable(self):
        from analyzers.image_analyzer import check_local_vision_available
        with patch("config.OLLAMA_BASE_URL", "http://localhost:19999"):
            assert check_local_vision_available() is False

    def test_returns_false_when_model_not_in_list(self):
        from analyzers.image_analyzer import check_local_vision_available
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "qwen3:14b"}]}
        with patch("requests.get", return_value=mock_resp):
            with patch("config.LOCAL_VISION_MODEL", "qwen2.5vl:7b"):
                assert check_local_vision_available() is False

    def test_returns_true_when_model_present(self):
        from analyzers.image_analyzer import check_local_vision_available
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [{"name": "qwen3:14b"}, {"name": "qwen2.5vl:7b"}]
        }
        with patch("requests.get", return_value=mock_resp):
            with patch("config.LOCAL_VISION_MODEL", "qwen2.5vl:7b"):
                assert check_local_vision_available() is True


# ── ingest_local_file (end-to-end via mock) ──────────────────────────────────


class TestIngestLocalFile:
    def setup_method(self):
        self.staging = tempfile.mkdtemp()
        self.nas = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.staging, ignore_errors=True)
        shutil.rmtree(self.nas, ignore_errors=True)

    def test_mock_ingest_writes_to_staging(self, tmp_path):
        """Mock-mode ingest produces a staging dir and a summary string; no network calls."""
        import state as state_module
        from image_pipeline import ingest_local_file

        file_path = tmp_path / "test.png"
        file_path.write_bytes(b"FAKE_IMAGE_BYTES")

        with patch("config.LOCAL_STAGING_DIR", self.staging), \
             patch("config.NAS_ROOT", self.nas):
            # NAS expects the Healthcare/0-Ian Healthcare tree from the mock result
            (Path(self.nas) / "Healthcare" / "0-Ian Healthcare").mkdir(parents=True)
            state = state_module.State({})
            summary = ingest_local_file(file_path, state, dry_run=False, mock=True)

        assert ":white_check_mark:" in summary
        assert "Healthcare" in summary
