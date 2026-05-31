"""Phase 14.10 — tests for meal_planner_send_to_todoist Job kind.

HTTP calls are mocked at meal_planner.todoist_client.create_task (relocated for Phase 21 v2) so
that assertions on payload["source_id"], output_config["labels"], and
output_config["section_id"] are possible without routing through requests.post.
The priority-mapping test additionally patches requests.post to verify that
the todoist_writer maps "normal" → int 2 in the final HTTP payload.

TODOIST_SECTIONS, TODOIST_API_TOKEN, and TODOIST_PROJECT_ID are set via
monkeypatch.setenv — real .env is never read.
No Gemini mocks: Phase 14.10 removed the consolidation call entirely.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests as _requests

from meal_planner.db import init_db, insert_ingredient, insert_recipe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MEALS_SECTION_ID = "sec-meals"
_SECTIONS = {
    "Produce": "sec-prod",
    "Pantry": "sec-pantry",
    "Other": "sec-other",
    "Meals": _MEALS_SECTION_ID,
}
_SECTIONS_JSON = json.dumps(_SECTIONS)


class _TodoistResp:
    """Canned Todoist success response (used only in the priority test)."""

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"id": "task-42"}


def _setup_db_one_recipe(db_path: Path, *, title: str = "Test Pasta") -> int:
    """Create a recipe with 2 ingredients. Returns recipe_id."""
    init_db(db_path)
    rid = insert_recipe(title=title, base_servings=4, path=db_path)
    insert_ingredient(
        recipe_id=rid,
        name="olive oil",
        qty_per_serving=0.25,
        unit="cup",
        todoist_section="Pantry",
        path=db_path,
    )
    insert_ingredient(
        recipe_id=rid,
        name="garlic",
        qty_per_serving=1.0,
        unit="clove",
        todoist_section="Produce",
        path=db_path,
    )
    return rid


def _make_env(monkeypatch: Any, *, sections_json: str = _SECTIONS_JSON) -> None:
    monkeypatch.setenv("TODOIST_SECTIONS", sections_json)
    monkeypatch.setenv("TODOIST_API_TOKEN", "test-token")
    monkeypatch.setenv("TODOIST_PROJECT_ID", "proj-1")


def _adapter_mock(monkeypatch: Any) -> list[dict]:
    """Patch jobs.adapters.todoist.create_task; return list that captures calls."""
    import meal_planner.todoist_client as _todoist_adapter

    captured: list[dict] = []

    def fake_create_task(output_config: dict, payload: dict) -> dict:
        captured.append({"output_config": output_config, "payload": payload})
        return {"created": True}

    monkeypatch.setattr(_todoist_adapter, "create_task", fake_create_task)
    return captured


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_happy_path_emits_one_task_per_scaled_ingredient(monkeypatch, tmp_path: Path) -> None:
    """1 recipe × 2 ingredients → 3 adapter calls (1 header + 2 ingredients), all meal-planner."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    rid = _setup_db_one_recipe(db_path)
    _make_env(monkeypatch)
    captured = _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid, 4]])
    out = result(blocking=True, timeout=5)

    assert out["items_sent"] == 3
    assert out["items_attempted"] == 3
    assert len(captured) == 3
    for call in captured:
        assert call["output_config"]["labels"] == ["meal-planner"]


def test_send_to_todoist_returns_full_result_shape(monkeypatch, tmp_path: Path) -> None:
    """Result dict has all Phase 17 Chunk C keys with correct no-consolidate defaults."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    rid = _setup_db_one_recipe(db_path)
    _make_env(monkeypatch)
    _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid, 4]])
    out = result(blocking=True, timeout=5)

    assert "items_sent" in out
    assert "items_attempted" in out
    assert out["consolidate_failed"] is None
    assert out["consolidate_dropped"] == 0
    assert out["error"] is None


def test_title_includes_scaled_qty_and_recipe_suffix(monkeypatch, tmp_path: Path) -> None:
    """Title contains scaled qty (0.25 × 4 = 1.0 → '1') and ends with ' (Test Pasta)'."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    init_db(db_path)
    rid = insert_recipe(title="Test Pasta", base_servings=4, path=db_path)
    insert_ingredient(
        recipe_id=rid,
        name="olive oil",
        qty_per_serving=0.25,
        unit="cup",
        todoist_section="Pantry",
        path=db_path,
    )
    _make_env(monkeypatch)
    captured = _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid, 4]])
    result(blocking=True, timeout=5)

    # captured[0] = header task; captured[1] = ingredient task
    assert len(captured) == 2
    ingredient_call = next(c for c in captured if c["output_config"]["section_id"] != _MEALS_SECTION_ID)
    title = ingredient_call["payload"]["title"]
    # scaled qty: 0.25 × 4 = 1.0 → "1" (:.4g)
    assert "1" in title
    assert "cup" in title
    assert "olive oil" in title
    assert title.endswith(" (Test Pasta)")


def test_section_drift_falls_back_to_first_section(monkeypatch, tmp_path: Path) -> None:
    """Ingredient with todoist_section='Bakery' (not in sections) → first section's id."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    init_db(db_path)
    rid = insert_recipe(title="Test Bread", base_servings=4, path=db_path)
    insert_ingredient(
        recipe_id=rid,
        name="bread flour",
        qty_per_serving=0.5,
        unit="cup",
        todoist_section="Bakery",  # not in _SECTIONS
        path=db_path,
    )
    _make_env(monkeypatch)
    captured = _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid, 4]])
    out = result(blocking=True, timeout=5)

    assert out["items_sent"] == 2  # header + 1 ingredient
    first_section_id = list(_SECTIONS.values())[0]  # "sec-prod"
    # captured[0] is the header (Meals); captured[1] is the ingredient (fallback)
    ingredient_call = next(c for c in captured if c["output_config"]["section_id"] != _MEALS_SECTION_ID)
    assert ingredient_call["output_config"]["section_id"] == first_section_id


def test_priority_is_string_normal_maps_to_int_2(monkeypatch, tmp_path: Path) -> None:
    """Kind passes priority='normal'; todoist_writer maps it to int 2 in the HTTP payload."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    init_db(db_path)
    rid = insert_recipe(title="Test Pasta", base_servings=4, path=db_path)
    insert_ingredient(
        recipe_id=rid,
        name="salt",
        qty_per_serving=0.25,
        unit="tsp",
        todoist_section="Pantry",
        path=db_path,
    )
    _make_env(monkeypatch)

    # Check the HTTP payload via requests.post to verify the int mapping
    http_captured: list[dict] = []

    def fake_post(url, *args, **kwargs):
        http_captured.append(kwargs.get("json") or {})
        return _TodoistResp()

    monkeypatch.setattr(_requests, "post", fake_post)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid, 4]])
    result(blocking=True, timeout=5)

    # _PRIORITY_MAP in todoist_writer maps "normal" → 2
    assert http_captured[0]["priority"] == 2


def test_multiple_recipes_emit_separate_tasks(monkeypatch, tmp_path: Path) -> None:
    """2 recipes, no ingredient overlap → total calls = sum; correct recipe suffixes; source_id."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    init_db(db_path)

    rid_a = insert_recipe(title="Pasta A", base_servings=4, path=db_path)
    insert_ingredient(recipe_id=rid_a, name="pasta", qty_per_serving=100.0,
                      unit="g", todoist_section="Pantry", path=db_path)
    insert_ingredient(recipe_id=rid_a, name="tomato", qty_per_serving=2.0,
                      unit=None, todoist_section="Produce", path=db_path)

    rid_b = insert_recipe(title="Soup B", base_servings=4, path=db_path)
    insert_ingredient(recipe_id=rid_b, name="broth", qty_per_serving=250.0,
                      unit="ml", todoist_section="Pantry", path=db_path)

    _make_env(monkeypatch)
    captured = _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid_a, 2], [rid_b, 4]])
    out = result(blocking=True, timeout=5)

    # recipe A: 1 header + 2 ingredients = 3; recipe B: 1 header + 1 ingredient = 2 → 5 total
    assert out["items_attempted"] == 5
    assert out["items_sent"] == 5
    assert len(captured) == 5

    titles = [c["payload"]["title"] for c in captured]
    # ingredient titles end with " (Pasta A)" / " (Soup B)"; headers do not
    pasta_a_titles = [t for t in titles if t.endswith(" (Pasta A)")]
    soup_b_titles = [t for t in titles if t.endswith(" (Soup B)")]
    assert len(pasta_a_titles) == 2
    assert len(soup_b_titles) == 1

    # ingredient source_ids use recipes:{rid}; header source_ids use recipes:{rid}:header
    source_ids = [c["payload"]["source_id"] for c in captured]
    assert source_ids.count(f"recipes:{rid_a}") == 2
    assert source_ids.count(f"recipes:{rid_b}") == 1
    assert source_ids.count(f"recipes:{rid_a}:header") == 1
    assert source_ids.count(f"recipes:{rid_b}:header") == 1


# ---------------------------------------------------------------------------
# Phase 17 Chunk D — recipe-title header task tests
# ---------------------------------------------------------------------------

def test_send_emits_recipe_header_in_meals_section(monkeypatch, tmp_path: Path) -> None:
    """Single recipe with 3 ingredients → 4 adapter calls; exactly 1 goes to Meals section."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    init_db(db_path)
    rid = insert_recipe(title="Tomato Soup", base_servings=4, path=db_path)
    for name, section in [("tomatoes", "Produce"), ("cream", "Dairy"), ("salt", "Pantry")]:
        insert_ingredient(recipe_id=rid, name=name, qty_per_serving=1.0, unit=None,
                          todoist_section=section, path=db_path)
    _make_env(monkeypatch)
    captured = _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid, 4]])
    result(blocking=True, timeout=5)

    assert len(captured) == 4

    meals_calls = [c for c in captured if c["output_config"]["section_id"] == _MEALS_SECTION_ID]
    assert len(meals_calls) == 1
    header = meals_calls[0]
    assert header["output_config"]["labels"] == ["meal-planner"]
    assert "Tomato Soup" in header["payload"]["title"]
    assert "servings" in header["payload"]["title"]
    assert header["payload"]["source_id"] == f"recipes:{rid}:header"


def test_send_two_recipes_emits_two_headers(monkeypatch, tmp_path: Path) -> None:
    """Two recipes → exactly 2 header calls in Meals section."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    init_db(db_path)
    rid_a = insert_recipe(title="Pasta X", base_servings=4, path=db_path)
    insert_ingredient(recipe_id=rid_a, name="pasta", qty_per_serving=100.0,
                      unit="g", todoist_section="Pantry", path=db_path)
    rid_b = insert_recipe(title="Soup Y", base_servings=4, path=db_path)
    insert_ingredient(recipe_id=rid_b, name="broth", qty_per_serving=250.0,
                      unit="ml", todoist_section="Pantry", path=db_path)
    _make_env(monkeypatch)
    captured = _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid_a, 2], [rid_b, 4]])
    result(blocking=True, timeout=5)

    meals_calls = [c for c in captured if c["output_config"]["section_id"] == _MEALS_SECTION_ID]
    assert len(meals_calls) == 2
    header_titles = {c["payload"]["title"] for c in meals_calls}
    assert any("Pasta X" in t for t in header_titles)
    assert any("Soup Y" in t for t in header_titles)


def test_send_header_uses_target_servings_in_title(monkeypatch, tmp_path: Path) -> None:
    """Recipe with base_servings=4 sent at target=8 → header title contains '8 servings', not '4'."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    init_db(db_path)
    rid = insert_recipe(title="Big Batch Chili", base_servings=4, path=db_path)
    insert_ingredient(recipe_id=rid, name="beans", qty_per_serving=0.5,
                      unit="cup", todoist_section="Pantry", path=db_path)
    _make_env(monkeypatch)
    captured = _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid, 8]])
    result(blocking=True, timeout=5)

    header = next(c for c in captured if c["output_config"]["section_id"] == _MEALS_SECTION_ID)
    assert "(8 servings)" in header["payload"]["title"]
    assert "(4 servings)" not in header["payload"]["title"]


def test_send_missing_meals_section_raises_runtimeerror(monkeypatch, tmp_path: Path) -> None:
    """TODOIST_SECTIONS without 'Meals' → RuntimeError; no ingredient tasks emitted."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    rid = _setup_db_one_recipe(db_path)
    sections_no_meals = {"Produce": "sec-prod", "Pantry": "sec-pantry"}
    _make_env(monkeypatch, sections_json=json.dumps(sections_no_meals))
    captured = _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    from console.tabs._job_status import _format_status, _read_result_or_synthesize_error
    from jobs import huey as _huey_mod

    task_result = meal_planner_send_to_todoist([[rid, 4]])
    synthesized = _read_result_or_synthesize_error(
        lambda tid: _huey_mod.result(tid, blocking=False), task_result.id
    )
    assert synthesized is not None
    level, msg = _format_status(synthesized)
    assert level == "error"
    assert "Meals" in msg

    # validation runs before the recipe loop — no ingredient tasks emitted
    assert len(captured) == 0


def test_send_header_counts_toward_items_sent_and_attempted(monkeypatch, tmp_path: Path) -> None:
    """1 recipe, 5 ingredients, all create succeed → items_sent=6, items_attempted=6."""
    import meal_planner.db as _db_mod
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    init_db(db_path)
    rid = insert_recipe(title="Full Dinner", base_servings=4, path=db_path)
    for i in range(5):
        insert_ingredient(recipe_id=rid, name=f"ingredient_{i}", qty_per_serving=1.0,
                          unit=None, todoist_section="Pantry", path=db_path)
    _make_env(monkeypatch)
    _adapter_mock(monkeypatch)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid, 4]])
    out = result(blocking=True, timeout=5)

    assert out["items_sent"] == 6
    assert out["items_attempted"] == 6


def test_send_header_create_failure_does_not_block_ingredients(monkeypatch, tmp_path: Path) -> None:
    """Header create_task returns {'created': False}; ingredients succeed → attempted=6, sent=5."""
    import meal_planner.db as _db_mod
    import meal_planner.todoist_client as _todoist_adapter
    db_path = tmp_path / "recipes.db"
    monkeypatch.setattr(_db_mod, "DB_PATH", db_path)
    init_db(db_path)
    rid = insert_recipe(title="Partial Recipe", base_servings=4, path=db_path)
    for i in range(5):
        insert_ingredient(recipe_id=rid, name=f"item_{i}", qty_per_serving=1.0,
                          unit=None, todoist_section="Pantry", path=db_path)
    _make_env(monkeypatch)

    call_count = [0]

    def fake_create_task(output_config: dict, payload: dict) -> dict:
        call_count[0] += 1
        # First call is the header (section_id == Meals) → simulate create failure
        if output_config.get("section_id") == _MEALS_SECTION_ID:
            return {"created": False}
        return {"created": True}

    monkeypatch.setattr(_todoist_adapter, "create_task", fake_create_task)

    from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
    result = meal_planner_send_to_todoist([[rid, 4]])
    out = result(blocking=True, timeout=5)

    assert out["items_attempted"] == 6
    assert out["items_sent"] == 5  # header failed, 5 ingredients succeeded
    assert call_count[0] == 6  # all 6 calls were made
