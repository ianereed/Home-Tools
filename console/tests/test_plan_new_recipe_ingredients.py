"""Regression tests: ingredients can be added to a brand-new recipe (Anny, 2026-10-20).

Root cause: "+ New recipe" creates a recipe with no ingredients, and the edit
panel built its grid with ``pd.DataFrame(before_rows)``. With ``before_rows ==
[]`` that frame has NO columns, so st.data_editor rendered a grid with nothing
to type into — five recipes created that day all saved with 0 ingredients.

Second bug behind it: a row added in the grid carries ``name=None`` until the
user types one, and ``row.get("name", "").strip()`` raised AttributeError.

The integration tests apply grid edits with Streamlit's own
``_apply_dataframe_edits`` to the exact frame the panel hands data_editor, so
the frame coming back matches what a real browser edit produces.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest
from streamlit.elements.lib.column_config_utils import determine_dataframe_schema
from streamlit.elements.widgets.data_editor import _apply_dataframe_edits

from console.tabs._recipe_form import INGREDIENT_COLUMNS, row_name
from console.tests.test_plan_autosave_grid_rerun import _FakeSt, _RerunStop
from meal_planner import db as _db
from meal_planner import queries
from meal_planner.db import init_db


class _GridSt(_FakeSt):
    """Fake st whose data_editor applies a browser-style edit delta to `data`."""

    def __init__(self, widget_values: dict, delta: dict):
        super().__init__(widget_values, editor_return=None)
        self.delta = delta
        self.editor_input: pd.DataFrame | None = None

    def data_editor(self, data, *, key=None, **k):
        self.editor_input = data
        out = data.copy()
        schema = determine_dataframe_schema(out, pa.Table.from_pandas(out).schema)
        _apply_dataframe_edits(out, self.delta, schema)
        return out


@pytest.fixture
def new_recipe(tmp_path: Path, monkeypatch) -> int:
    p = tmp_path / "recipes.db"
    init_db(p)
    monkeypatch.setattr(_db, "DB_PATH", p)
    return queries.create_recipe(title="New Recipe")


def _widgets(rid: int) -> dict:
    """Scalar widgets matching the stub exactly, so only the grid is dirty."""
    return {
        f"edit_title_{rid}": "New Recipe",
        f"edit_servings_{rid}": 4,
        f"edit_instructions_{rid}": "",
        f"edit_cook_time_{rid}": 0,
        f"edit_source_{rid}": "",
        f"edit_recipe_book_{rid}": "",
        f"edit_tags_{rid}": [],
        f"edit_new_tag_{rid}": "",
    }


def _delta(added_rows: list[dict]) -> dict:
    return {"edited_rows": {}, "added_rows": added_rows, "deleted_rows": []}


def test_empty_frame_keeps_ingredient_columns():
    from console.tabs import plan

    df = plan._ingredients_frame([])
    assert list(df.columns) == list(INGREDIENT_COLUMNS)
    assert str(df["qty_per_serving"].dtype) == "float64"


def test_add_ingredient_to_new_recipe(new_recipe, monkeypatch):
    from console.tabs import plan

    fake = _GridSt(_widgets(new_recipe), _delta([
        {"name": "salt", "qty_per_serving": 0.5, "unit": "tsp"},
    ]))
    monkeypatch.setattr(plan, "st", fake)

    with pytest.raises(_RerunStop):  # ingredient autosave rebuilds the grid
        plan._render_edit_panel(new_recipe)

    assert list(fake.editor_input.columns) == list(INGREDIENT_COLUMNS)
    ingr = queries.list_ingredients(new_recipe)
    assert [(i.name, i.qty_per_serving, i.unit) for i in ingr] == [("salt", 0.5, "tsp")]


def test_autosave_makes_new_recipe_non_pending(new_recipe, monkeypatch):
    """After ingredients autosave, Cancel must no longer hard-delete the recipe."""
    from console.tabs import plan

    fake = _GridSt(_widgets(new_recipe), _delta([{"name": "salt"}]))
    fake.session_state[plan._PENDING_KEY] = {new_recipe}
    monkeypatch.setattr(plan, "st", fake)

    with pytest.raises(_RerunStop):
        plan._render_edit_panel(new_recipe)

    assert new_recipe not in fake.session_state[plan._PENDING_KEY]


def test_added_row_without_name_does_not_crash(new_recipe, monkeypatch):
    """Quantity typed before name: wait for the name, don't raise or save."""
    from console.tabs import plan

    fake = _GridSt(_widgets(new_recipe), _delta([{"qty_per_serving": 2}]))
    monkeypatch.setattr(plan, "st", fake)

    plan._render_edit_panel(new_recipe)

    assert fake.rerun_called is False
    assert queries.list_ingredients(new_recipe) == []


@pytest.mark.parametrize("row, expected", [
    ({"name": "  salt "}, "salt"),
    ({"name": None}, ""),
    ({"name": float("nan")}, ""),
    ({}, ""),
])
def test_row_name(row, expected):
    assert row_name(row) == expected
