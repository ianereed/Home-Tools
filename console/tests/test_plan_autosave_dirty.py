"""Tests for the autosave dirty-gate in console/tabs/plan.py.

The edit panel writes when the form's normalized values (_form_values) differ
from the recipe's (_recipe_values), the tag set changed, or _ingredient_diff
has something to save. A false-negative loses work; a false-positive churns
the DB / updated_at on every rerun. The two normalizers must mirror each other
exactly so a just-saved form reads back as clean.
"""
from __future__ import annotations

from types import SimpleNamespace

from console.tabs.plan import (
    _form_values,
    _plan_ingredient_sync,
    _plan_recipe_sync,
    _recipe_values,
)


def _edit_is_dirty(recipe, current_tags, payload, tags, before_rows, after_rows) -> bool:
    """Whether the panel's autosave would write, for a panel just opened.

    Runs the real merge functions with the snapshot equal to the DB (no other
    session involved), so only this session's edits count.
    """
    snap = {"fields": _recipe_values(recipe), "tags": sorted(current_tags)}
    rec = _plan_recipe_sync(snap, recipe, current_tags, _form_values(payload), tags)
    ingr = _plan_ingredient_sync(before_rows, before_rows, after_rows)
    return bool(rec["writes"] or rec["tags"] is not None or any(ingr["diff"].values()))


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


# Tags merge one tag at a time against the snapshot (PR #59 review).

def _snap(**over):
    r = _recipe(**over)
    return r, {"fields": _recipe_values(r), "tags": ["dinner"]}


def test_tag_merge_keeps_both_sessions_additions():
    r, snap = _snap()
    rec = _plan_recipe_sync(snap, r, ["dinner", "vegan"], _form_values(_payload()), ["dinner", "spicy"])
    assert rec["tags"] == ["dinner", "spicy", "vegan"]
    assert rec["conflicts"] == []


def test_tag_merge_applies_removal_on_top_of_their_addition():
    r, snap = _snap()
    rec = _plan_recipe_sync(snap, r, ["dinner", "lunch"], _form_values(_payload()), [])
    assert rec["tags"] == ["lunch"]


def test_tag_merge_unchanged_here_writes_nothing():
    r, snap = _snap()
    rec = _plan_recipe_sync(snap, r, ["dinner", "lunch"], _form_values(_payload()), ["dinner"])
    assert rec["tags"] is None
    assert rec["final_tags"] == ["dinner", "lunch"]
    assert rec["theirs_tags"] is True
