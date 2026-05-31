"""Unit tests for meal_planner.vision: extract.py, _ollama.validate_schema, intake_db.py.

Coverage:
- ExtractResult across all five status branches (ok / timeout / parse_fail /
  validation_fail / ollama_error). The Ollama HTTP call is mocked.
- validate_schema: the same shapes the bench has been gating on.
- intake_db CRUD: record_intake dedup, mark_status round-trip via get_by_sha,
  list_pending filter.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from meal_planner.db import _SCHEMA, _get_conn
from meal_planner.vision import _ollama, extract, intake_db
from meal_planner.vision._ollama import validate_schema
from meal_planner.vision.extract import ExtractResult, extract_recipe_from_photo


# ---------------------------------------------------------------------------
# extract_recipe_from_photo — five status branches
# ---------------------------------------------------------------------------


def _photo(tmp_path: Path) -> Path:
    p = tmp_path / "recipe.jpg"
    p.write_bytes(b"\xff\xd8\xff")
    return p


def _good_payload() -> dict:
    return {
        "title": "Brown Butter Cookies",
        "ingredients": [
            {"qty": "2", "unit": "cup", "name": "flour"},
            {"qty": "1", "unit": "cup", "name": "butter"},
        ],
        "tags": ["baking"],
    }


def test_extract_status_ok(monkeypatch, tmp_path):
    photo = _photo(tmp_path)
    payload = _good_payload()

    def mock_post(*args, **kwargs):
        body = {"model": "llama3.2-vision:11b", "response": json.dumps(payload), "eval_count": 30}
        m = MagicMock()
        m.status_code = 200
        m.text = json.dumps(body)
        m.json.return_value = body
        return m

    monkeypatch.setattr(_ollama.requests, "post", mock_post)
    res = extract_recipe_from_photo(photo, base_url="http://localhost:11434")

    assert res.status == "ok"
    assert res.parsed == payload
    assert res.error is None
    assert res.latency_s is not None and res.latency_s >= 0
    assert res.n_retries == 0


def test_extract_status_parse_fail(monkeypatch, tmp_path):
    """Model returns text that does not parse as JSON. Both first call and retry fail."""
    photo = _photo(tmp_path)

    def mock_post(*args, **kwargs):
        body = {"model": "llama3.2-vision:11b", "response": "I can't read this image.", "eval_count": 5}
        m = MagicMock()
        m.status_code = 200
        m.text = json.dumps(body)
        m.json.return_value = body
        return m

    monkeypatch.setattr(_ollama.requests, "post", mock_post)
    res = extract_recipe_from_photo(photo)

    assert res.status == "parse_fail"
    assert res.parsed is None
    assert res.error is not None
    assert res.n_retries == 1  # call_ollama_vision retries once on schema fail


def test_extract_status_validation_fail(monkeypatch, tmp_path):
    """Model returns valid JSON but missing the 'name' key on every retry."""
    photo = _photo(tmp_path)
    bad = {
        "title": "Bad Recipe",
        "ingredients": [{"qty": "1", "unit": "cup"}],  # missing name
        "tags": [],
    }

    def mock_post(*args, **kwargs):
        body = {"model": "llama3.2-vision:11b", "response": json.dumps(bad), "eval_count": 8}
        m = MagicMock()
        m.status_code = 200
        m.text = json.dumps(body)
        m.json.return_value = body
        return m

    monkeypatch.setattr(_ollama.requests, "post", mock_post)
    res = extract_recipe_from_photo(photo)

    assert res.status == "validation_fail"
    assert res.parsed is not None  # we surface the parsed-but-invalid dict
    assert res.error is not None
    assert "name" in res.error  # error mentions the missing key


def test_extract_status_ollama_error_500(monkeypatch, tmp_path):
    """Non-200 response is unrecoverable — extract.py classifies as ollama_error."""
    photo = _photo(tmp_path)

    def mock_post(*args, **kwargs):
        m = MagicMock()
        m.status_code = 500
        m.text = "internal error"
        return m

    monkeypatch.setattr(_ollama.requests, "post", mock_post)
    res = extract_recipe_from_photo(photo)

    assert res.status == "ollama_error"
    assert res.parsed is None
    assert res.error is not None
    assert "HTTP 500" in res.error


def test_extract_status_timeout(monkeypatch, tmp_path):
    """requests.Timeout from the underlying call surfaces as status=timeout."""
    photo = _photo(tmp_path)

    def mock_post(*args, **kwargs):
        raise requests.Timeout("Read timed out")

    monkeypatch.setattr(_ollama.requests, "post", mock_post)
    res = extract_recipe_from_photo(photo, timeout_s=1)

    assert res.status == "timeout"
    assert res.parsed is None
    assert res.error is not None
    assert "timed out" in res.error.lower()


def test_extract_passes_timeout_param(monkeypatch, tmp_path):
    """timeout_s parameter must reach requests.post as the timeout kwarg."""
    photo = _photo(tmp_path)
    captured: dict = {}

    def mock_post(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        body = {"model": "llama3.2-vision:11b", "response": json.dumps(_good_payload()), "eval_count": 1}
        m = MagicMock()
        m.status_code = 200
        m.text = json.dumps(body)
        m.json.return_value = body
        return m

    monkeypatch.setattr(_ollama.requests, "post", mock_post)
    extract_recipe_from_photo(photo, timeout_s=500)

    assert captured["timeout"] == 500


# ---------------------------------------------------------------------------
# validate_schema — corner cases
# ---------------------------------------------------------------------------


def test_validate_schema_rejects_not_a_dict():
    ok, errs = validate_schema(["not", "a", "dict"])
    assert ok is False
    assert "not_a_dict" in errs


def test_validate_schema_rejects_ingredients_not_list():
    ok, errs = validate_schema({"title": "X", "ingredients": "stringy", "tags": []})
    assert ok is False
    assert "ingredients_not_list" in errs


def test_validate_schema_rejects_missing_ingredient_key():
    ok, errs = validate_schema({
        "title": "X",
        "ingredients": [{"qty": "1", "unit": "cup"}],  # no name
        "tags": [],
    })
    assert ok is False
    assert any("ingredient_missing_key_name" in e for e in errs)


def test_validate_schema_accepts_full_recipe():
    ok, errs = validate_schema({
        "title": "Apple Pie",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": ["dessert"],
    })
    assert ok is True
    assert errs == []


def test_validate_schema_accepts_null_title_sentinel():
    """Prompt allows title=null as 'not a recipe' sentinel."""
    ok, _ = validate_schema({"title": None, "ingredients": [], "tags": []})
    assert ok is True


# ---------------------------------------------------------------------------
# validate_schema — instructions field (Phase 19)
# ---------------------------------------------------------------------------


def test_validate_schema_accepts_string_instructions():
    ok, errs = validate_schema({
        "title": "Apple Pie",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
        "instructions": "1. Preheat oven.\n2. Mix dry ingredients.",
    })
    assert ok is True
    assert errs == []


def test_validate_schema_accepts_null_instructions():
    ok, errs = validate_schema({
        "title": "Apple Pie",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
        "instructions": None,
    })
    assert ok is True
    assert errs == []


def test_validate_schema_accepts_missing_instructions_key():
    """Backward compat: pre-Phase-19 responses without `instructions` stay valid."""
    ok, errs = validate_schema({
        "title": "Apple Pie",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
    })
    assert ok is True
    assert errs == []


def test_validate_schema_rejects_non_string_instructions():
    ok, errs = validate_schema({
        "title": "Apple Pie",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
        "instructions": 123,
    })
    assert ok is False
    assert "instructions_not_str_or_null" in errs


def test_recipe_extraction_prompt_includes_instructions_schema():
    """Canary: prompt file must declare the instructions field for the LLM.

    Cheap regression — survives prose rewording but breaks if a future edit
    drops the schema entry.
    """
    from meal_planner.vision._ollama import load_prompt
    text = load_prompt()
    # Schema spec line
    assert '"instructions": string|null' in text
    # Example response includes the field
    assert '"instructions": "1.' in text


def test_validate_schema_accepts_string_recipe_book():
    ok, errs = validate_schema({
        "title": "Pie",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
        "recipe_book": "NYT Cooking",
    })
    assert ok is True
    assert errs == []


def test_validate_schema_accepts_null_recipe_book():
    ok, errs = validate_schema({
        "title": "Pie",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
        "recipe_book": None,
    })
    assert ok is True


def test_validate_schema_accepts_missing_recipe_book_key():
    ok, errs = validate_schema({
        "title": "Pie",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
    })
    assert ok is True


def test_validate_schema_rejects_non_string_recipe_book():
    ok, errs = validate_schema({
        "title": "Pie",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
        "recipe_book": ["NYT", "Cooking"],
    })
    assert ok is False
    assert "recipe_book_not_str_or_null" in errs


def test_recipe_extraction_prompt_includes_recipe_book_schema():
    from meal_planner.vision._ollama import load_prompt
    text = load_prompt()
    assert '"recipe_book": string|null' in text
    assert '"recipe_book":' in text  # also in the example


# ---------------------------------------------------------------------------
# intake_db — CRUD round-trips on an in-memory schema
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test_recipes.db"
    with _get_conn(p) as c:
        c.executescript(_SCHEMA)
    return p


def test_record_intake_dedup(db_path: Path):
    assert intake_db.record_intake("sha-aaa", "/orig/foo.jpg", "/nas/foo.jpg", path=db_path) is True
    assert intake_db.record_intake("sha-aaa", "/orig/foo.jpg", "/nas/foo.jpg", path=db_path) is False


def test_mark_status_roundtrips(db_path: Path):
    from meal_planner.db import insert_recipe
    recipe_id = insert_recipe(title="Test", path=db_path)

    intake_db.record_intake("sha-bbb", "/orig/b.jpg", "/nas/b.jpg", path=db_path)
    intake_db.mark_status(
        "sha-bbb",
        "ok",
        recipe_id=recipe_id,
        extraction_path="ollama",
        db_path=db_path,
    )

    row = intake_db.get_by_sha("sha-bbb", db_path=db_path)
    assert row is not None
    assert row.status == "ok"
    assert row.recipe_id == recipe_id
    assert row.extraction_path == "ollama"
    assert row.completed_at is not None  # terminal status sets completed_at


def test_mark_status_non_terminal_no_completed_at(db_path: Path):
    intake_db.record_intake("sha-ccc", "/orig/c.jpg", "/nas/c.jpg", path=db_path)
    intake_db.mark_status("sha-ccc", "extracting", db_path=db_path)
    row = intake_db.get_by_sha("sha-ccc", db_path=db_path)
    assert row is not None
    assert row.status == "extracting"
    assert row.completed_at is None


def test_list_pending_filters(db_path: Path):
    from meal_planner.db import insert_recipe
    rid = insert_recipe(title="x", path=db_path)

    intake_db.record_intake("sha-d1", "/orig/d1.jpg", "/nas/d1.jpg", path=db_path)
    intake_db.record_intake("sha-d2", "/orig/d2.jpg", "/nas/d2.jpg", path=db_path)
    intake_db.record_intake("sha-d3", "/orig/d3.jpg", "/nas/d3.jpg", path=db_path)
    intake_db.mark_status("sha-d2", "ok", recipe_id=rid, db_path=db_path)

    pending = intake_db.list_pending(db_path=db_path)
    pending_shas = {r.sha for r in pending}
    assert pending_shas == {"sha-d1", "sha-d3"}


def test_get_by_sha_missing_returns_none(db_path: Path):
    assert intake_db.get_by_sha("sha-nope", db_path=db_path) is None


def test_init_intake_table_idempotent_on_existing_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # init_intake_table on an empty conn should create the table even though
    # the recipes table doesn't exist (no FK enforcement on missing parent
    # at table-create time).
    intake_db.init_intake_table(conn)
    intake_db.init_intake_table(conn)  # second call must not error
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='photos_intake'"
    ).fetchall()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Bake-off backwards-compat — the shadowed names on bake_off must still work
# ---------------------------------------------------------------------------


def test_bake_off_shadowed_names_still_resolve():
    """Existing bench tests import _call_ollama_vision and patch _unload_ollama on bake_off."""
    from meal_planner.eval import bake_off
    assert bake_off._call_ollama_vision is _ollama.call_ollama_vision
    assert bake_off._unload_ollama is _ollama.unload_ollama
    assert bake_off._validate_schema is _ollama.validate_schema
    assert bake_off._ollama_default_ctx_for is _ollama.default_ctx_for
    assert bake_off._load_prompt is _ollama.load_prompt
    assert bake_off._NUM_CTX_TABLE is _ollama.NUM_CTX_TABLE


# ---------------------------------------------------------------------------
# Normalizer integration — call_ollama_vision normalizes fused qty/unit output
# ---------------------------------------------------------------------------


def test_call_ollama_vision_normalizes_fused_qty_unit(monkeypatch, tmp_path):
    """LLM returns fused qty — call_ollama_vision returns normalized dict + warning."""
    photo = _photo(tmp_path)
    fused_payload = {
        "title": "Sausage Orzo",
        "ingredients": [
            {"qty": "1 teaspoon", "unit": None, "name": "olive oil"},
            {"qty": "2", "unit": "cup", "name": "orzo"},  # already ok — no-op
        ],
        "tags": ["pasta"],
    }

    def mock_post(*args, **kwargs):
        body = {"model": "llama3.2-vision:11b", "response": json.dumps(fused_payload), "eval_count": 20}
        m = MagicMock()
        m.status_code = 200
        m.text = json.dumps(body)
        m.json.return_value = body
        return m

    monkeypatch.setattr(_ollama.requests, "post", mock_post)
    parsed, metadata = _ollama.call_ollama_vision(
        "llama3.2-vision:11b", photo, "Extract recipe.", base_url="http://localhost:11434"
    )

    assert parsed is not None
    first_ing = parsed["ingredients"][0]
    assert first_ing["qty"] == "1", f"qty should be '1', got {first_ing['qty']!r}"
    assert first_ing["unit"] == "teaspoon", f"unit should be 'teaspoon', got {first_ing['unit']!r}"
    assert first_ing["name"] == "olive oil"

    second_ing = parsed["ingredients"][1]
    assert second_ing["qty"] == "2"   # already ok, unchanged
    assert second_ing["unit"] == "cup"

    assert "normalize_warnings" in metadata, "metadata should carry normalize_warnings"
    warns = metadata["normalize_warnings"]
    assert len(warns) == 1
    assert "teaspoon" in warns[0]


def test_call_ollama_vision_normalizes_schema_invalid_retry(monkeypatch, tmp_path):
    """First call returns schema-valid garbage that triggers retry; retry returns a
    schema-INVALID dict (missing 'name' on one ingredient) but the other ingredient
    is fused. Normalizer must still run on the retry result so the well-formed
    ingredient gets its qty/unit split — H3 from the Phase 16 review.
    """
    photo = _photo(tmp_path)
    # First response: schema-invalid (ingredient missing 'name' key) — triggers retry.
    first_payload = {
        "title": "Bad",
        "ingredients": [{"qty": "1", "unit": "cup"}],  # missing name
        "tags": [],
    }
    # Retry response: still schema-invalid (still missing 'name') but contains a fused qty.
    retry_payload = {
        "title": "Bad",
        "ingredients": [
            {"qty": "1", "unit": "cup"},  # still missing name
            {"qty": "2 tablespoons", "unit": None, "name": "olive oil"},  # fused, fixable
        ],
        "tags": [],
    }
    calls = {"n": 0}

    def mock_post(*args, **kwargs):
        calls["n"] += 1
        payload = first_payload if calls["n"] == 1 else retry_payload
        body = {"model": "llama3.2-vision:11b", "response": json.dumps(payload), "eval_count": 5}
        m = MagicMock()
        m.status_code = 200
        m.text = json.dumps(body)
        m.json.return_value = body
        return m

    monkeypatch.setattr(_ollama.requests, "post", mock_post)
    parsed, metadata = _ollama.call_ollama_vision(
        "llama3.2-vision:11b", photo, "Extract recipe.", base_url="http://localhost:11434"
    )

    assert calls["n"] == 2, "retry should have happened"
    assert metadata["n_retries"] == 1
    # The fused ingredient on the retry path must have been normalized despite
    # the overall response still failing schema validation.
    fixable = parsed["ingredients"][1]
    assert fixable["qty"] == "2", f"expected '2', got {fixable['qty']!r}"
    assert fixable["unit"] == "tablespoons", f"expected 'tablespoons', got {fixable['unit']!r}"
    assert "normalize_warnings" in metadata
    assert any("tablespoons" in w for w in metadata["normalize_warnings"])
    # The schema-invalid ingredient is preserved untouched (no 'name' key added).
    assert "name" not in parsed["ingredients"][0]
