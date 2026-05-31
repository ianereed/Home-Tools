"""Post-extraction normalizer for LLM qty/unit output bugs.

Fixes three deterministic failure modes without touching the prompt:
  1. qty/unit fused — qty='1 teaspoon', unit=null  →  qty='1', unit='teaspoon'
     (also handles two-word units: qty='8 fl oz' → qty='8', unit='fl oz')
  2. unit-in-name  — qty='1', unit=null, name='teaspoon turmeric'
                  →  qty='1', unit='teaspoon', name='turmeric'
  3. qty/unit fused with non-unit garbage in unit field — qty='2 tsp',
     unit='vegetable oil' → qty='2', unit='tsp' (unit field discarded;
     name is canonical). Emits an extra "discarded" warning when the dropped
     content was distinct from the name (e.g. unit='large cloves, minced').

Pattern 2 has guards to avoid over-firing on descriptive names: it does NOT
fire when the name is a single unit-vocab token (would empty the name) or
when the second token is "of" (descriptive: "slice of bread", "cup of milk").

Pure functions; no mutation of inputs.
"""
from __future__ import annotations

import re

_UNIT_VOCAB = frozenset({
    # volume
    "tsp", "tsp.", "teaspoon", "teaspoons",
    "tbsp", "tbsp.", "tablespoon", "tablespoons",
    "cup", "cups", "c", "c.",
    "ml", "milliliter", "milliliters",
    "l", "liter", "liters", "litre", "litres",
    "fl", "floz",
    "pint", "pints", "pt",
    "quart", "quarts", "qt",
    "gallon", "gallons", "gal",
    # mass
    "oz", "oz.", "ounce", "ounces",
    "lb", "lb.", "lbs", "lbs.", "pound", "pounds",
    "g", "gram", "grams",
    "kg", "kilogram", "kilograms",
    # count-ish
    "clove", "cloves",
    "head", "heads",
    "stick", "sticks",
    "can", "cans",
    "package", "packages", "pkg", "pkgs",
    "sprig", "sprigs",
    "bunch", "bunches",
    "slice", "slices",
    "piece", "pieces",
    "fillet", "fillets",
    "sheet", "sheets",
    "pack", "packs",
    # vague-amount tokens
    "pinch", "pinches",
    "dash", "dashes",
})

# Multi-token units the LLM might emit. Checked as a longest-prefix before
# falling back to single-token vocab — keeps Pattern 1 from passing through
# fused output like '8 fl oz'.
_MULTI_TOKEN_UNITS = frozenset({
    "fl oz", "fl. oz.", "fl oz.", "fl. oz",
    "fluid ounce", "fluid ounces",
    "cubic centimeter", "cubic centimeters",
    "cubic inch", "cubic inches",
    "dry pint", "dry pints",
    "dry quart", "dry quarts",
})

# Matches: <number> <rest-of-line>   (anchored to full string)
# Number alternatives ordered longest-first to avoid partial matches:
#   mixed fraction  1 1/2
#   fraction        1/2
#   range           5-6  or  1.5-2
#   decimal/int     2.5  or  1
# `(.+)$` captures everything after the number; _extract_unit_prefix decides
# whether the tail is a recognised (multi-)unit.
_FUSED_RE = re.compile(
    r"^(\d+\s+\d+/\d+|\d+/\d+|\d+\.?\d*-\d+\.?\d*|\d+\.?\d*)\s+(.+)$"
)


def _is_unit_token(tok: str) -> bool:
    """Case-insensitive membership check against _UNIT_VOCAB."""
    return tok.lower() in _UNIT_VOCAB


def _extract_unit_prefix(tail: str) -> tuple[str, str] | None:
    """If `tail` starts with a (multi-token) unit, return (unit, rest).

    Tries the 2-token prefix first, then the 1-token prefix. Case-insensitive
    membership; preserves original casing in the returned unit.
    Returns None if no prefix matches.
    """
    tokens = tail.strip().split()
    if not tokens:
        return None
    if len(tokens) >= 2:
        two = f"{tokens[0]} {tokens[1]}"
        if two.lower() in _MULTI_TOKEN_UNITS:
            return two, " ".join(tokens[2:])
    if tokens[0].lower() in _UNIT_VOCAB:
        return tokens[0], " ".join(tokens[1:])
    return None


def normalize_ingredient(ing: dict) -> tuple[dict, list[str]]:
    """Return (normalized_dict, warnings_list). Does NOT mutate input."""
    qty = ing.get("qty")
    unit = ing.get("unit")
    name = ing.get("name", "") or ""
    warnings: list[str] = []

    if qty is None or (isinstance(qty, str) and qty.strip() == ""):
        return ing, warnings
    if not isinstance(qty, (str, int, float)):
        return ing, warnings

    qty_s = str(qty).strip()
    unit_s = str(unit).strip() if unit is not None else ""

    unit_missing = not unit_s

    if unit_missing:
        # Pattern 1: qty/unit fused. Only fire when the regex tail is exactly
        # a (multi-)unit prefix with no trailing content — otherwise the LLM
        # may have crammed name fragments into qty and we shouldn't guess.
        m = _FUSED_RE.match(qty_s)
        if m:
            num_part = m.group(1)
            tail = m.group(2)
            extracted = _extract_unit_prefix(tail)
            if extracted is not None:
                unit_str, rest = extracted
                if not rest:
                    warnings.append(
                        f"normalize: qty='{qty_s}' split → qty='{num_part}' unit='{unit_str}'"
                    )
                    return {**ing, "qty": num_part, "unit": unit_str}, warnings

        # Pattern 2: unit-in-name. Guards against over-firing:
        #   - name is a single unit-vocab word (would empty the name)
        #   - second token is 'of' (descriptive: "slice of bread")
        extracted = _extract_unit_prefix(name)
        if extracted is not None:
            unit_str, rest = extracted
            if rest:
                rest_first = rest.split(None, 1)[0].lower()
                if rest_first != "of":
                    warnings.append(
                        f"normalize: name='{name}' split → unit='{unit_str}' name='{rest}'"
                    )
                    return {**ing, "unit": unit_str, "name": rest}, warnings
    else:
        # Pattern 3: qty fused + unit has non-unit garbage (ingredient text / prep note).
        # Only applies when the current unit value is not a real cooking measurement.
        if not _is_unit_token(unit_s):
            m = _FUSED_RE.match(qty_s)
            if m:
                num_part = m.group(1)
                tail = m.group(2)
                extracted = _extract_unit_prefix(tail)
                if extracted is not None:
                    unit_str, rest = extracted
                    if not rest:
                        warnings.append(
                            f"normalize: qty='{qty_s}' unit='{unit_s}' fused+nonunit → "
                            f"qty='{num_part}' unit='{unit_str}'"
                        )
                        # Surface a separate warning when the dropped unit_s
                        # carried real content (not a duplicate of name).
                        if unit_s.strip().lower() != name.strip().lower():
                            warnings.append(
                                f"normalize: discarded unit field content: '{unit_s}'"
                            )
                        return {**ing, "qty": num_part, "unit": unit_str}, warnings

    return ing, warnings


# Phase 19 polish: model sometimes returns "1. step. 2. step. 3. step." inline
# instead of the requested "1. step\n2. step\n3. step" multi-line format.
# This regex splits on the boundary "<period><whitespace><digit+>.<space><Cap>"
# using a lookbehind on the period and a lookahead on the next-step marker so
# only the inter-step whitespace is consumed and replaced with `\n`.
#
# Idempotent: if the model already returned \n-separated steps, the matched
# whitespace IS the newline, and the substitution rewrites \n → \n (no-op).
# Won't false-positive on "1.5 cups" (no leading period; lookbehind fails)
# or "35-40 minutes at 425F. Done." (no digit-dot after the period).
_INLINE_STEP_SPLIT_RE = re.compile(r"(?<=\.)\s+(?=\d+\.\s+[A-Z])")


def normalize_instructions(text: str | None) -> str | None:
    """Re-split inline-numbered cooking steps onto separate lines.

    Returns the input unchanged when None, empty, or no inline-step pattern
    found. Pure function; no side effects.
    """
    if not text:
        return text
    return _INLINE_STEP_SPLIT_RE.sub("\n", text)


def normalize_extraction(parsed: dict) -> tuple[dict, list[str]]:
    """Apply normalize_ingredient to every entry in parsed['ingredients']
    and normalize_instructions to parsed['instructions'].

    Returns (new_parsed, all_warnings). Passes title and tags through unchanged.
    If 'ingredients' is missing or not a list, returns (parsed, []) unchanged.
    """
    ings = parsed.get("ingredients")
    if not isinstance(ings, list):
        return parsed, []

    all_warnings: list[str] = []
    normalized: list = []
    for i, ing in enumerate(ings):
        # Schema-invalid retries can have non-dict items; pass them through untouched.
        if not isinstance(ing, dict):
            normalized.append(ing)
            continue
        norm_ing, w = normalize_ingredient(ing)
        normalized.append(norm_ing)
        for warning in w:
            all_warnings.append(f"row {i}: {warning}")

    new_parsed = {**parsed, "ingredients": normalized}
    if "instructions" in parsed:
        new_parsed["instructions"] = normalize_instructions(parsed["instructions"])

    return new_parsed, all_warnings
