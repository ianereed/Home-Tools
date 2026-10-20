"""Anny's food log: entries, the food picker, and per-entry values."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from anny_food import breakdown as _bd
from anny_food.targets import Targets


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_entry(
    conn: sqlite3.Connection,
    *,
    eaten_on: date,
    kind: str,
    label: str,
    servings: float = 1.0,
    recipe_id: int | None = None,
    staple_id: int | None = None,
    values: dict[str, float] | None = None,
    note: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO anny_log (eaten_on, logged_at, kind, recipe_id, staple_id, servings, "
        "label, values_json, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (eaten_on.isoformat(), _now_utc(), kind, recipe_id, staple_id, float(servings),
         label, json.dumps(values) if values is not None else None, note),
    )
    return cur.lastrowid  # type: ignore[return-value]


def update_entry(conn: sqlite3.Connection, entry_id: int, *, servings: float | None = None,
                 eaten_on: date | None = None) -> None:
    if servings is not None:
        conn.execute("UPDATE anny_log SET servings = ? WHERE id = ?", (float(servings), entry_id))
    if eaten_on is not None:
        conn.execute("UPDATE anny_log SET eaten_on = ? WHERE id = ?", (eaten_on.isoformat(), entry_id))


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("DELETE FROM anny_log WHERE id = ?", (entry_id,))


def entries_between(conn: sqlite3.Connection, start: date, end: date) -> list[dict]:
    """Entries with start <= eaten_on <= end, oldest first."""
    rows = conn.execute(
        "SELECT * FROM anny_log WHERE eaten_on BETWEEN ? AND ? ORDER BY eaten_on, id",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


def first_entry_date(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT min(eaten_on) AS d FROM anny_log").fetchone()
    return date.fromisoformat(row["d"]) if row["d"] else None


def list_foods(conn: sqlite3.Connection) -> list[dict]:
    """Everything the quick-add picker offers: recipes, then staples."""
    recipes = conn.execute(
        "SELECT r.id, r.title, n.recipe_id IS NOT NULL AS has_bd FROM recipes r "
        "LEFT JOIN anny_recipe_nutrition n ON n.recipe_id = r.id ORDER BY r.title COLLATE NOCASE"
    ).fetchall()
    staples = conn.execute(
        "SELECT id, name, serving_label FROM anny_staples ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return (
        [{"kind": "recipe", "id": r["id"], "label": r["title"], "has_breakdown": bool(r["has_bd"])}
         for r in recipes]
        + [{"kind": "staple", "id": s["id"], "label": f"{s['name']} ({s['serving_label']})",
            "has_breakdown": True} for s in staples]
    )


def staple(conn: sqlite3.Connection, staple_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM anny_staples WHERE id = ?", (staple_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["values"] = json.loads(row["values_json"])
    out["breakdown"] = json.loads(row["breakdown_json"])
    out["safety_flags"] = json.loads(row["safety_flags_json"])
    return out


class ValueResolver:
    """Per-entry values (already × servings), caching recipe/staple lookups."""

    def __init__(self, conn: sqlite3.Connection, targets: Targets):
        self._conn = conn
        self._targets = targets
        self._recipes: dict[int, _bd.RecipeNutrition] = {}
        self._staples: dict[int, dict[str, float]] = {}

    def per_serving(self, entry: dict) -> tuple[dict[str, float], str | None]:
        """Values for one serving, plus a problem note when they're missing
        or unreliable (shown next to the entry)."""
        kind = entry["kind"]
        if kind == "recipe" and entry["recipe_id"] is not None:
            rid = entry["recipe_id"]
            if rid not in self._recipes:
                try:
                    self._recipes[rid] = _bd.recipe_nutrition(self._conn, rid, self._targets)
                except KeyError:
                    return {}, "recipe deleted"
            n = self._recipes[rid]
            if not n.has_breakdown and not n.overrides:
                return {}, "breakdown pending"
            return n.values, ("recipe changed — breakdown pending" if n.stale else None)
        if kind == "staple" and entry["staple_id"] is not None:
            sid = entry["staple_id"]
            if sid not in self._staples:
                s = staple(self._conn, sid)
                self._staples[sid] = s["values"] if s else {}
            return self._staples[sid], (None if self._staples[sid] else "staple deleted")
        if entry["values_json"]:
            return json.loads(entry["values_json"]), None
        return {}, ("breakdown pending" if kind == "pending" else "no amounts")

    def values(self, entry: dict) -> dict[str, float]:
        per, _ = self.per_serving(entry)
        s = float(entry["servings"])
        return {k: v * s for k, v in per.items()}
