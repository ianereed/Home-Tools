"""Tests for the Gemini escalation kind (meal_planner_gemini_extract).

The Gemini HTTP call is mocked via extract_recipe_from_gemini; preprocessing is
mocked so no PIL/rasterize toolchain is needed.
"""
from __future__ import annotations

from pathlib import Path

import jobs.kinds.meal_planner_gemini_extract as gem_mod
from meal_planner.db import _SCHEMA, _get_conn
from meal_planner.vision.extract import ExtractResult
from meal_planner.vision.intake_db import get_by_sha, mark_status, record_intake

_SHA = "9e0117e000000001"
_GOOD = {
    "title": "Dal",
    "ingredients": [{"qty": "1", "unit": "cup", "name": "red lentils"}],
    "tags": ["indian"],
}


def _setup(tmp_path, monkeypatch, *, status="gemini_pending", make_file=True):
    import jobs.lib
    import meal_planner.db
    import meal_planner.vision.intake_db as idb

    intake_dir = tmp_path / "photo-intake"
    (intake_dir / "_processing").mkdir(parents=True)
    db_p = tmp_path / "recipes.db"
    with _get_conn(db_p) as c:
        c.executescript(_SCHEMA)

    monkeypatch.setattr(jobs.lib.RequiresSpec, "validate", lambda self: [])
    monkeypatch.setattr(meal_planner.db, "DB_PATH", db_p)
    monkeypatch.setattr(idb, "DB_PATH", db_p)
    monkeypatch.setenv("MEAL_PLANNER_NAS_INTAKE_DIR", str(intake_dir))

    nas = intake_dir / "_processing" / f"{_SHA}.jpg"
    if make_file:
        nas.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
    record_intake(_SHA, source_path="IMG.jpg", nas_path=str(nas), path=db_p)
    mark_status(_SHA, status, db_path=db_p)

    # Preprocess is a no-op image path (the extract is mocked, so it's unused).
    monkeypatch.setattr(gem_mod, "preprocess_to_image", lambda *a, **kw: nas)
    return intake_dir, db_p, nas


def test_gemini_ok_persists_and_moves_to_done(tmp_path, monkeypatch):
    intake_dir, db_p, nas = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        gem_mod, "extract_recipe_from_gemini",
        lambda *a, **kw: ExtractResult(status="ok", parsed=_GOOD, latency_s=1.0, error=None, n_retries=0),
    )

    ret = gem_mod.meal_planner_gemini_extract.func(_SHA)

    assert ret["status"] == "gemini_ok"
    assert ret["recipe_id"] is not None
    row = get_by_sha(_SHA, db_path=db_p)
    assert row.status == "gemini_ok"
    assert row.extraction_path == "gemini"
    assert not nas.exists()
    assert (intake_dir / "_done" / f"{_SHA}.jpg").exists()


def test_gemini_failure_wedges_and_moves(tmp_path, monkeypatch):
    intake_dir, db_p, nas = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        gem_mod, "extract_recipe_from_gemini",
        lambda *a, **kw: ExtractResult(status="validation_fail", parsed=None, latency_s=1.0,
                                       error="bad", n_retries=1),
    )

    ret = gem_mod.meal_planner_gemini_extract.func(_SHA)

    assert ret["status"] == "wedged"
    assert get_by_sha(_SHA, db_path=db_p).status == "wedged"
    assert not nas.exists()
    assert (intake_dir / "_wedged" / f"{_SHA}.jpg").exists()


def test_gemini_no_api_key_wedges(tmp_path, monkeypatch):
    intake_dir, db_p, nas = _setup(tmp_path, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    ret = gem_mod.meal_planner_gemini_extract.func(_SHA)

    assert ret["status"] == "wedged"
    assert "GEMINI_API_KEY" in (get_by_sha(_SHA, db_path=db_p).error or "")
    assert (intake_dir / "_wedged" / f"{_SHA}.jpg").exists()


def test_gemini_skips_non_pending(tmp_path, monkeypatch):
    """A row not in gemini_pending is left untouched (idempotent re-delivery)."""
    intake_dir, db_p, nas = _setup(tmp_path, monkeypatch, status="ok")

    ret = gem_mod.meal_planner_gemini_extract.func(_SHA)

    assert ret["status"] == "skipped_already_handled"
    assert get_by_sha(_SHA, db_path=db_p).status == "ok"
    assert nas.exists()  # untouched
