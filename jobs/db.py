"""
SQLite configuration for the huey backend.

WAL + busy_timeout + foreign_keys are applied once at module import. huey
itself uses a single file; we apply the pragmas to that file before any
worker connects.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

JOBS_DIR = Path.home() / "Home-Tools" / "jobs"
HUEY_DB_PATH = JOBS_DIR / "jobs.db"

_PRAGMAS = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),  # WAL + NORMAL is durable + fast enough for hobby load
    ("busy_timeout", "5000"),    # ms — wait up to 5s before raising SQLITE_BUSY
    ("foreign_keys", "ON"),
)


def configure_sqlite(path: Path) -> None:
    """Set WAL + busy_timeout + foreign_keys on the huey db file.

    Idempotent. Safe to call before huey ever opens the db (we'll create it
    if missing). Raises OSError if the parent dir doesn't exist or isn't
    writable — by design; surfacing that early beats discovering it inside
    the consumer.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        for key, val in _PRAGMAS:
            conn.execute(f"PRAGMA {key}={val}")
        conn.commit()
