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

        -- Blood pressure readings. Sparse: manual entries or a Garmin cuff. Multiple
        -- readings per day matter clinically, so keyed on timestamp (like heart_rate),
        -- not date (like wellness).
        CREATE TABLE IF NOT EXISTS blood_pressure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,        -- local ISO 'YYYY-MM-DDTHH:MM:SS' (measurement time)
            systolic INTEGER NOT NULL,      -- mmHg
            diastolic INTEGER NOT NULL,     -- mmHg
            pulse INTEGER,                  -- bpm when the cuff reports it
            source TEXT NOT NULL,           -- 'garmin' | 'apple' | 'manual'
            source_id TEXT,                 -- Garmin measurement pk/version (audit/debug)
            notes TEXT,
            UNIQUE(timestamp, source)
        );

        CREATE TABLE IF NOT EXISTS body_weight (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,        -- local ISO; 'YYYY-MM-DDT00:00:00' if date-only
            weight_kg REAL NOT NULL,        -- kg canonical (Garmin sends grams: /1000 at write)
            bmi REAL,                       -- as reported by source; never computed locally
            source TEXT NOT NULL,           -- 'garmin' | 'apple' | 'dexa' | 'manual'
            source_id TEXT,                 -- Garmin samplePk
            UNIQUE(timestamp, source)
        );

        -- Body-composition snapshots: Garmin scale BIA rows AND quarterly DEXA rows.
        -- source implies method ('garmin'=BIA estimate, 'dexa'=DEXA scan); the UI must
        -- never merge them into one series.
        CREATE TABLE IF NOT EXISTS body_composition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,        -- 'YYYY-MM-DDT00:00:00' for DEXA (date-only)
            weight_kg REAL,
            body_fat_pct REAL,
            lean_mass_kg REAL,              -- DEXA lean soft tissue; Garmin muscleMass
            fat_mass_kg REAL,               -- DEXA direct; NULL for Garmin
            bone_mass_kg REAL,
            visceral_fat_rating REAL,       -- Garmin unitless index
            visceral_fat_mass_kg REAL,      -- DEXA VAT mass (converted from lb)
            body_water_pct REAL,            -- Garmin only
            note TEXT,                      -- e.g. scan provider
            source TEXT NOT NULL,           -- 'garmin' | 'dexa' | 'manual'
            UNIQUE(timestamp, source)
        );

        -- Daily nutrition rollup. Created in Phase 0 (future-proofing); populated in
        -- Phase 7 once the diet-tracking app is chosen and adopted.
        CREATE TABLE IF NOT EXISTS nutrition_daily (
            date TEXT NOT NULL,
            calories_kcal REAL,
            protein_g REAL,
            carbs_g REAL,
            fat_g REAL,
            saturated_fat_g REAL,
            fiber_g REAL,
            sugar_g REAL,
            sodium_mg REAL,
            potassium_mg REAL,
            source TEXT NOT NULL,           -- 'apple' (HAE receiver) | 'garmin' | 'manual'
            PRIMARY KEY (date, source)
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
