"""photos_intake table operations.

The table itself is created by meal_planner.db.init_db (Phase 16 schema add).
This module provides typed CRUD for the worker code in Chunks 2-4.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from meal_planner.db import DB_PATH, _get_conn, init_db

_VALID_STATUSES = frozenset({
    "pending", "extracting", "ok", "ok_partial",
    "parse_fail", "validation_fail", "ollama_error", "timeout",
    "outlier_pending", "gemini_pending", "gemini_ok",
    "skipped", "wedged",
})


@dataclass
class IntakeRow:
    sha: str
    source_path: str
    nas_path: str
    status: str
    recipe_id: int | None
    error: str | None
    n_retries: int
    enqueued_at: str
    completed_at: str | None
    extraction_path: str | None
    extraction_warnings: str | None
    source: str | None = None


def _row_to_intake(row: sqlite3.Row) -> IntakeRow:
    # `source` was added in Phase 21 as a migration via _add_column_if_missing;
    # access defensively so this stays compatible with conns built from in-test
    # _SCHEMA executescripts that don't run the migration step.
    try:
        source = row["source"]
    except (IndexError, KeyError):
        source = None
    return IntakeRow(
        sha=row["sha"],
        source_path=row["source_path"],
        nas_path=row["nas_path"],
        status=row["status"],
        recipe_id=row["recipe_id"],
        error=row["error"],
        n_retries=row["n_retries"],
        enqueued_at=row["enqueued_at"],
        completed_at=row["completed_at"],
        extraction_path=row["extraction_path"],
        extraction_warnings=row["extraction_warnings"],
        source=source,
    )


def init_intake_table(conn: sqlite3.Connection | None = None) -> None:
    """Ensure the photos_intake table exists. Delegates to meal_planner.db.init_db
    when conn is None; otherwise issues the schema directly on the given conn."""
    if conn is None:
        init_db()
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS photos_intake (
            sha TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            nas_path TEXT NOT NULL,
            status TEXT NOT NULL,
            recipe_id INTEGER REFERENCES recipes(id) ON DELETE SET NULL,
            error TEXT,
            n_retries INTEGER NOT NULL DEFAULT 0,
            enqueued_at TEXT NOT NULL,
            completed_at TEXT,
            extraction_path TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_photos_intake_status ON photos_intake(status);
        """
    )


def record_intake(
    sha: str,
    source_path: str,
    nas_path: str,
    *,
    source: str | None = None,
    conn: sqlite3.Connection | None = None,
    path: Path | None = None,
) -> bool:
    """Insert a fresh pending row for a content-hash. Returns False if sha already exists.

    `source` (Phase 21): provenance label for analytics — "nas" or "iphone".
    NULL on legacy rows that pre-date the column.
    """
    now = datetime.now(timezone.utc).isoformat()
    # Try the path with `source` first; fall back to the legacy column set on
    # an in-test schema that hasn't had the migration applied.
    sql_with_source = """
        INSERT OR IGNORE INTO photos_intake
          (sha, source_path, nas_path, status, n_retries, enqueued_at, source)
        VALUES (?, ?, ?, 'pending', 0, ?, ?)
    """
    sql_legacy = """
        INSERT OR IGNORE INTO photos_intake
          (sha, source_path, nas_path, status, n_retries, enqueued_at)
        VALUES (?, ?, ?, 'pending', 0, ?)
    """
    params_with_source = (sha, source_path, nas_path, now, source)
    params_legacy = (sha, source_path, nas_path, now)

    def _exec(c: sqlite3.Connection) -> bool:
        try:
            cur = c.execute(sql_with_source, params_with_source)
        except sqlite3.OperationalError as exc:
            if "no column named source" not in str(exc).lower():
                raise
            cur = c.execute(sql_legacy, params_legacy)
        return cur.rowcount > 0

    if conn is not None:
        return _exec(conn)
    p = path or DB_PATH
    with _get_conn(p) as c:
        return _exec(c)


def _delete_by_sha(sha: str, *, db_path: Path | None = None) -> None:
    """Internal: remove a row when a scan-side rename failed before the file moved.

    Not for general use — Chunk 4 wedge logic owns the legitimate "remove
    photos_intake row" cases.
    """
    p = db_path or DB_PATH
    with _get_conn(p) as c:
        c.execute("DELETE FROM photos_intake WHERE sha = ?", (sha,))


def mark_status(
    sha: str,
    status: str,
    *,
    recipe_id: int | None = None,
    error: str | None = None,
    extraction_path: str | None = None,
    extraction_warnings: str | None = None,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> None:
    """Update status (and optionally recipe_id/error/extraction_path/extraction_warnings) for a sha row.

    Sets completed_at when the new status is a terminal one.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"unknown status: {status!r}")
    terminal = {"ok", "ok_partial", "skipped", "wedged", "gemini_ok"}
    completed_at = datetime.now(timezone.utc).isoformat() if status in terminal else None

    sql = """
        UPDATE photos_intake
           SET status = ?,
               recipe_id = COALESCE(?, recipe_id),
               error = ?,
               extraction_path = COALESCE(?, extraction_path),
               extraction_warnings = COALESCE(?, extraction_warnings),
               completed_at = COALESCE(?, completed_at)
         WHERE sha = ?
    """
    params = (status, recipe_id, error, extraction_path, extraction_warnings, completed_at, sha)
    if conn is not None:
        conn.execute(sql, params)
        return
    p = db_path or DB_PATH
    with _get_conn(p) as c:
        c.execute(sql, params)


def list_pending(
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> list[IntakeRow]:
    """Return all rows currently at status='pending'."""
    sql = "SELECT * FROM photos_intake WHERE status = 'pending' ORDER BY enqueued_at"
    if conn is not None:
        rows = conn.execute(sql).fetchall()
        return [_row_to_intake(r) for r in rows]
    p = db_path or DB_PATH
    with _get_conn(p) as c:
        rows = c.execute(sql).fetchall()
        return [_row_to_intake(r) for r in rows]


# Failure statuses worth a re-extraction. llama3.2-vision is non-deterministic:
# the same image can parse_fail/validation_fail one run and succeed the next
# (confirmed 2026-05-31 — done_reason=stop, well under num_ctx, yet 2 fails +
# 1 clean success). timeout/ollama_error are transient transport/load issues.
RETRYABLE_STATUSES = frozenset({"timeout", "ollama_error", "parse_fail", "validation_fail"})


def _list_by_status_and_retries(
    statuses: frozenset[str], op: str, max_retries: int, conn, db_path,
) -> list[IntakeRow]:
    placeholders = ",".join(["?"] * len(statuses))
    sql = (
        f"SELECT * FROM photos_intake "
        f"WHERE status IN ({placeholders}) AND n_retries {op} ? "
        f"ORDER BY enqueued_at"
    )
    params = (*sorted(statuses), max_retries)
    if conn is not None:
        return [_row_to_intake(r) for r in conn.execute(sql, params).fetchall()]
    p = db_path or DB_PATH
    with _get_conn(p) as c:
        return [_row_to_intake(r) for r in c.execute(sql, params).fetchall()]


def list_retryable(
    max_retries: int,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> list[IntakeRow]:
    """Rows in a retryable failure status with n_retries < max_retries."""
    return _list_by_status_and_retries(RETRYABLE_STATUSES, "<", max_retries, conn, db_path)


def list_exhausted(
    max_retries: int,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> list[IntakeRow]:
    """Rows still in a retryable failure status that have hit n_retries >= max_retries
    (i.e. out of attempts — the caller should wedge them)."""
    return _list_by_status_and_retries(RETRYABLE_STATUSES, ">=", max_retries, conn, db_path)


def bump_retry(
    sha: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> None:
    """Arm a failed row for another attempt: increment n_retries, reset to
    'pending', and clear the prior error. The caller then re-enqueues ingest."""
    sql = (
        "UPDATE photos_intake "
        "SET n_retries = n_retries + 1, status = 'pending', error = NULL "
        "WHERE sha = ?"
    )
    if conn is not None:
        conn.execute(sql, (sha,))
        return
    p = db_path or DB_PATH
    with _get_conn(p) as c:
        c.execute(sql, (sha,))


def get_by_sha(
    sha: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> IntakeRow | None:
    sql = "SELECT * FROM photos_intake WHERE sha = ?"
    if conn is not None:
        row = conn.execute(sql, (sha,)).fetchone()
        return _row_to_intake(row) if row else None
    p = db_path or DB_PATH
    with _get_conn(p) as c:
        row = c.execute(sql, (sha,)).fetchone()
        return _row_to_intake(row) if row else None


# ── Gemini daily-budget counter ──────────────────────────────────────────────
#
# The Gemini escalation path is capped per calendar day to leave free-tier
# headroom for other Gemini tasks. The cap is keyed on the *local* calendar day
# (date('now','localtime')) so it resets at the user's midnight, not UTC — a
# user-facing boundary (see the TZ rule). The mini runs in the user's timezone.
# Counts ATTEMPTS (a failed Gemini call still burned quota), so callers consume
# the budget BEFORE making the call.

def gemini_used_today(
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> int:
    """Number of Gemini calls already consumed on the current local day."""
    sql = (
        "SELECT COALESCE("
        "(SELECT n FROM gemini_usage WHERE day = date('now','localtime')), 0)"
    )
    if conn is not None:
        return int(conn.execute(sql).fetchone()[0])
    p = db_path or DB_PATH
    with _get_conn(p) as c:
        return int(c.execute(sql).fetchone()[0])


def gemini_try_consume(
    max_per_day: int = 5,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: Path | None = None,
) -> bool:
    """Atomically consume one unit of today's Gemini budget.

    Returns True if a unit was consumed (caller may proceed with the Gemini
    call), False if today's cap is already reached. The increment is a single
    upsert gated on the current count, so concurrent callers can't overshoot the
    cap. Success is verified via the connection's total_changes delta rather than
    trusting rowcount (see feedback_insert_or_ignore_silent_failure)."""
    sql = (
        "INSERT INTO gemini_usage(day, n) VALUES(date('now','localtime'), 1) "
        "ON CONFLICT(day) DO UPDATE SET n = n + 1 WHERE gemini_usage.n < ?"
    )

    def _run(c: sqlite3.Connection) -> bool:
        before = c.total_changes
        c.execute(sql, (max_per_day,))
        return (c.total_changes - before) == 1

    if conn is not None:
        return _run(conn)
    p = db_path or DB_PATH
    with _get_conn(p) as c:
        consumed = _run(c)
        c.commit()
        return consumed
