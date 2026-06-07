"""Regression tests: the ingredient diff must be idempotent across a pandas round-trip.

Root cause (2026-06-06): pandas converts Python None to float('nan') for any
mixed numeric column (e.g. qty_per_serving when some rows have a quantity and
some do not; todoist_section when some rows have a section and some do not).
_rows_from_editor previously only NaN-normalised the `id` column, so
diff_ingredients saw None != nan as a permanent phantom "update" that kept
the autosave st.rerun() firing every render — an infinite rerun loop.

This file covers:
  - Unit tests on diff_ingredients directly (None vs nan → no phantom update)
  - Integration tests via _render_edit_panel on a mixed-qty recipe (the live
    trigger) and a mixed-section recipe (the latent trigger), asserting that
    opening the editor without touching anything does not call st.rerun().
"""
from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from console.tabs._recipe_form import diff_ingredients
from meal_planner import db as _db
from meal_planner.db import init_db, insert_ingredient, insert_recipe
from meal_planner import queries


# ---------------------------------------------------------------------------
# Unit tests on diff_ingredients itself
# ---------------------------------------------------------------------------

def test_diff_none_vs_nan_qty_not_a_phantom_update():
    """None qty_per_serving and float('nan') qty_per_serving must compare equal."""
    before = [{"id": 1, "name": "salt", "qty_per_serving": None, "unit": "", "notes": "",
               "todoist_section": None, "sort_order": 1}]
    # Simulate the pandas round-trip: None → NaN in a mixed numeric column.
    after = [{"id": 1, "name": "salt", "qty_per_serving": float("nan"), "unit": "", "notes": "",
              "todoist_section": None, "sort_order": 1}]
    result = diff_ingredients(before, after)
    assert result["updates"] == [], "None vs nan should not be a phantom update"
    assert result["deletes"] == []
    assert result["adds"] == []


def test_diff_none_vs_nan_section_not_a_phantom_update():
    """None todoist_section and float('nan') must compare equal."""
    before = [{"id": 2, "name": "flour", "qty_per_serving": 2.0, "unit": "cup", "notes": "",
               "todoist_section": None, "sort_order": 1}]
    after  = [{"id": 2, "name": "flour", "qty_per_serving": 2.0, "unit": "cup", "notes": "",
               "todoist_section": float("nan"), "sort_order": 1}]
    result = diff_ingredients(before, after)
    assert result["updates"] == [], "None vs nan in section should not be a phantom update"


def test_diff_mixed_recipe_pandas_roundtrip_is_clean():
    """Full pandas round-trip on a mixed-qty recipe produces an empty diff."""
    before = [
        {"id": 1, "name": "flour", "qty_per_serving": 2.0, "unit": "cup",
         "notes": "", "todoist_section": "Baking", "sort_order": 1},
        {"id": 2, "name": "salt",  "qty_per_serving": None, "unit": "",
         "notes": "", "todoist_section": None, "sort_order": 2},
        {"id": 3, "name": "eggs",  "qty_per_serving": 3.0, "unit": "",
         "notes": "", "todoist_section": None, "sort_order": 3},
    ]
    # Replicate _rows_from_editor: pandas round-trip then nan_to_none every cell.
    from console.tabs._recipe_form import nan_to_none
    raw = pd.DataFrame(before).to_dict("records")
    after = []
    for row in raw:
        norm = {k: nan_to_none(v) for k, v in row.items()}
        if norm.get("id") is None:
            norm["id"] = 0
        after.append(norm)
    result = diff_ingredients(before, after)
    assert result == {"adds": [], "updates": [], "deletes": []}, (
        "Pandas round-trip of a mixed-qty recipe should produce an empty diff"
    )


def test_diff_real_change_still_detected():
    """A genuine edit is still reported as an update after the NaN fix."""
    before = [{"id": 1, "name": "sugar", "qty_per_serving": None, "unit": "",
               "notes": "", "todoist_section": None, "sort_order": 1}]
    after  = [{"id": 1, "name": "sugar", "qty_per_serving": 0.5, "unit": "cup",
               "notes": "", "todoist_section": None, "sort_order": 1}]
    result = diff_ingredients(before, after)
    assert len(result["updates"]) == 1, "A real edit must still be detected"


# ---------------------------------------------------------------------------
# Helpers shared by the integration tests below
# ---------------------------------------------------------------------------

class _RerunStop(Exception):
    pass


class _Col:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeColumnConfig:
    def __getattr__(self, _): return lambda *a, **k: None


class _FakeSt:
    """Minimal Streamlit stand-in for driving _render_edit_panel once."""

    def __init__(self, widget_values: dict, editor_return: pd.DataFrame):
        self.session_state: dict = {}
        self.widget_values = widget_values
        self.editor_return = editor_return
        self.rerun_called = False
        self.column_config = _FakeColumnConfig()

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

    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Col() for _ in range(n)]

    def subheader(self, *a, **k): pass
    def markdown(self, *a, **k): pass
    def caption(self, *a, **k): pass
    def success(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass

    def button(self, *a, **k):
        return False

    def rerun(self):
        self.rerun_called = True
        raise _RerunStop


def _scalar_widget_values(recipe_id: int, title: str = "Soup") -> dict:
    return {
        f"edit_title_{recipe_id}": title,
        f"edit_servings_{recipe_id}": 4,
        f"edit_instructions_{recipe_id}": "",
        f"edit_cook_time_{recipe_id}": 0,
        f"edit_source_{recipe_id}": "",
        f"edit_recipe_book_{recipe_id}": "",
        f"edit_tags_{recipe_id}": [],
        f"edit_new_tag_{recipe_id}": "",
    }


# ---------------------------------------------------------------------------
# Integration: opening a mixed-qty recipe without editing must NOT rerun
# ---------------------------------------------------------------------------

@pytest.fixture
def mixed_qty_db(tmp_path: Path, monkeypatch) -> tuple[Path, int]:
    """A recipe with one quantified and one blank-qty ingredient."""
    p = tmp_path / "recipes.db"
    init_db(p)
    monkeypatch.setattr(_db, "DB_PATH", p)
    rid = insert_recipe(title="Soup", base_servings=4, path=p)
    insert_ingredient(recipe_id=rid, name="flour", qty_per_serving=2.0,
                      unit="cup", sort_order=1, path=p)
    insert_ingredient(recipe_id=rid, name="salt",  qty_per_serving=None,
                      unit="", sort_order=2, path=p)
    return p, rid


def test_mixed_qty_recipe_no_rerun_on_open(mixed_qty_db, monkeypatch):
    """Opening a mixed-qty recipe in the editor must not trigger st.rerun().

    Pre-fix: the pandas round-trip turned None qty → NaN, diff saw a phantom
    "update", persisted, popped the grid key, and called st.rerun() every render.
    Post-fix: the diff is idempotent, no rerun is requested.
    """
    from console.tabs import plan

    p, rid = mixed_qty_db
    ingr = queries.list_ingredients(rid, path=p)  # [flour, salt]

    # The editor returns EXACTLY what ingredients_to_rows would give — the
    # DB values unchanged, which is what happens when the user hasn't touched
    # anything yet.
    from console.tabs._recipe_form import ingredients_to_rows
    rows = ingredients_to_rows(ingr)
    editor_return = pd.DataFrame(rows)

    fake = _FakeSt(_scalar_widget_values(rid), editor_return)
    monkeypatch.setattr(plan, "st", fake)

    # Must complete without raising _RerunStop.
    plan._render_edit_panel(rid)

    assert fake.rerun_called is False, (
        "Opening a mixed-qty recipe must not trigger st.rerun() — "
        "the autosave diff must be idempotent"
    )


# ---------------------------------------------------------------------------
# Integration: opening a mixed-section recipe must NOT rerun (latent trigger)
# ---------------------------------------------------------------------------

@pytest.fixture
def mixed_section_db(tmp_path: Path, monkeypatch) -> tuple[Path, int]:
    """A recipe with one sectioned and one unsectioned ingredient."""
    p = tmp_path / "recipes.db"
    init_db(p)
    monkeypatch.setattr(_db, "DB_PATH", p)
    rid = insert_recipe(title="Salad", base_servings=2, path=p)
    insert_ingredient(recipe_id=rid, name="lettuce", qty_per_serving=1.0,
                      unit="head", todoist_section="Produce", sort_order=1, path=p)
    insert_ingredient(recipe_id=rid, name="salt", qty_per_serving=None,
                      unit="", todoist_section=None, sort_order=2, path=p)
    return p, rid


def test_mixed_section_recipe_no_rerun_on_open(mixed_section_db, monkeypatch):
    """Opening a recipe with mixed todoist_section must not trigger st.rerun()."""
    from console.tabs import plan

    p, rid = mixed_section_db
    ingr = queries.list_ingredients(rid, path=p)

    from console.tabs._recipe_form import ingredients_to_rows
    rows = ingredients_to_rows(ingr)
    editor_return = pd.DataFrame(rows)

    wv = _scalar_widget_values(rid, title="Salad")
    wv[f"edit_servings_{rid}"] = 2
    fake = _FakeSt(wv, editor_return)
    monkeypatch.setattr(plan, "st", fake)

    plan._render_edit_panel(rid)

    assert fake.rerun_called is False, (
        "Opening a mixed-section recipe must not trigger st.rerun()"
    )
