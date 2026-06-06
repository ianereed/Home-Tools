"""Database initialization and helper functions."""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "health.db")


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sleep (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            total_minutes REAL,
            deep_minutes REAL,
            rem_minutes REAL,
            light_minutes REAL,
            awake_minutes REAL,
            source TEXT NOT NULL,
            UNIQUE(date, source)
        );

        CREATE TABLE IF NOT EXISTS heart_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            bpm INTEGER NOT NULL,
            context TEXT DEFAULT 'resting',
            source TEXT NOT NULL,
            UNIQUE(timestamp, source)
        );

        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT,
            duration_minutes REAL,
            distance_km REAL,
            avg_hr INTEGER,
            max_hr INTEGER,
            calories INTEGER,
            source TEXT NOT NULL,
            source_id TEXT,
            start_time TEXT,
            dup_of INTEGER,
            UNIQUE(source, source_id)
        );

        CREATE TABLE IF NOT EXISTS wellness (
            date TEXT PRIMARY KEY,
            hrv REAL,
            hrv_sdnn REAL,
            sleep_score REAL,
            sleep_quality REAL,
            avg_sleeping_hr REAL,
            readiness REAL,
            spo2 REAL,
            steps INTEGER,
            source TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS activity_streams (
            activity_id TEXT NOT NULL,
            timestamp_offset INTEGER NOT NULL,
            bpm INTEGER,
            PRIMARY KEY (activity_id, timestamp_offset)
        );
    """)
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn):
    """Apply additive schema changes to a pre-existing DB.

    CREATE TABLE IF NOT EXISTS never alters an existing table, so columns added
    after the table first shipped have to be added with guarded ALTER TABLEs.
    Each is a no-op once present, so init_db() stays idempotent.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(activities)")}
    if "start_time" not in cols:
        # Full local start timestamp — lets the de-dup matcher pair the same
        # workout across sources by time, not just calendar date.
        conn.execute("ALTER TABLE activities ADD COLUMN start_time TEXT")
    if "dup_of" not in cols:
        # NULL = canonical/unique. Non-NULL points at the canonical row this is
        # a cross-source duplicate of (the recording device's copy).
        conn.execute("ALTER TABLE activities ADD COLUMN dup_of INTEGER")


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
