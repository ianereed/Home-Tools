"""Tests for meal_planner.vision._normalize — TDD for Option B normalizer."""
from __future__ import annotations

import pytest

from meal_planner.vision._normalize import (
    normalize_extraction,
    normalize_ingredient,
    normalize_instructions,
)


# ---------------------------------------------------------------------------
# Pattern 1: qty/unit fused (qty contains "number unit", unit is null/empty)
# ---------------------------------------------------------------------------

def test_p1_bare_digit():
    ing = {"qty": "1 teaspoon", "unit": None, "name": "olive oil"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "1"
    assert out["unit"] == "teaspoon"
    assert out["name"] == "olive oil"
    assert len(warns) == 1


def test_p1_fraction():
    ing = {"qty": "1/2 pound", "unit": None, "name": "orzo"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "1/2"
    assert out["unit"] == "pound"
    assert len(warns) == 1


def test_p1_mixed():
    ing = {"qty": "1 1/2 cups", "unit": None, "name": "sugar"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "1 1/2"
    assert out["unit"] == "cups"
    assert len(warns) == 1


def test_p1_range():
    ing = {"qty": "5-6 cloves", "unit": None, "name": "garlic"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "5-6"
    assert out["unit"] == "cloves"
    assert len(warns) == 1


def test_p1_decimal():
    ing = {"qty": "2.5 oz", "unit": None, "name": "butter"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "2.5"
    assert out["unit"] == "oz"
    assert len(warns) == 1


# ---------------------------------------------------------------------------
# Pattern 2: unit-in-name (name starts with a unit token)
# ---------------------------------------------------------------------------

def test_p2_singular():
    ing = {"qty": "1", "unit": None, "name": "teaspoon turmeric"}
    out, warns = normalize_ingredient(ing)
    assert out["unit"] == "teaspoon"
    assert out["name"] == "turmeric"
    assert len(warns) == 1


def test_p2_plural():
    ing = {"qty": "5-6", "unit": None, "name": "cloves Garlic"}
    out, warns = normalize_ingredient(ing)
    assert out["unit"] == "cloves"
    assert out["name"] == "Garlic"
    assert len(warns) == 1


def test_p2_capitalized():
    ing = {"qty": "1", "unit": None, "name": "Teaspoon Turmeric"}
    out, warns = normalize_ingredient(ing)
    assert out["unit"] == "Teaspoon"
    assert out["name"] == "Turmeric"
    assert len(warns) == 1


def test_p2_abbreviated():
    ing = {"qty": "2", "unit": None, "name": "tsp salt"}
    out, warns = normalize_ingredient(ing)
    assert out["unit"] == "tsp"
    assert out["name"] == "salt"
    assert len(warns) == 1


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

def test_noop_unit_already_set():
    ing = {"qty": "1", "unit": "cup", "name": "flour"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


def test_noop_qty_null():
    ing = {"qty": None, "unit": None, "name": "salt to taste"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


def test_noop_name_has_no_unit():
    """'large' is intentionally NOT in the unit vocab."""
    ing = {"qty": "1", "unit": None, "name": "large eggs"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


def test_noop_single_word_name():
    ing = {"qty": "1", "unit": None, "name": "egg"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


# ---------------------------------------------------------------------------
# Edge-case behavior
# ---------------------------------------------------------------------------

def test_no_double_fire_p1_then_p2():
    """Pattern 1 fires → name is not re-checked for Pattern 2."""
    ing = {"qty": "1 teaspoon", "unit": None, "name": "olive oil"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "1"
    assert out["unit"] == "teaspoon"
    assert out["name"] == "olive oil"
    assert len(warns) == 1, f"Expected 1 warning, got {warns}"


def test_p2_of_pattern_is_noop():
    """'X of Y' is descriptive (e.g., 'cup of milk', 'slice of bread') — no fire."""
    ing = {"qty": "1", "unit": None, "name": "cup of cup-sized portions"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


# ---------------------------------------------------------------------------
# Pattern 3: qty/unit fused + unit field has non-unit garbage
# ---------------------------------------------------------------------------

def test_p3_fused_with_ingredient_in_unit():
    """qty='2 tsp', unit='vegetable oil' — unit is not a real measurement."""
    ing = {"qty": "2 tsp", "unit": "vegetable oil", "name": "vegetable oil"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "2"
    assert out["unit"] == "tsp"
    assert out["name"] == "vegetable oil"
    assert len(warns) == 1


def test_p3_fraction_with_prep_in_unit():
    """qty='1/2 cup', unit='sour cream' — unit is ingredient text."""
    ing = {"qty": "1/2 cup", "unit": "sour cream", "name": "sour cream"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "1/2"
    assert out["unit"] == "cup"
    assert out["name"] == "sour cream"
    assert len(warns) == 1


def test_p3_noop_when_unit_is_real():
    """qty='2 cups', unit='cup' — unit is already a real unit, no change."""
    ing = {"qty": "2 cups", "unit": "cup", "name": "flour"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


# ---------------------------------------------------------------------------
# normalize_extraction over a full dict
# ---------------------------------------------------------------------------

_ORZO_SIDECAR = {
    "title": "Easy Sausage and Pea Orzo Risotto",
    "ingredients": [
        {"qty": "1 teaspoon", "unit": None, "name": "olive oil"},
        {"qty": "10 ounce", "unit": None, "name": "Italian sausage, removed from its casing"},
        {"qty": "1/4 cup", "unit": None, "name": "minced shallot"},
        {"qty": "1/2 pound", "unit": None, "name": "orzo"},
        {"qty": "3 cup", "unit": None, "name": "hot water or low-sodium vegetable or chicken stock"},
        {"qty": None, "unit": None, "name": "kosher salt"},
        {"qty": None, "unit": None, "name": "freshly ground black pepper"},
        {"qty": "1 cup", "unit": None, "name": "frozen peas"},
        {"qty": "1/4 cup", "unit": None, "name": "finely grated Parmigiano-Reggiano"},
        {"qty": None, "unit": None, "name": "chopped flat-leaf parsley (optional)"},
    ],
    "tags": ["italian", "pasta", "weeknight"],
}


def test_normalize_extraction_orzo():
    """7 fused ingredients normalized; 3 empty-qty pass through. 7 warnings."""
    result, warns = normalize_extraction(_ORZO_SIDECAR)
    assert len(warns) == 7, f"Expected 7 warnings, got {len(warns)}: {warns}"
    # All 7 non-null qty ingredients should now have a unit set
    ings = result["ingredients"]
    for ing in ings:
        if ing["qty"] not in (None, ""):
            assert ing["unit"] not in (None, ""), f"Unit missing after normalize: {ing}"
    # Title and tags pass through
    assert result["title"] == _ORZO_SIDECAR["title"]
    assert result["tags"] == _ORZO_SIDECAR["tags"]
    # Input not mutated
    assert _ORZO_SIDECAR["ingredients"][0]["unit"] is None


# ---------------------------------------------------------------------------
# Multi-token units (Pattern 1, H1 from Opus review)
# ---------------------------------------------------------------------------

def test_p1_two_token_unit_fl_oz():
    """qty='8 fl oz' should split into qty='8', unit='fl oz'."""
    ing = {"qty": "8 fl oz", "unit": None, "name": "milk"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "8"
    assert out["unit"] == "fl oz"
    assert out["name"] == "milk"
    assert len(warns) == 1


def test_p1_two_token_unit_fluid_ounce():
    ing = {"qty": "12 fluid ounces", "unit": None, "name": "milk"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "12"
    assert out["unit"] == "fluid ounces"


def test_p2_two_token_unit_in_name():
    ing = {"qty": "8", "unit": None, "name": "fl oz milk"}
    out, warns = normalize_ingredient(ing)
    assert out["unit"] == "fl oz"
    assert out["name"] == "milk"


# ---------------------------------------------------------------------------
# Pattern 2 over-fire guards (H2 from Opus review)
# ---------------------------------------------------------------------------

def test_p2_noop_slice_of_bread():
    """name='slice of bread' is descriptive, not a measurement — Pattern 2 must NOT fire."""
    ing = {"qty": "1", "unit": None, "name": "slice of bread"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


def test_p2_noop_package_of_cheese():
    ing = {"qty": "1", "unit": None, "name": "package of cream cheese"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


def test_p2_noop_single_word_unit_as_name():
    """name is just 'oz' — Pattern 2 would empty the name. Must NOT fire."""
    ing = {"qty": "1", "unit": None, "name": "oz"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


def test_p2_still_fires_on_real_unit_name_pair():
    """Sanity: legitimate 'cloves Garlic' still works after over-fire guards."""
    ing = {"qty": "5-6", "unit": None, "name": "cloves Garlic"}
    out, warns = normalize_ingredient(ing)
    assert out["unit"] == "cloves"
    assert out["name"] == "Garlic"


# ---------------------------------------------------------------------------
# Robustness: non-string qty, missing keys, bare mixed fraction (Q3 fragility)
# ---------------------------------------------------------------------------

def test_qty_int_passes_through():
    ing = {"qty": 1, "unit": None, "name": "egg"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


def test_ingredient_missing_keys():
    """Missing qty/unit keys entirely — must not crash."""
    out, warns = normalize_ingredient({"name": "salt"})
    assert out == {"name": "salt"}
    assert warns == []


def test_bare_mixed_fraction_no_unit_is_noop():
    """qty='1 1/2' alone, name has no unit token — must remain unchanged."""
    ing = {"qty": "1 1/2", "unit": None, "name": "pepper"}
    out, warns = normalize_ingredient(ing)
    assert out == ing
    assert warns == []


def test_idempotent_double_normalize():
    """Calling normalize_ingredient twice on already-normalized input is a no-op."""
    once, w1 = normalize_ingredient({"qty": "1 teaspoon", "unit": None, "name": "salt"})
    twice, w2 = normalize_ingredient(once)
    assert twice == once
    assert w2 == []


# ---------------------------------------------------------------------------
# Pattern 3: discarded-content warning (M1 from Opus review)
# ---------------------------------------------------------------------------

def test_p3_emits_discarded_warning_for_meaningful_unit():
    """unit='large cloves, minced' contains real prep info — must surface a discarded warning."""
    ing = {"qty": "2 cloves", "unit": "large cloves, minced", "name": "garlic"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "2"
    assert out["unit"] == "cloves"
    # First warning is the split; second flags discarded unit content
    assert len(warns) == 2
    assert any("discarded" in w for w in warns), warns


def test_p3_no_discarded_warning_for_redundant_unit():
    """unit='vegetable oil' duplicates the name — no extra warning needed."""
    ing = {"qty": "2 tsp", "unit": "vegetable oil", "name": "vegetable oil"}
    out, warns = normalize_ingredient(ing)
    assert out["qty"] == "2"
    assert out["unit"] == "tsp"
    assert len(warns) == 1


# ---------------------------------------------------------------------------
# Phase 19 polish: normalize_instructions — inline-numbered → \n-separated
# ---------------------------------------------------------------------------


def test_normalize_instructions_splits_inline_numbered_steps():
    """Model often returns '1. step. 2. step. 3. step.' as a single line."""
    inp = "1. Mix together and pour over chicken. 2. Massage and leave in fridge for 24 hours. 3. Cover with foil and bake for 35-40 minutes at 425F."
    out = normalize_instructions(inp)
    assert out == (
        "1. Mix together and pour over chicken.\n"
        "2. Massage and leave in fridge for 24 hours.\n"
        "3. Cover with foil and bake for 35-40 minutes at 425F."
    )


def test_normalize_instructions_idempotent_on_newline_separated():
    """Already-newline-separated input is unchanged."""
    inp = "1. Preheat oven.\n2. Mix dry ingredients.\n3. Bake."
    assert normalize_instructions(inp) == inp


def test_normalize_instructions_passes_through_none_and_empty():
    assert normalize_instructions(None) is None
    assert normalize_instructions("") == ""


def test_normalize_instructions_no_false_positive_on_decimal_in_text():
    """'35-40 minutes at 425F' has no period-then-digit-dot — no false split."""
    inp = "1. Bake at 425F for 35-40 minutes. 2. Cool."
    out = normalize_instructions(inp)
    assert out == "1. Bake at 425F for 35-40 minutes.\n2. Cool."


def test_normalize_instructions_no_false_positive_on_fractional_ingredient():
    """Sentences containing fractions like '1.5 cups' must not be split.

    The lookbehind requires the preceding period to NOT be inside a number
    (since 1.5 has '.' followed by digit, not space+digit-period).
    """
    inp = "1. Add 1.5 cups of flour. 2. Stir well."
    out = normalize_instructions(inp)
    # Should split at "flour. 2." but NOT at "1.5"
    assert out == "1. Add 1.5 cups of flour.\n2. Stir well."


def test_normalize_instructions_handles_unnumbered_prose():
    """Plain prose (no numbered steps) passes through unchanged."""
    inp = "Mix everything together and bake until golden."
    assert normalize_instructions(inp) == inp


def test_normalize_extraction_applies_instructions_normalizer():
    """The wrapper used by call_ollama_vision routes instructions through the splitter."""
    parsed = {
        "title": "Bake",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
        "instructions": "1. Mix. 2. Bake. 3. Eat.",
    }
    out, _ = normalize_extraction(parsed)
    assert out["instructions"] == "1. Mix.\n2. Bake.\n3. Eat."


def test_normalize_extraction_preserves_missing_instructions_key():
    """No 'instructions' key in input → none added in output (backward compat)."""
    parsed = {
        "title": "Bake",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
    }
    out, _ = normalize_extraction(parsed)
    assert "instructions" not in out


def test_normalize_extraction_preserves_null_instructions():
    """instructions=None passes through unchanged."""
    parsed = {
        "title": "Bake",
        "ingredients": [{"qty": "1", "unit": "cup", "name": "flour"}],
        "tags": [],
        "instructions": None,
    }
    out, _ = normalize_extraction(parsed)
    assert out["instructions"] is None
