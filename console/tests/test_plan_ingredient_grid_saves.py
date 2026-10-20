"""Regression tests: ingredient-grid autosave edge cases (review, 2026-10-20).

- Clearing an existing ingredient's name sent name=None to update_ingredient,
  which hit the NOT NULL constraint and rolled back the whole autosave
  transaction — every other edit in it too — on every rerun.
- An ingredient added in the grid with no Grocery section was saved with
  none, so Send put it in whichever Todoist section is listed first instead
  of the one sections.classify() picks (the importers' default).

Grid edits go through Streamlit's own _apply_dataframe_edits (_GridSt).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from console.tests.test_plan_concurrent_edits import _BrowserSt
from meal_planner import db as _db
from meal_planner import queries
from meal_planner.db import init_db, insert_ingredient, insert_recipe
from meal_planner.sections import MEATS, SKIP_SECTION


@pytest.fixture
def soup(tmp_path: Path, monkeypatch) -> int:
    p = tmp_path / "recipes.db"
    init_db(p)
    monkeypatch.setattr(_db, "DB_PATH", p)
    rid = insert_recipe(title="Soup", base_servings=4, path=p)
    # Tied sort_order -> list_ingredients orders by name: apple, banana.
    insert_ingredient(recipe_id=rid, name="apple", qty_per_serving=1.0, sort_order=0, path=p)
    insert_ingredient(recipe_id=rid, name="banana", qty_per_serving=1.0, sort_order=0, path=p)
    return rid


@pytest.fixture
def me(monkeypatch) -> _BrowserSt:
    from console.tabs import plan

    fake = _BrowserSt()
    monkeypatch.setattr(plan, "st", fake)
    return fake


def _edits(edited_rows=None, added_rows=None) -> dict:
    return {"edited_rows": edited_rows or {}, "added_rows": added_rows or [], "deleted_rows": []}


def _render(me, rid, delta) -> bool:
    from console.tabs import plan

    return me.run(plan._render_edit_panel, rid, delta=delta)


def test_blank_name_skips_only_that_row(soup, me):
    _render(me, soup, _edits())

    # Clear apple's name; in the same commit, change banana's quantity.
    _render(me, soup, _edits({0: {"name": None}, 1: {"qty_per_serving": 3.0}}))

    by_name = {i.name: i.qty_per_serving for i in queries.list_ingredients(soup)}
    assert by_name == {"apple": 1.0, "banana": 3.0}
    assert any("blank" in w for w in me.warnings)
    assert not any("Autosave failed" in w for w in me.warnings)


def test_blank_name_alone_writes_nothing_and_does_not_loop(soup, me):
    _render(me, soup, _edits())
    before = queries.get_recipe(soup).updated_at

    assert _render(me, soup, _edits({0: {"name": "  "}})) is False

    assert queries.get_recipe(soup).updated_at == before
    assert any("blank" in w for w in me.warnings)


def test_added_ingredient_without_section_is_classified(soup, me):
    _render(me, soup, _edits())

    _render(me, soup, _edits(added_rows=[
        {"name": "chicken thigh", "qty_per_serving": 0.5, "unit": "lb"},
        {"name": "salt"},
        {"name": "lemon", "todoist_section": "Asian market"},
    ]))

    sections = {i.name: i.todoist_section for i in queries.list_ingredients(soup)}
    assert sections["chicken thigh"] == MEATS
    assert sections["salt"] == SKIP_SECTION
    assert sections["lemon"] == "Asian market"  # an explicit pick wins
