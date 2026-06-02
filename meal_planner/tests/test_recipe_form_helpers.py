"""Unit tests for console/tabs/_recipe_form.py pure-fn helpers.

These tests import only from _recipe_form — no streamlit, no DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make console package importable from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from console.tabs._recipe_form import (
    clean_optional_str,
    diff_ingredients,
    ingredients_to_rows,
    nan_to_none,
    normalize_tags,
    validate_recipe_form,
)


# ---------------------------------------------------------------------------
# validate_recipe_form
# ---------------------------------------------------------------------------

def test_valid_minimal() -> None:
    ok, errs = validate_recipe_form({"title": "Soup"})
    assert ok
    assert errs == []


def test_valid_full_payload() -> None:
    ok, errs = validate_recipe_form(
        {
            "title": "Beef Stew",
            "base_servings": 4,
            "instructions": "Mix and cook.",
            "cook_time_min": 45,
            "source": "Grandma",
        }
    )
    assert ok
    assert errs == []


def test_missing_title_fails() -> None:
    ok, errs = validate_recipe_form({})
    assert not ok
    assert any("Title" in e for e in errs)


def test_empty_title_fails() -> None:
    ok, errs = validate_recipe_form({"title": "   "})
    assert not ok
    assert any("Title" in e for e in errs)


def test_non_string_title_fails() -> None:
    ok, errs = validate_recipe_form({"title": 123})
    assert not ok


def test_servings_zero_fails() -> None:
    ok, errs = validate_recipe_form({"title": "X", "base_servings": 0})
    assert not ok
    assert any("Servings" in e for e in errs)


def test_servings_negative_fails() -> None:
    ok, errs = validate_recipe_form({"title": "X", "base_servings": -1})
    assert not ok


def test_servings_non_numeric_fails() -> None:
    ok, errs = validate_recipe_form({"title": "X", "base_servings": "lots"})
    assert not ok


def test_cook_time_negative_fails() -> None:
    ok, errs = validate_recipe_form({"title": "X", "cook_time_min": -5})
    assert not ok
    assert any("Cook time" in e for e in errs)


def test_cook_time_none_passes() -> None:
    ok, errs = validate_recipe_form({"title": "X", "cook_time_min": None})
    assert ok


def test_cook_time_empty_string_passes() -> None:
    ok, errs = validate_recipe_form({"title": "X", "cook_time_min": ""})
    assert ok


def test_cook_time_non_numeric_fails() -> None:
    ok, errs = validate_recipe_form({"title": "X", "cook_time_min": "fast"})
    assert not ok


# ---------------------------------------------------------------------------
# diff_ingredients
# ---------------------------------------------------------------------------

_ING_A = {"id": 1, "name": "Flour", "qty_per_serving": 100.0, "unit": "g", "notes": "", "todoist_section": "", "sort_order": 0}
_ING_B = {"id": 2, "name": "Salt", "qty_per_serving": 5.0, "unit": "g", "notes": "", "todoist_section": "", "sort_order": 1}


def test_diff_no_change() -> None:
    result = diff_ingredients([_ING_A, _ING_B], [_ING_A, _ING_B])
    assert result == {"adds": [], "updates": [], "deletes": []}


def test_diff_add_new_row() -> None:
    new_row = {"id": None, "name": "Pepper", "qty_per_serving": 2.0, "unit": "g", "notes": "", "todoist_section": "", "sort_order": 2}
    result = diff_ingredients([_ING_A], [_ING_A, new_row])
    assert len(result["adds"]) == 1
    assert result["adds"][0]["name"] == "Pepper"
    assert result["updates"] == []
    assert result["deletes"] == []


def test_diff_add_new_row_id_zero() -> None:
    new_row = {"id": 0, "name": "Sugar", "qty_per_serving": 10.0, "unit": "g", "notes": "", "todoist_section": "", "sort_order": 3}
    result = diff_ingredients([], [new_row])
    assert len(result["adds"]) == 1
    assert result["adds"][0]["name"] == "Sugar"


def test_diff_update_existing_row() -> None:
    modified = {**_ING_A, "qty_per_serving": 200.0}
    result = diff_ingredients([_ING_A, _ING_B], [modified, _ING_B])
    assert len(result["updates"]) == 1
    assert result["updates"][0]["id"] == 1
    assert result["updates"][0]["qty_per_serving"] == 200.0
    assert result["adds"] == []
    assert result["deletes"] == []


def test_diff_delete_row() -> None:
    result = diff_ingredients([_ING_A, _ING_B], [_ING_A])
    assert result["deletes"] == [_ING_B]
    assert result["adds"] == []
    assert result["updates"] == []


def test_diff_mixed_operations() -> None:
    modified_a = {**_ING_A, "name": "Wheat Flour"}
    new_row = {"id": None, "name": "Water", "qty_per_serving": 50.0, "unit": "ml", "notes": "", "todoist_section": "", "sort_order": 5}
    result = diff_ingredients([_ING_A, _ING_B], [modified_a, new_row])
    assert len(result["adds"]) == 1
    assert len(result["updates"]) == 1
    assert result["updates"][0]["name"] == "Wheat Flour"
    assert len(result["deletes"]) == 1
    assert result["deletes"][0]["id"] == 2


def test_diff_empty_before() -> None:
    new_row = {"id": None, "name": "Yeast", "qty_per_serving": 7.0, "unit": "g", "notes": "", "todoist_section": "", "sort_order": 0}
    result = diff_ingredients([], [new_row])
    assert len(result["adds"]) == 1
    assert result["updates"] == []
    assert result["deletes"] == []


def test_diff_empty_after() -> None:
    result = diff_ingredients([_ING_A, _ING_B], [])
    assert result["adds"] == []
    assert result["updates"] == []
    assert {r["id"] for r in result["deletes"]} == {1, 2}


# ---------------------------------------------------------------------------
# normalize_tags
# ---------------------------------------------------------------------------

def test_normalize_lowercase() -> None:
    assert normalize_tags(["Asian", "ITALIAN"]) == ["asian", "italian"]


def test_normalize_strips_whitespace() -> None:
    assert normalize_tags([" soup ", "stew"]) == ["soup", "stew"]


def test_normalize_dedup_preserves_order() -> None:
    assert normalize_tags(["asian", "Asian", "soup"]) == ["asian", "soup"]


def test_normalize_empty_strings_dropped() -> None:
    assert normalize_tags(["", "  ", "soup"]) == ["soup"]


def test_normalize_empty_list() -> None:
    assert normalize_tags([]) == []


# ---------------------------------------------------------------------------
# ingredients_to_rows
# ---------------------------------------------------------------------------

def test_ingredients_to_rows() -> None:
    from meal_planner.models import Ingredient
    ing = Ingredient(
        id=7,
        recipe_id=1,
        name="Egg",
        qty_per_serving=2.0,
        unit=None,
        notes=None,
        todoist_section=None,
        sort_order=0,
    )
    rows = ingredients_to_rows([ing])
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == 7
    assert r["name"] == "Egg"
    assert r["qty_per_serving"] == 2.0
    assert r["unit"] == ""
    assert r["notes"] == ""
    # None (not "") so the SelectboxColumn grid renders a blank section without
    # erroring on an out-of-options value.
    assert r["todoist_section"] is None


# ---------------------------------------------------------------------------
# clean_optional_str
# ---------------------------------------------------------------------------

def test_clean_optional_str_passes_string_through() -> None:
    assert clean_optional_str("cups") == "cups"


def test_clean_optional_str_preserves_empty_string() -> None:
    # Critical: the form→DB boundary must let "" through so clearing a
    # text field actually clears the column.
    assert clean_optional_str("") == ""


def test_clean_optional_str_none_returns_none() -> None:
    assert clean_optional_str(None) is None


def test_clean_optional_str_non_string_returns_none() -> None:
    # data_editor can hand us NaN floats for cleared cells; coerce to None.
    assert clean_optional_str(float("nan")) is None
    assert clean_optional_str(42) is None
    assert clean_optional_str(0) is None


# ---------------------------------------------------------------------------
# nan_to_none
# ---------------------------------------------------------------------------

def test_nan_to_none_converts_nan() -> None:
    assert nan_to_none(float("nan")) is None


def test_nan_to_none_passes_none_through() -> None:
    assert nan_to_none(None) is None


def test_nan_to_none_passes_finite_floats_through() -> None:
    assert nan_to_none(0.0) == 0.0
    assert nan_to_none(2.5) == 2.5
    assert nan_to_none(-3.14) == -3.14


def test_nan_to_none_passes_ints_through() -> None:
    assert nan_to_none(5) == 5
    assert nan_to_none(0) == 0
