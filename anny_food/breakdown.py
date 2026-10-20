"""Per-serving food-group breakdowns: staleness, export, import, overrides.

Breakdowns are computed outside the app (a Claude Code session following
rules.md) and come back through import_payload(). The app never calls an
LLM. A breakdown is tied to a hash of the recipe's ingredient rows and
servings; editing the recipe makes it stale, which pending() reports.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from anny_food.targets import Targets

CONFIDENCE = ("low", "med", "high")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _field(ing, name: str):
    if isinstance(ing, (dict, sqlite3.Row)):
        return ing[name]
    return getattr(ing, name)


def ingredients_hash(base_servings: int | None, ingredients) -> str:
    """Stable hash of what the breakdown depends on.

    Order-independent (re-sorting rows doesn't invalidate) and ignores
    todoist_section / sort_order, which don't change nutrition.
    """
    rows = []
    for ing in ingredients:
        qty = _field(ing, "qty_per_serving")
        rows.append([
            (_field(ing, "name") or "").strip(),
            None if qty is None else round(float(qty), 6),
            (_field(ing, "qty_raw") or "").strip(),
            (_field(ing, "unit") or "").strip(),
            (_field(ing, "notes") or "").strip(),
        ])
    rows.sort(key=lambda r: json.dumps(r))
    blob = json.dumps({"base_servings": base_servings, "ingredients": rows},
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _ingredient_rows(conn: sqlite3.Connection, recipe_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT name, qty_per_serving, qty_raw, unit, notes FROM ingredients "
        "WHERE recipe_id = ? ORDER BY sort_order, id",
        (recipe_id,),
    ).fetchall()


def current_hash(conn: sqlite3.Connection, recipe_id: int) -> str:
    row = conn.execute("SELECT base_servings FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if row is None:
        raise KeyError(recipe_id)
    return ingredients_hash(row["base_servings"], _ingredient_rows(conn, recipe_id))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@dataclass
class RecipeNutrition:
    recipe_id: int
    computed: dict[str, float]          # from the stored breakdown ({} if none)
    overrides: dict[str, float]
    values: dict[str, float]            # effective: overrides win, missing → 0
    breakdown: list[dict] = field(default_factory=list)
    safety_flags: list[str] = field(default_factory=list)
    confidence: str | None = None
    notes: str | None = None
    model: str | None = None
    has_breakdown: bool = False
    stale: bool = False                 # ingredients changed since computed
    stale_overrides: list[str] = field(default_factory=list)


def recipe_nutrition(conn: sqlite3.Connection, recipe_id: int, targets: Targets) -> RecipeNutrition:
    keys = targets.value_keys()
    row = conn.execute(
        "SELECT * FROM anny_recipe_nutrition WHERE recipe_id = ?", (recipe_id,)
    ).fetchone()
    overrides_rows = conn.execute(
        "SELECT key, value, ingredients_hash_at_set FROM anny_nutrition_override WHERE recipe_id = ?",
        (recipe_id,),
    ).fetchall()
    now_hash = current_hash(conn, recipe_id)
    computed = json.loads(row["values_json"]) if row else {}
    overrides = {r["key"]: r["value"] for r in overrides_rows}
    values = {k: float(overrides.get(k, computed.get(k, 0.0))) for k in keys}
    return RecipeNutrition(
        recipe_id=recipe_id,
        computed=computed,
        overrides=overrides,
        values=values,
        breakdown=json.loads(row["breakdown_json"]) if row else [],
        safety_flags=json.loads(row["safety_flags_json"]) if row else [],
        confidence=row["confidence"] if row else None,
        notes=row["notes"] if row else None,
        model=row["model"] if row else None,
        has_breakdown=row is not None,
        stale=row is not None and row["ingredients_hash"] != now_hash,
        stale_overrides=[r["key"] for r in overrides_rows
                         if r["ingredients_hash_at_set"] != now_hash],
    )


def set_override(conn: sqlite3.Connection, recipe_id: int, key: str, value: float) -> None:
    conn.execute(
        "INSERT INTO anny_nutrition_override (recipe_id, key, value, set_at, ingredients_hash_at_set) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(recipe_id, key) DO UPDATE SET "
        "value = excluded.value, set_at = excluded.set_at, "
        "ingredients_hash_at_set = excluded.ingredients_hash_at_set",
        (recipe_id, key, float(value), _now_utc(), current_hash(conn, recipe_id)),
    )


def clear_override(conn: sqlite3.Connection, recipe_id: int, key: str | None = None) -> None:
    if key is None:
        conn.execute("DELETE FROM anny_nutrition_override WHERE recipe_id = ?", (recipe_id,))
    else:
        conn.execute("DELETE FROM anny_nutrition_override WHERE recipe_id = ? AND key = ?",
                     (recipe_id, key))


# ---------------------------------------------------------------------------
# Export: what needs a breakdown
# ---------------------------------------------------------------------------

def pending(
    conn: sqlite3.Connection,
    targets: Targets,
    *,
    ids: list[int] | None = None,
    force: bool = False,
) -> dict:
    """Recipes whose breakdown is missing, stale or lacks a newly added key,
    plus log entries Anny saved as "don't know". With ids, only those
    recipes; with force, include them even when up to date."""
    keys = set(targets.value_keys())
    sql = "SELECT id, title, base_servings, instructions FROM recipes"
    params: tuple = ()
    if ids:
        sql += f" WHERE id IN ({','.join('?' * len(ids))})"
        params = tuple(ids)
    recipes = []
    for r in conn.execute(sql + " ORDER BY id", params).fetchall():
        ings = _ingredient_rows(conn, r["id"])
        h = ingredients_hash(r["base_servings"], ings)
        stored = conn.execute(
            "SELECT ingredients_hash, values_json FROM anny_recipe_nutrition WHERE recipe_id = ?",
            (r["id"],),
        ).fetchone()
        if stored is None:
            reason = "missing"
        elif stored["ingredients_hash"] != h:
            reason = "stale"
        elif not keys <= set(json.loads(stored["values_json"])):
            reason = "missing_keys"
        elif force:
            reason = "forced"
        else:
            continue
        recipes.append({
            "recipe_id": r["id"],
            "title": r["title"],
            "base_servings": r["base_servings"],
            "ingredients_hash": h,
            "reason": reason,
            "ingredients": [dict(i) for i in ings],
            "instructions": r["instructions"],
        })
    log = [dict(row) for row in conn.execute(
        "SELECT id AS log_id, eaten_on, servings, label, note FROM anny_log "
        "WHERE kind = 'pending' ORDER BY id"
    ).fetchall()] if not ids else []
    return {"exported_at": _now_utc(), "value_keys": sorted(keys),
            "recipes": recipes, "log": log}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def validate_item(item: dict, targets: Targets) -> list[str]:
    """Shape checks shared by recipes, staples and log fills."""
    errs: list[str] = []
    required = set(targets.value_keys())
    allowed = {t.key for t in targets.rows if not t.derived}
    values = item.get("values")
    if not isinstance(values, dict):
        return ["values: missing or not an object"]
    missing = sorted(required - set(values))
    if missing:
        errs.append(f"values: missing keys {missing}")
    unknown = sorted(set(values) - allowed)
    if unknown:
        errs.append(f"values: unknown keys {unknown}")
    for k, v in values.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v) or v < 0:
            errs.append(f"values.{k}: must be a number ≥ 0, got {v!r}")
    bd = item.get("breakdown", [])
    if not isinstance(bd, list) or not all(isinstance(x, dict) for x in bd):
        errs.append("breakdown: must be a list of objects")
    flags = item.get("safety_flags", [])
    if not isinstance(flags, list):
        errs.append("safety_flags: must be a list")
    else:
        bad = sorted(set(flags) - set(targets.safety_flags))
        if bad:
            errs.append(f"safety_flags: unknown codes {bad}")
    if item.get("confidence") not in CONFIDENCE:
        errs.append(f"confidence: must be one of {CONFIDENCE}")
    return errs


@dataclass
class ImportReport:
    errors: list[str] = field(default_factory=list)
    recipes: int = 0
    staples: int = 0
    log: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def import_payload(
    conn: sqlite3.Connection,
    payload: dict,
    targets: Targets,
    *,
    dry_run: bool = False,
) -> ImportReport:
    """Validate everything first; write nothing unless all of it is valid.

    The caller owns the transaction (commit/rollback); with dry_run nothing
    is written at all.
    """
    rep = ImportReport()
    default_model = payload.get("model")
    recipes = payload.get("recipes", [])
    staples = payload.get("staples", [])
    log = payload.get("log", [])

    for i, item in enumerate(recipes):
        where = f"recipes[{i}] (id {item.get('recipe_id')})"
        rid = item.get("recipe_id")
        try:
            h = current_hash(conn, rid)
        except KeyError:
            rep.errors.append(f"{where}: no such recipe")
            continue
        if item.get("ingredients_hash") != h:
            rep.errors.append(f"{where}: ingredients changed since export "
                              f"(have {h}, got {item.get('ingredients_hash')}); re-export")
        rep.errors += [f"{where}: {e}" for e in validate_item(item, targets)]
    for i, item in enumerate(staples):
        where = f"staples[{i}] ({item.get('name')!r})"
        if not item.get("name") or not item.get("serving_label"):
            rep.errors.append(f"{where}: name and serving_label are required")
        rep.errors += [f"{where}: {e}" for e in validate_item(item, targets)]
    for i, item in enumerate(log):
        where = f"log[{i}] (id {item.get('log_id')})"
        row = conn.execute("SELECT kind FROM anny_log WHERE id = ?", (item.get("log_id"),)).fetchone()
        if row is None:
            rep.errors.append(f"{where}: no such log entry")
        elif row["kind"] != "pending":
            rep.errors.append(f"{where}: entry is {row['kind']!r}, not pending")
        rep.errors += [f"{where}: {e}" for e in validate_item(item, targets)]

    if rep.errors or dry_run:
        rep.recipes, rep.staples, rep.log = len(recipes), len(staples), len(log)
        return rep

    now = _now_utc()
    for item in recipes:
        conn.execute(
            "INSERT INTO anny_recipe_nutrition (recipe_id, ingredients_hash, values_json, "
            "breakdown_json, safety_flags_json, confidence, notes, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(recipe_id) DO UPDATE SET "
            "ingredients_hash = excluded.ingredients_hash, values_json = excluded.values_json, "
            "breakdown_json = excluded.breakdown_json, safety_flags_json = excluded.safety_flags_json, "
            "confidence = excluded.confidence, notes = excluded.notes, model = excluded.model, "
            "created_at = excluded.created_at",
            (item["recipe_id"], item["ingredients_hash"], json.dumps(item["values"]),
             json.dumps(item.get("breakdown", [])), json.dumps(item.get("safety_flags", [])),
             item["confidence"], item.get("notes"), item.get("model", default_model), now),
        )
        rep.recipes += 1
    for item in staples:
        conn.execute(
            "INSERT INTO anny_staples (name, serving_label, values_json, breakdown_json, "
            "safety_flags_json, confidence, notes, model, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
            "serving_label = excluded.serving_label, values_json = excluded.values_json, "
            "breakdown_json = excluded.breakdown_json, safety_flags_json = excluded.safety_flags_json, "
            "confidence = excluded.confidence, notes = excluded.notes, model = excluded.model, "
            "updated_at = excluded.updated_at",
            (item["name"], item["serving_label"], json.dumps(item["values"]),
             json.dumps(item.get("breakdown", [])), json.dumps(item.get("safety_flags", [])),
             item["confidence"], item.get("notes"), item.get("model", default_model), now),
        )
        rep.staples += 1
    for item in log:
        conn.execute(
            "UPDATE anny_log SET kind = 'custom', values_json = ?, breakdown_json = ? WHERE id = ?",
            (json.dumps(item["values"]), json.dumps(item.get("breakdown", [])), item["log_id"]),
        )
        rep.log += 1
    return rep
