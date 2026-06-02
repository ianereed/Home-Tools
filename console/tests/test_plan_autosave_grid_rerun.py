"""Regression test for the data_editor autosave revert (Anny, 2026-06-02).

st.data_editor stores its edits as a POSITION-keyed delta in session_state that
persists across reruns. The autosave path writes ingredient changes to the DB
mid-session; on the next rerun list_ingredients reorders the rows
(ORDER BY sort_order, name), so a stale position-keyed delta would re-apply at
the wrong row — reverting or duplicating the user's edit. The fix: after an
ingredient-changing autosave, drop the grid's delta key and st.rerun() so the
editor rebuilds clean from the saved rows.

This test drives the real _render_edit_panel against a temp DB with a stub `st`,
and asserts the fix's contract: the grid key is cleared and a rerun is requested
on an ingredient edit, the DB is updated, and a scalar-only edit does NOT rerun.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from meal_planner import db as _db
from meal_planner.db import init_db, insert_ingredient, insert_recipe
from meal_planner import queries


class _RerunStop(Exception):
    """Raised by the stub st.rerun() to halt the render, like Streamlit does."""


class _Col:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeColumnConfig:
    def __getattr__(self, _name):
        return lambda *a, **k: None


class _FakeSt:
    """Minimal Streamlit stand-in for driving _render_edit_panel once.

    Value-returning widgets look up their `key=` in `widget_values`, falling
    back to the passed `value=`/`default=`. data_editor returns `editor_return`.
    rerun() raises _RerunStop and records the call.
    """

    def __init__(self, widget_values: dict, editor_return: pd.DataFrame):
        self.session_state: dict = {}
        self.widget_values = widget_values
        self.editor_return = editor_return
        self.rerun_called = False
        self.column_config = _FakeColumnConfig()

    # --- value widgets ---
    def text_input(self, label, value="", key=None, **k):
        return self.widget_values.get(key, value)

    def number_input(self, label, *, value=0, key=None, **k):
        return self.widget_values.get(key, value)

    def text_area(self, label, value="", key=None, **k):
        return self.widget_values.get(key, value)

    def pills(self, label, *, options=None, default=None, key=None, **k):
        return self.widget_values.get(key, default or [])

    def data_editor(self, data, *, key=None, **k):
        return self.editor_return

    # --- layout / output no-ops ---
    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Col() for _ in range(n)]

    def subheader(self, *a, **k):
        pass

    def markdown(self, *a, **k):
        pass

    def caption(self, *a, **k):
        pass

    def success(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def button(self, *a, **k):
        return False

    def rerun(self):
        self.rerun_called = True
        raise _RerunStop


@pytest.fixture
def recipe_db(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "recipes.db"
    init_db(p)
    # Point both queries.* and plan._persist_recipe at the temp DB.
    monkeypatch.setattr(_db, "DB_PATH", p)
    rid = insert_recipe(title="Soup", base_servings=4, path=p)
    # Tied sort_order=0 -> list_ingredients orders by name -> apple, banana.
    insert_ingredient(recipe_id=rid, name="apple", qty_per_serving=1.0, sort_order=0, path=p)
    insert_ingredient(recipe_id=rid, name="banana", qty_per_serving=1.0, sort_order=0, path=p)
    return p


def _scalar_widget_values(recipe_id: int) -> dict:
    """Widget returns that match the recipe exactly (no scalar-field change)."""
    return {
        f"edit_title_{recipe_id}": "Soup",
        f"edit_servings_{recipe_id}": 4,
        f"edit_instructions_{recipe_id}": "",
        f"edit_cook_time_{recipe_id}": 0,
        f"edit_source_{recipe_id}": "",
        f"edit_recipe_book_{recipe_id}": "",
        f"edit_tags_{recipe_id}": [],
        f"edit_new_tag_{recipe_id}": "",
    }


def _editor_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_ingredient_edit_clears_grid_delta_and_reruns(recipe_db, monkeypatch):
    from console.tabs import plan

    rid = queries.list_recipes(path=recipe_db)[0].id
    ingr = queries.list_ingredients(rid, path=recipe_db)  # [apple, banana]
    apple, banana = ingr[0], ingr[1]

    # User renames the SECOND row (banana) -> zucchini. The editor returns the
    # merged frame for this run.
    editor_return = _editor_frame([
        {"id": apple.id, "name": "apple", "qty_per_serving": 1.0, "unit": "",
         "notes": "", "todoist_section": None, "sort_order": 0},
        {"id": banana.id, "name": "zucchini", "qty_per_serving": 1.0, "unit": "",
         "notes": "", "todoist_section": None, "sort_order": 0},
    ])
    fake = _FakeSt(_scalar_widget_values(rid), editor_return)
    # Simulate the data_editor having a live delta in session_state (what a real
    # cell edit leaves behind). The fix must pop this exact key.
    fake.session_state[f"edit_ingr_{rid}"] = {
        "edited_rows": {1: {"name": "zucchini"}}, "added_rows": [], "deleted_rows": [],
    }
    monkeypatch.setattr(plan, "st", fake)

    with pytest.raises(_RerunStop):
        plan._render_edit_panel(rid)

    # Contract: ingredient autosave drops the position-keyed grid delta + reruns.
    assert fake.rerun_called is True
    assert f"edit_ingr_{rid}" not in fake.session_state
    # And the rename was actually persisted.
    names = {i.name for i in queries.list_ingredients(rid, path=recipe_db)}
    assert names == {"apple", "zucchini"}


def test_scalar_only_edit_does_not_rerun(recipe_db, monkeypatch):
    """A title-only change autosaves without the grid-rerun (no stale delta)."""
    from console.tabs import plan

    rid = queries.list_recipes(path=recipe_db)[0].id
    ingr = queries.list_ingredients(rid, path=recipe_db)

    # Editor returns the ingredients UNCHANGED.
    editor_return = _editor_frame([
        {"id": i.id, "name": i.name, "qty_per_serving": 1.0, "unit": "",
         "notes": "", "todoist_section": None, "sort_order": 0}
        for i in ingr
    ])
    wv = _scalar_widget_values(rid)
    wv[f"edit_title_{rid}"] = "Soup Deluxe"  # the only change
    fake = _FakeSt(wv, editor_return)
    monkeypatch.setattr(plan, "st", fake)

    # Must complete without raising _RerunStop (no grid rerun on scalar edits).
    plan._render_edit_panel(rid)

    assert fake.rerun_called is False
    assert queries.get_recipe(rid, path=recipe_db).title == "Soup Deluxe"
