"""Anny Food tables, kept in the shared recipes.db next to the recipes they
describe. Nothing here touches health-dashboard data."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from meal_planner import db as _mp_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS anny_recipe_nutrition (
    recipe_id INTEGER PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
    ingredients_hash TEXT NOT NULL,
    values_json TEXT NOT NULL,          -- per-serving {key: amount}
    breakdown_json TEXT NOT NULL,       -- audit lines, see rules.md
    safety_flags_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL,           -- low | med | high
    notes TEXT,
    model TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS anny_nutrition_override (
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value REAL NOT NULL,
    set_at TEXT NOT NULL,
    ingredients_hash_at_set TEXT,       -- warns when the recipe changed since
    PRIMARY KEY (recipe_id, key)
);
CREATE TABLE IF NOT EXISTS anny_staples (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    serving_label TEXT NOT NULL,
    values_json TEXT NOT NULL,
    breakdown_json TEXT NOT NULL DEFAULT '[]',
    safety_flags_json TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL DEFAULT 'high',
    notes TEXT,
    model TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS anny_log (
    id INTEGER PRIMARY KEY,
    eaten_on TEXT NOT NULL,             -- local date, YYYY-MM-DD
    logged_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('recipe', 'staple', 'custom', 'pending')),
    recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
    staple_id INTEGER REFERENCES anny_staples(id) ON DELETE SET NULL,
    servings REAL NOT NULL DEFAULT 1,
    label TEXT NOT NULL,                -- what Anny saw/typed
    values_json TEXT,                   -- custom/pending entries only
    breakdown_json TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_anny_log_eaten_on ON anny_log(eaten_on);
CREATE TABLE IF NOT EXISTS anny_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_db(path: Path | str | None = None) -> None:
    """Ensure the recipe schema, then the anny_* tables. Idempotent."""
    p = Path(path) if path is not None else _mp_db.DB_PATH
    _mp_db.init_db(p)
    with _mp_db._get_conn(p) as conn:
        conn.executescript(_SCHEMA)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path is not None else _mp_db.DB_PATH
    return _mp_db._get_conn(p)


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM anny_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None and row["value"] is not None else default


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        "INSERT INTO anny_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
