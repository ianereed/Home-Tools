"""Tests for the autosave dirty-gate in console/tabs/plan.py.

_edit_is_dirty decides whether an edit-panel rerun should issue an autosave
write. A false-negative loses work; a false-positive churns the DB / updated_at
on every rerun. Field normalization here must mirror _persist_recipe exactly so
a just-saved form reads back as clean.
"""
from __future__ import annotations

from types import SimpleNamespace

from console.tabs.plan import _edit_is_dirty, _rows_from_editor


def _recipe(**over):
    base = dict(
        title="Soup",
        base_servings=4,
        instructions="Boil it",
        cook_time_min=30,
        source="NYT",
        recipe_book="Family",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _payload(**over):
    base = dict(
        title="Soup",
        base_servings=4,
        instructions="Boil it",
        cook_time_min=30,
        source="NYT",
        recipe_book="Family",
    )
    base.update(over)
    return base


def _rows(*names):
    return [
        {"id": i + 1, "name": n, "qty_per_serving": 1.0, "unit": "cup",
         "notes": None, "todoist_section": None, "sort_order": i}
        for i, n in enumerate(names)
    ]


def test_clean_form_is_not_dirty():
    r = _recipe()
    rows = _rows("carrot", "onion")
    assert _edit_is_dirty(r, ["dinner"], _payload(), ["dinner"], rows, rows) is False


def test_title_change_is_dirty():
    r = _recipe()
    assert _edit_is_dirty(r, [], _payload(title="Stew"), [], [], []) is True


def test_tag_change_is_dirty():
    r = _recipe()
    assert _edit_is_dirty(r, ["dinner"], _payload(), ["dinner", "soup"], [], []) is True


def test_tag_reorder_is_not_dirty():
    """Tags compare as sets — reordering the same tags is not a change."""
    r = _recipe()
    assert _edit_is_dirty(r, ["a", "b"], _payload(), ["b", "a"], [], []) is False


def test_ingredient_edit_is_dirty():
    r = _recipe()
    before = _rows("carrot")
    after = _rows("celery")  # same id, different name
    assert _edit_is_dirty(r, [], _payload(), [], before, after) is True


def test_ingredient_add_is_dirty():
    r = _recipe()
    before = _rows("carrot")
    after = before + [{"id": 0, "name": "leek", "qty_per_serving": 1.0,
                       "unit": "ea", "notes": None, "todoist_section": None,
                       "sort_order": 1}]
    assert _edit_is_dirty(r, [], _payload(), [], before, after) is True


def test_blank_new_row_is_not_dirty():
    """A trailing empty data_editor row (no name) must not trigger a write."""
    r = _recipe()
    before = _rows("carrot")
    after = before + [{"id": 0, "name": "", "qty_per_serving": None,
                       "unit": None, "notes": None, "todoist_section": None,
                       "sort_order": None}]
    assert _edit_is_dirty(r, [], _payload(), [], before, after) is False


def test_none_cook_time_matches_zero_widget():
    """Recipe with NULL cook_time vs the number_input's 0 default is clean."""
    r = _recipe(cook_time_min=None)
    assert _edit_is_dirty(r, [], _payload(cook_time_min=0), [], [], []) is False


def test_none_instructions_matches_empty_string():
    """Recipe with NULL instructions vs the empty text_area is clean."""
    r = _recipe(instructions=None)
    assert _edit_is_dirty(r, [], _payload(instructions=""), [], [], []) is False


def test_none_source_and_recipe_book_match_empty_widgets():
    r = _recipe(source=None, recipe_book=None)
    assert _edit_is_dirty(
        r, [], _payload(source="", recipe_book="  "), [], [], []
    ) is False
