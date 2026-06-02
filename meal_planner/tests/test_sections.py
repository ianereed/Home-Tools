"""Tests for the deterministic ingredient -> Todoist section classifier."""
from __future__ import annotations

import pytest

from meal_planner.sections import (
    ASIAN,
    CANONICAL_SECTIONS,
    DAIRY,
    FROZEN,
    FRUITS_VEGGIES,
    MEATS,
    SHELF,
    classify,
)


@pytest.mark.parametrize("name,expected", [
    # straightforward
    ("large eggs", DAIRY),
    ("boneless, skinless chicken thighs", MEATS),
    ("garlic cloves", FRUITS_VEGGIES),
    ("all-purpose flour", SHELF),
    ("soy sauce", ASIAN),
    ("frozen peas", FROZEN),
    # ordering / collision cases that drove the rule order
    ("chicken broth, homemade or store-bought", SHELF),       # broth beats chicken
    ("unsalted butter (2 sticks; 225g)", DAIRY),               # butter beats salt
    ("salt (for eggs)", SHELF),                                # salt beats egg
    ("butter, cut into cubes (or coconut oil if dairy free)", DAIRY),   # butter beats coconut oil
    ("heavy cream (or full-fat coconut milk if dairy free)", DAIRY),    # cream beats coconut milk
    ("kewpie mayo (to taste)", ASIAN),                         # kewpie beats mayo
    ("old el paso chopped green chilies", SHELF),              # canned green chilies beat fresh chili
    ("ground ginger", SHELF),                                  # dried spice beats fresh ginger
    ("minced ginger", FRUITS_VEGGIES),                         # fresh ginger stays produce
    ("canned chopped tomatoes", SHELF),                        # canned beats tomato
    ("cherry or grape tomatoes", FRUITS_VEGGIES),              # fresh tomato
    ("coconut flesh", FRUITS_VEGGIES),
    ("sweetened shredded coconut", SHELF),
    ("udon noodles / rice", ASIAN),                            # udon beats noodle/rice
    ("white rice, uncooked", SHELF),
    # the leading-"frozen" qualifier must not hijack a cooking note
    ("boneless skinless chicken thighs (or breast, bone-in; if frozen add 1-2 min)", MEATS),
])
def test_classify(name, expected):
    assert classify(name) == expected


def test_always_canonical():
    """Even a nonsense ingredient returns a real Todoist section (never NULL)."""
    for n in ["", "xyzzy widget", "unicorn tears", "   "]:
        assert classify(n) in CANONICAL_SECTIONS
