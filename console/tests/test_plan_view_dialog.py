"""Tests for the Phase 19 View dialog: format_view_block helper + session state."""
from __future__ import annotations

from unittest.mock import patch

from console.tabs._recipe_form import format_view_block
from meal_planner.models import Ingredient, Recipe


def _make_recipe(
    *,
    instructions: str | None = "1. preheat\n2. bake",
    cook_time_min: int | None = 30,
    source: str | None = "nas-intake",
    base_servings: int = 4,
    recipe_book: str | None = None,
) -> Recipe:
    return Recipe(
        id=1,
        title="Lemon Orzo",
        base_servings=base_servings,
        instructions=instructions,
        cook_time_min=cook_time_min,
        source=source,
        photo_path=None,
        recipe_book=recipe_book,
        created_at="2026-05-30",
        updated_at="2026-05-30",
    )


def _make_ingredient(
    *, name: str, qty_per_serving: float | None = 0.25, unit: str | None = "cup",
) -> Ingredient:
    return Ingredient(
        id=1, recipe_id=1, name=name, qty_per_serving=qty_per_serving, unit=unit,
        notes=None, todoist_section=None, sort_order=0,
    )


# ---------------------------------------------------------------------------
# format_view_block — basic rendering
# ---------------------------------------------------------------------------


def test_format_view_block_includes_title() -> None:
    out = format_view_block(_make_recipe(), [], [])
    assert "## Lemon Orzo" in out


def test_format_view_block_renders_instructions_verbatim() -> None:
    out = format_view_block(_make_recipe(), [], [])
    assert "1. preheat\n2. bake" in out
    assert "### Instructions" in out


def test_format_view_block_placeholder_when_instructions_none() -> None:
    recipe = _make_recipe(instructions=None)
    out = format_view_block(recipe, [], [])
    assert "_No instructions saved._" in out
    # The verbatim placeholder lets the user know nothing was extracted
    assert "1. preheat" not in out


def test_format_view_block_placeholder_when_instructions_empty_string() -> None:
    recipe = _make_recipe(instructions="")
    out = format_view_block(recipe, [], [])
    assert "_No instructions saved._" in out


def test_format_view_block_meta_line_includes_source_cook_time_servings() -> None:
    out = format_view_block(_make_recipe(), [], [])
    assert "Source: nas-intake" in out
    assert "Cook time: 30 min" in out
    assert "Base servings: 4" in out


def test_format_view_block_omits_missing_meta() -> None:
    recipe = _make_recipe(cook_time_min=None, source=None)
    out = format_view_block(recipe, [], [])
    assert "Cook time" not in out
    assert "Source" not in out
    # Base servings always shown
    assert "Base servings: 4" in out


def test_format_view_block_renders_tags_when_present() -> None:
    out = format_view_block(_make_recipe(), ["pasta", "quick"], [])
    assert "Tags: pasta, quick" in out


def test_format_view_block_omits_tags_line_when_no_tags() -> None:
    out = format_view_block(_make_recipe(), [], [])
    assert "Tags:" not in out


def test_format_view_block_includes_recipe_book_when_set() -> None:
    """Phase 19.5: recipe_book renders in the meta line as 'From: <book>'."""
    out = format_view_block(_make_recipe(recipe_book="Serious Eats"), [], [])
    assert "From: Serious Eats" in out


def test_format_view_block_omits_recipe_book_line_when_none() -> None:
    out = format_view_block(_make_recipe(recipe_book=None), [], [])
    assert "From:" not in out


# ---------------------------------------------------------------------------
# format_view_block — ingredient scaling
# ---------------------------------------------------------------------------


def test_format_view_block_scales_ingredients_by_base_servings() -> None:
    """View dialog shows ingredients at base_servings (qty_per_serving × base)."""
    recipe = _make_recipe(base_servings=4)
    ings = [_make_ingredient(name="orzo", qty_per_serving=0.25, unit="cup")]
    out = format_view_block(recipe, [], ings)
    # 0.25 × 4 = 1
    assert "- 1 cup orzo" in out


def test_format_view_block_drops_qty_when_none() -> None:
    ings = [_make_ingredient(name="salt", qty_per_serving=None, unit=None)]
    out = format_view_block(_make_recipe(), [], ings)
    assert "- salt" in out


def test_format_view_block_handles_decimal_qty_cleanly() -> None:
    """0.5 × 4 = 2 (integer), should render as '2' not '2.0'."""
    recipe = _make_recipe(base_servings=4)
    ings = [_make_ingredient(name="butter", qty_per_serving=0.5, unit="tbsp")]
    out = format_view_block(recipe, [], ings)
    assert "- 2 tbsp butter" in out
    assert "2.0" not in out


def test_format_view_block_omits_ingredients_section_when_empty() -> None:
    out = format_view_block(_make_recipe(), [], [])
    assert "**Ingredients**" not in out


# ---------------------------------------------------------------------------
# Session-state wiring
# ---------------------------------------------------------------------------


def test_view_recipe_id_is_session_state_key() -> None:
    """When the View button is clicked, plan.py sets _view_recipe_id.

    Direct module probe — the key string must match what _render_view_dialog
    reads. Catches accidental rename of the session-state key on either side.
    """
    from console.tabs import plan
    import inspect

    src = inspect.getsource(plan)
    # Both writer (button handler) and reader (render path) reference it
    assert src.count('"_view_recipe_id"') >= 2


def test_session_state_pop_clears_view_id() -> None:
    """Replicates the close-button handler: popping _view_recipe_id clears state."""
    state: dict = {"_view_recipe_id": 42}
    with patch("streamlit.session_state", state):
        import streamlit as st
        st.session_state.pop("_view_recipe_id", None)
    assert "_view_recipe_id" not in state
