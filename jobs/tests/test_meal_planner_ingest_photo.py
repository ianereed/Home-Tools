"""Phase 16 Chunk 2 — tests for meal_planner_ingest_photo kind."""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import jobs.kinds.meal_planner_ingest_photo as ingest_mod
from meal_planner.db import _SCHEMA, _get_conn
from meal_planner.vision.extract import ExtractResult
from meal_planner.vision.intake_db import get_by_sha, record_intake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_PARSED = {
    "title": "Lemon Tart",
    "ingredients": [
        {"qty": "2", "unit": "cup", "name": "flour"},
        {"qty": "1", "unit": "cup", "name": "butter"},
    ],
    "tags": ["dessert"],
}

_TEST_SHA = "abcd1234ef567890"


def _setup_db(tmp_path: Path) -> Path:
    db_p = tmp_path / "recipes.db"
    with _get_conn(db_p) as c:
        c.executescript(_SCHEMA)
    return db_p


def _setup_intake(intake_dir: Path, db_p: Path) -> Path:
    """Create _processing/<sha>.jpg and a pending DB row. Returns the nas_path."""
    proc_dir = intake_dir / "_processing"
    proc_dir.mkdir(parents=True, exist_ok=True)
    (intake_dir / "_done").mkdir(parents=True, exist_ok=True)

    nas_path = proc_dir / f"{_TEST_SHA}.jpg"
    nas_path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    record_intake(_TEST_SHA, source_path="IMG_9999.jpg", nas_path=str(nas_path), path=db_p)
    return nas_path


def _wire(monkeypatch, intake_dir: Path, db_p: Path, extract_result: ExtractResult):
    """Monkeypatch all external calls so the test is fully self-contained."""
    import jobs.lib
    import meal_planner.db
    import meal_planner.vision.intake_db as idb

    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])
    monkeypatch.setattr(jobs.lib._model_state, "_http_post", lambda *a, **kw: None)
    monkeypatch.setenv("MEAL_PLANNER_NAS_INTAKE_DIR", str(intake_dir))
    monkeypatch.setattr(meal_planner.db, "DB_PATH", db_p)
    monkeypatch.setattr(idb, "DB_PATH", db_p)

    # _process_one: just copy src → dst so the preprocessed file exists
    def _fake_process_one(src, dst, max_dim, autocontrast_cutoff, log_path):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    monkeypatch.setattr(ingest_mod, "_process_one", _fake_process_one)
    monkeypatch.setattr(ingest_mod, "extract_recipe_from_photo", lambda *a, **kw: extract_result)


# ---------------------------------------------------------------------------
# Tests — one per status branch
# ---------------------------------------------------------------------------

def test_ingest_ok_seeds_recipe(tmp_path, monkeypatch):
    """On ok: recipe row + tag + ingredients inserted; file moved to _done/."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    nas_path = _setup_intake(intake_dir, db_p)

    result_ok = ExtractResult(
        status="ok", parsed=_GOOD_PARSED, latency_s=42.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_ok)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    assert ret["status"] == "ok"
    assert ret["recipe_id"] is not None
    assert ret["latency_s"] == 42.0

    # Recipe row exists
    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM recipes WHERE source='nas-intake'").fetchone()
    assert row is not None
    assert row["title"] == "Lemon Tart"
    done_path = str(intake_dir / "_done" / f"{_TEST_SHA}.jpg")
    assert row["photo_path"] == done_path

    # Tag linked
    tag_row = conn.execute(
        "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id=t.id WHERE rt.recipe_id=?",
        (row["id"],),
    ).fetchone()
    assert tag_row is not None
    assert tag_row["name"] == "photo-intake"

    # Ingredients inserted
    ings = conn.execute("SELECT name FROM ingredients WHERE recipe_id=?", (row["id"],)).fetchall()
    assert len(ings) == 2
    conn.close()

    # File moved to _done/
    assert not nas_path.exists()
    assert (intake_dir / "_done" / f"{_TEST_SHA}.jpg").exists()

    # DB row updated
    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row is not None
    assert db_row.status == "ok"
    assert db_row.recipe_id == ret["recipe_id"]
    assert db_row.extraction_path == "ollama"
    assert db_row.completed_at is not None


def test_ingest_pdf_rasterizes_before_preprocess(tmp_path, monkeypatch):
    """A .pdf source is rasterized+stacked before _process_one, and archived in
    _done/ under its real .pdf suffix (not forced to .jpg)."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    proc_dir = intake_dir / "_processing"
    proc_dir.mkdir(parents=True, exist_ok=True)
    (intake_dir / "_done").mkdir(parents=True, exist_ok=True)

    nas_path = proc_dir / f"{_TEST_SHA}.pdf"
    nas_path.write_bytes(b"%PDF-1.4" + b"\x00" * 100)
    record_intake(_TEST_SHA, source_path="recipe.pdf", nas_path=str(nas_path), path=db_p)

    result_ok = ExtractResult(
        status="ok", parsed=_GOOD_PARSED, latency_s=1.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_ok)

    raster_calls = []

    def _fake_raster(src, dst, dpi=200):
        raster_calls.append((Path(src), Path(dst)))
        Path(dst).write_bytes(b"\x89PNG\r\n")  # stand-in rasterized image
        return 2

    monkeypatch.setattr(ingest_mod.rasterize, "pdf_to_stacked_image", _fake_raster)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    assert ret["status"] == "ok"
    # rasterize was invoked on the PDF (not on a .jpg)
    assert len(raster_calls) == 1
    assert raster_calls[0][0] == nas_path
    # archived under its real extension
    done_pdf = intake_dir / "_done" / f"{_TEST_SHA}.pdf"
    assert done_pdf.exists()
    assert not (intake_dir / "_done" / f"{_TEST_SHA}.jpg").exists()
    assert not nas_path.exists()

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT photo_path FROM recipes WHERE source='nas-intake'").fetchone()
    conn.close()
    assert row["photo_path"] == str(done_pdf)


def test_ingest_jpg_does_not_rasterize(tmp_path, monkeypatch):
    """A plain .jpg source must NOT go through the PDF rasterizer."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    _wire(monkeypatch, intake_dir, db_p, ExtractResult(
        status="ok", parsed=_GOOD_PARSED, latency_s=1.0, error=None, n_retries=0,
    ))
    raster = MagicMock()
    monkeypatch.setattr(ingest_mod.rasterize, "pdf_to_stacked_image", raster)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "ok"
    raster.assert_not_called()
    assert (intake_dir / "_done" / f"{_TEST_SHA}.jpg").exists()


def test_ingest_timeout_leaves_file_in_processing(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    nas_path = _setup_intake(intake_dir, db_p)

    result_timeout = ExtractResult(
        status="timeout", parsed=None, latency_s=500.0, error="timed out", n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_timeout)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    assert ret["status"] == "timeout"
    assert ret["recipe_id"] is None
    assert nas_path.exists()  # still in _processing/
    assert not (intake_dir / "_done" / f"{_TEST_SHA}.jpg").exists()

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "timeout"
    assert db_row.error == "timed out"
    assert db_row.completed_at is None  # not terminal


def test_ingest_parse_fail_records_error(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    nas_path = _setup_intake(intake_dir, db_p)

    result_pf = ExtractResult(
        status="parse_fail", parsed=None, latency_s=10.0, error="JSON decode error", n_retries=1,
    )
    _wire(monkeypatch, intake_dir, db_p, result_pf)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "parse_fail"
    assert nas_path.exists()

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "parse_fail"
    assert "JSON" in db_row.error


def test_ingest_validation_fail_records_error(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    nas_path = _setup_intake(intake_dir, db_p)

    result_vf = ExtractResult(
        status="validation_fail",
        parsed={"title": "Bad", "ingredients": [{"qty": "1"}], "tags": []},
        latency_s=15.0,
        error="ingredient_missing_key_name",
        n_retries=1,
    )
    _wire(monkeypatch, intake_dir, db_p, result_vf)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "validation_fail"
    assert nas_path.exists()

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "validation_fail"
    assert "name" in db_row.error


def test_ingest_ollama_error_records_error(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    nas_path = _setup_intake(intake_dir, db_p)

    result_oe = ExtractResult(
        status="ollama_error", parsed=None, latency_s=1.0, error="HTTP 500: internal", n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_oe)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "ollama_error"
    assert nas_path.exists()

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "ollama_error"
    assert "500" in db_row.error


def test_ingest_rename_failure_keeps_photo_path_real(tmp_path, monkeypatch):
    """Option B: rename before DB write. Rename failure → no recipe row, status=ollama_error."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    nas_path = _setup_intake(intake_dir, db_p)

    result_ok = ExtractResult(
        status="ok", parsed=_GOOD_PARSED, latency_s=1.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_ok)

    original_rename = Path.rename

    def _failing_rename(self, dst):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "rename", _failing_rename)

    with pytest.raises(OSError, match="disk full"):
        ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    # No recipe row inserted.
    import sqlite3
    conn = sqlite3.connect(str(db_p))
    row = conn.execute("SELECT * FROM recipes WHERE source='nas-intake'").fetchone()
    conn.close()
    assert row is None

    # photos_intake row marked ollama_error.
    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "ollama_error"

    # Source file still in _processing/ (rename never succeeded).
    assert nas_path.exists()


def test_ingest_crash_records_error(tmp_path, monkeypatch):
    """Unhandled exception from _process_one marks row as ollama_error and re-raises."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    nas_path = _setup_intake(intake_dir, db_p)

    _wire(monkeypatch, intake_dir, db_p, ExtractResult(
        status="ok", parsed=_GOOD_PARSED, latency_s=1.0, error=None, n_retries=0,
    ))
    # Override _process_one to crash after _wire already set it to a copy helper.
    monkeypatch.setattr(ingest_mod, "_process_one", lambda *a, **kw: (_ for _ in ()).throw(OSError("NAS gone")))

    with pytest.raises(OSError, match="NAS gone"):
        ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "ollama_error"
    assert "NAS gone" in db_row.error


def test_ingest_skips_non_pending_row(tmp_path, monkeypatch):
    """If the row status is not 'pending', ingest returns early without extraction."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    # Advance the row past pending
    from meal_planner.vision.intake_db import mark_status
    mark_status(_TEST_SHA, "extracting", db_path=db_p)

    import jobs.lib
    import meal_planner.db
    import meal_planner.vision.intake_db as idb
    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])
    monkeypatch.setattr(jobs.lib._model_state, "_http_post", lambda *a, **kw: None)
    monkeypatch.setenv("MEAL_PLANNER_NAS_INTAKE_DIR", str(intake_dir))
    monkeypatch.setattr(meal_planner.db, "DB_PATH", db_p)
    monkeypatch.setattr(idb, "DB_PATH", db_p)

    extract_mock = MagicMock()
    monkeypatch.setattr(ingest_mod, "extract_recipe_from_photo", extract_mock)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "skipped_already_handled"
    extract_mock.assert_not_called()


def test_ingest_photo_partial_warnings_status_ok_partial(tmp_path, monkeypatch):
    """When some ingredient qtys are compound, status=ok_partial with warnings JSON."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    result_partial = ExtractResult(
        status="ok",
        parsed={
            "title": "X",
            "ingredients": [
                {"name": "a", "qty": 1, "unit": "cup"},
                {"name": "b", "qty": "compound thing", "unit": None},
            ],
            "tags": [],
        },
        latency_s=1.0,
        error=None,
        n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_partial)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    assert ret["status"] == "ok_partial"
    assert ret["warning_count"] == 1

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "ok_partial"
    assert db_row.completed_at is not None
    assert db_row.extraction_warnings is not None

    import json
    warnings = json.loads(db_row.extraction_warnings)
    assert len(warnings) == 1
    assert "compound thing" in warnings[0]


def test_ingest_photo_clean_extraction_status_ok(tmp_path, monkeypatch):
    """When all ingredient qtys are numeric, status=ok with no warnings."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    result_clean = ExtractResult(
        status="ok",
        parsed={
            "title": "Y",
            "ingredients": [
                {"name": "butter", "qty": 2, "unit": "tbsp"},
                {"name": "flour", "qty": "1/2", "unit": "cup"},
            ],
            "tags": [],
        },
        latency_s=1.0,
        error=None,
        n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_clean)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    assert ret["status"] == "ok"
    assert ret["warning_count"] == 0

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "ok"
    assert db_row.completed_at is not None


def test_ingest_photo_writes_sidecar_json(tmp_path, monkeypatch):
    """Successful extraction writes a sidecar JSON next to the photo in _done/."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    result_ok = ExtractResult(
        status="ok", parsed=_GOOD_PARSED, latency_s=1.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_ok)

    ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    sidecar = intake_dir / "_done" / f"{_TEST_SHA}.json"
    assert sidecar.exists(), "sidecar JSON was not written to _done/"
    import json
    parsed = json.loads(sidecar.read_text())
    assert parsed == _GOOD_PARSED


def test_ingest_photo_persists_llm_tags(tmp_path, monkeypatch):
    """LLM-returned tags are persisted alongside the hardcoded 'photo-intake' tag."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    result_tagged = ExtractResult(
        status="ok",
        parsed={
            "title": "Veggie Stir Fry",
            "ingredients": [{"qty": "1", "unit": "cup", "name": "broccoli"}],
            "tags": ["dessert", "quick", "vegetarian"],
        },
        latency_s=1.0,
        error=None,
        n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_tagged)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["recipe_id"] is not None

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    tags = {
        row["name"]
        for row in conn.execute(
            "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id=t.id WHERE rt.recipe_id=?",
            (ret["recipe_id"],),
        ).fetchall()
    }
    conn.close()

    assert "photo-intake" in tags
    assert "dessert" in tags
    assert "quick" in tags
    assert "vegetarian" in tags
    assert len(tags) == 4


def test_ingest_photo_sidecar_failure_does_not_block(tmp_path, monkeypatch):
    """If the sidecar write raises OSError, the recipe row and ok status are unaffected."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    result_ok = ExtractResult(
        status="ok", parsed=_GOOD_PARSED, latency_s=1.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_ok)

    original_write_text = Path.write_text

    def _failing_write_text(self, content, *args, **kwargs):
        if str(self).endswith(".json"):
            raise OSError("disk full")
        return original_write_text(self, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _failing_write_text)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    assert ret["status"] == "ok"
    assert ret["recipe_id"] is not None

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    recipe_count = conn.execute("SELECT COUNT(*) FROM recipes WHERE source='nas-intake'").fetchone()[0]
    conn.close()
    assert recipe_count == 1, "recipe row must be inserted even if sidecar write fails"

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "ok"


# ---------------------------------------------------------------------------
# Phase 19: instructions persistence
# ---------------------------------------------------------------------------


def test_ingest_persists_instructions(tmp_path, monkeypatch):
    """instructions from result.parsed are stored on the recipe row."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    parsed_with_instr = {
        **_GOOD_PARSED,
        "instructions": "1. preheat\n2. mix\n3. bake",
    }
    result = ExtractResult(
        status="ok", parsed=parsed_with_instr, latency_s=10.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "ok"

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT instructions FROM recipes WHERE id=?", (ret["recipe_id"],),
    ).fetchone()
    conn.close()
    assert row["instructions"] == "1. preheat\n2. mix\n3. bake"


def test_ingest_no_instructions_key_persists_null(tmp_path, monkeypatch):
    """When parsed has no 'instructions' key, recipe.instructions stays NULL."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    # _GOOD_PARSED has no 'instructions' key — backward-compat path
    result = ExtractResult(
        status="ok", parsed=_GOOD_PARSED, latency_s=10.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "ok"

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT instructions FROM recipes WHERE id=?", (ret["recipe_id"],),
    ).fetchone()
    conn.close()
    assert row["instructions"] is None


def test_ingest_empty_instructions_normalized_to_null(tmp_path, monkeypatch):
    """Whitespace-only or empty-string instructions normalize to NULL in DB."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    parsed_empty = {**_GOOD_PARSED, "instructions": "   "}
    result = ExtractResult(
        status="ok", parsed=parsed_empty, latency_s=10.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "ok"

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT instructions FROM recipes WHERE id=?", (ret["recipe_id"],),
    ).fetchone()
    conn.close()
    assert row["instructions"] is None


def test_ingest_sidecar_includes_instructions(tmp_path, monkeypatch):
    """Sidecar JSON in _done/ round-trips the instructions field."""
    import json
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    parsed_with_instr = {**_GOOD_PARSED, "instructions": "1. step\n2. step"}
    result = ExtractResult(
        status="ok", parsed=parsed_with_instr, latency_s=10.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "ok"

    sidecar = intake_dir / "_done" / f"{_TEST_SHA}.json"
    assert sidecar.exists()
    parsed = json.loads(sidecar.read_text())
    assert parsed["instructions"] == "1. step\n2. step"


def test_ingest_persists_recipe_book(tmp_path, monkeypatch):
    """Phase 19.5: recipe_book from result.parsed flows to recipes.recipe_book."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    parsed = {**_GOOD_PARSED, "recipe_book": "Serious Eats"}
    result = ExtractResult(
        status="ok", parsed=parsed, latency_s=10.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result)
    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["status"] == "ok"

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT recipe_book FROM recipes WHERE id=?", (ret["recipe_id"],),
    ).fetchone()
    conn.close()
    assert row["recipe_book"] == "Serious Eats"


def test_ingest_no_recipe_book_key_stays_null(tmp_path, monkeypatch):
    """Missing recipe_book key → recipes.recipe_book IS NULL."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    # _GOOD_PARSED has no recipe_book key
    result = ExtractResult(
        status="ok", parsed=_GOOD_PARSED, latency_s=10.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result)
    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT recipe_book FROM recipes WHERE id=?", (ret["recipe_id"],),
    ).fetchone()
    conn.close()
    assert row["recipe_book"] is None


def test_ingest_empty_recipe_book_normalized_to_null(tmp_path, monkeypatch):
    """Whitespace-only recipe_book → NULL in DB."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    parsed = {**_GOOD_PARSED, "recipe_book": "   "}
    result = ExtractResult(
        status="ok", parsed=parsed, latency_s=10.0, error=None, n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result)
    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT recipe_book FROM recipes WHERE id=?", (ret["recipe_id"],),
    ).fetchone()
    conn.close()
    assert row["recipe_book"] is None


# ---------------------------------------------------------------------------
# Misc edge cases
# ---------------------------------------------------------------------------


def test_ingest_photo_invalid_tag_entries_skipped(tmp_path, monkeypatch):
    """Non-string, empty, and whitespace-only tags are silently skipped."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "photo-intake"
    _setup_intake(intake_dir, db_p)

    result_mixed_tags = ExtractResult(
        status="ok",
        parsed={
            "title": "Z",
            "ingredients": [{"qty": "1", "unit": "cup", "name": "water"}],
            "tags": ["dessert", "", 42, "  ", None, "quick"],
        },
        latency_s=1.0,
        error=None,
        n_retries=0,
    )
    _wire(monkeypatch, intake_dir, db_p, result_mixed_tags)

    ret = ingest_mod.meal_planner_ingest_photo.func(_TEST_SHA)
    assert ret["recipe_id"] is not None

    import sqlite3
    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    tags = {
        row["name"]
        for row in conn.execute(
            "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id=t.id WHERE rt.recipe_id=?",
            (ret["recipe_id"],),
        ).fetchall()
    }
    conn.close()

    assert tags == {"photo-intake", "dessert", "quick"}
