"""Pure-function helpers for the recipe edit form.

No ``import streamlit`` at the top level — all helpers are unit-testable
without a running Streamlit server. Streamlit imports belong in plan.py.
"""
from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# Form → DB value coercion
# ---------------------------------------------------------------------------

def clean_optional_str(v: object) -> str | None:
    """Pass strings through (including empty); coerce anything else to None.

    Used at the form→DB boundary so clearing a text field actually writes
    "" to the DB instead of being silently dropped by a truthiness filter.
    """
    return v if isinstance(v, str) else None


def nan_to_none(v):
    """Convert NaN floats to None; pass everything else through.

    pandas / ``st.data_editor`` returns ``float('nan')`` for cleared numeric
    cells. NaN is not ``None``, so without this conversion the DB silently
    stores NaN and reads back NaN — corrupting downstream arithmetic.
    """
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_recipe_form(payload: dict) -> tuple[bool, list[str]]:
    """Validate a recipe form payload.

    Expected keys (all optional except 'title'):
      title: str
      base_servings: int | float
      instructions: str
      cook_time_min: int | float | None
      source: str
      recipe_book: str

    Returns (ok, errs). When ok is True, errs is empty.
    """
    errs: list[str] = []
    title = payload.get("title", "")
    if not isinstance(title, str) or not title.strip():
        errs.append("Title is required.")

    base_servings = payload.get("base_servings")
    if base_servings is not None:
        try:
            v = int(base_servings)
            if v < 1:
                errs.append("Servings must be at least 1.")
        except (TypeError, ValueError):
            errs.append("Servings must be a whole number.")

    cook_time_min = payload.get("cook_time_min")
    if cook_time_min is not None and cook_time_min != "":
        try:
            v = int(cook_time_min)
            if v < 0:
                errs.append("Cook time cannot be negative.")
        except (TypeError, ValueError):
            errs.append("Cook time must be a whole number of minutes.")

    return (len(errs) == 0, errs)


# ---------------------------------------------------------------------------
# Ingredient diff
# ---------------------------------------------------------------------------

def diff_ingredients(
    before: list[dict], after: list[dict]
) -> dict[str, list[dict]]:
    """Compute adds/updates/deletes between two ingredient lists.

    Each dict must have an ``id`` key (int) for existing rows.
    New rows from the editor have id == 0 or id == None (data_editor appends
    with no id).

    Returns::

        {
            "adds":    [row, ...],      # rows with no existing id
            "updates": [row, ...],      # rows whose id exists in before
            "deletes": [row, ...],      # before rows whose id is absent from after
        }

    Only rows that actually changed are included in "updates" — comparison
    excludes the ``id`` field itself.
    """
    before_by_id = {row["id"]: row for row in before if row.get("id")}

    after_ids: set[int] = set()
    adds: list[dict] = []
    updates: list[dict] = []

    for row in after:
        rid = row.get("id")
        if not rid:
            adds.append(row)
        else:
            after_ids.add(rid)
            old = before_by_id.get(rid)
            if old is None:
                # id present in after but not before — treat as add
                adds.append({k: v for k, v in row.items() if k != "id"})
            else:
                old_cmp = {k: v for k, v in old.items() if k != "id"}
                new_cmp = {k: v for k, v in row.items() if k != "id"}
                if old_cmp != new_cmp:
                    updates.append(row)

    deletes = [row for row in before if row.get("id") and row["id"] not in after_ids]

    return {"adds": adds, "updates": updates, "deletes": deletes}


# ---------------------------------------------------------------------------
# Tag normalization
# ---------------------------------------------------------------------------

def normalize_tags(raw_tags: list[str]) -> list[str]:
    """Lowercase, strip whitespace, and deduplicate. Preserves first-seen order."""
    seen: dict[str, None] = {}
    for t in raw_tags:
        t = t.strip().lower()
        if t:
            seen[t] = None
    return list(seen)


# ---------------------------------------------------------------------------
# Ingredient list → editor rows
# ---------------------------------------------------------------------------

def ingredients_to_rows(ingredients: list) -> list[dict]:
    """Convert Ingredient dataclass instances to plain dicts for st.data_editor.

    The ``id`` field is kept so diff_ingredients can match rows on save.
    """
    return [
        {
            "id": ing.id,
            "name": ing.name,
            "qty_per_serving": ing.qty_per_serving,
            "unit": ing.unit or "",
            "notes": ing.notes or "",
            "todoist_section": ing.todoist_section or "",
            "sort_order": ing.sort_order,
        }
        for ing in ingredients
    ]


# ---------------------------------------------------------------------------
# Phase 19 — View dialog formatting (read-only markdown)
# ---------------------------------------------------------------------------

def _fmt_qty(qty: float) -> str:
    """Format a qty number for display: drop trailing zeros / .0."""
    if qty == int(qty):
        return str(int(qty))
    return f"{qty:g}"


def format_view_block(recipe, tags: list[str], ingredients: list) -> str:
    """Render a recipe as read-only markdown for the View dialog.

    Ingredients are displayed at recipe.base_servings (per-serving × base
    so the user sees the recipe as printed, not as per-serving rows).
    Falls back to ``_No instructions saved._`` when recipe.instructions is None.
    Pure function: no Streamlit imports, safe to unit-test without a server.
    """
    parts: list[str] = [f"## {recipe.title}"]

    meta: list[str] = []
    if getattr(recipe, "recipe_book", None):
        meta.append(f"From: {recipe.recipe_book}")
    if recipe.source:
        meta.append(f"Source: {recipe.source}")
    if recipe.cook_time_min:
        meta.append(f"Cook time: {recipe.cook_time_min} min")
    meta.append(f"Base servings: {recipe.base_servings}")
    if tags:
        meta.append(f"Tags: {', '.join(tags)}")
    parts.append("")
    parts.append("_" + " · ".join(meta) + "_")

    if ingredients:
        parts.append("")
        parts.append("**Ingredients**")
        for ing in ingredients:
            line_parts: list[str] = []
            if ing.qty_per_serving is not None:
                total = ing.qty_per_serving * recipe.base_servings
                line_parts.append(_fmt_qty(total))
            elif getattr(ing, "qty_raw", None):
                # Fallback for non-numeric qtys (ranges like "2-3", verbatim
                # strings stored when parse_qty couldn't extract a number).
                # We show the raw string as-is — it represents the recipe at
                # base_servings, not per-serving (matches insert convention).
                line_parts.append(ing.qty_raw)
            if ing.unit:
                line_parts.append(ing.unit)
            line_parts.append(ing.name)
            parts.append(f"- {' '.join(line_parts)}")

    parts.append("")
    parts.append("### Instructions")
    if recipe.instructions:
        parts.append(recipe.instructions)
    else:
        parts.append("_No instructions saved._")

    return "\n".join(parts)
