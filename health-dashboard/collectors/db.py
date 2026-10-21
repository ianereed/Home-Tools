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
            timestamp TEXT NOT NULL,        -- local ISO time of the weigh-in; 'T00:00:00' if date-only (DEXA)
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
            lean_mass_kg REAL,              -- DEXA lean soft tissue only
            fat_mass_kg REAL,               -- DEXA direct; NULL for Garmin
            bone_mass_kg REAL,
            visceral_fat_rating REAL,       -- Garmin unitless index
            visceral_fat_mass_kg REAL,      -- DEXA VAT mass (converted from lb)
            body_water_pct REAL,            -- Garmin only
            note TEXT,                      -- e.g. scan provider
            source TEXT NOT NULL,           -- 'garmin' | 'dexa' | 'manual'
            muscle_mass_kg REAL,            -- Garmin scale skeletal-muscle estimate (not lean mass)
            source_id TEXT,                 -- Garmin samplePk
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
            cholesterol_mg REAL,
            source TEXT NOT NULL,           -- 'mfp' (MyFitnessPal) | 'apple' (HAE receiver) | 'garmin' | 'manual'
            updated_at TEXT,
            PRIMARY KEY (date, source)
        );

        -- Garmin Connect Lifestyle Logging (launched 2025): tagged daily
        -- behaviors (Alcohol, etc.) with a per-subtype quantity. One row per
        -- (date, behavior, subtype). Alcohol subtypes: BEER | WINE | SPIRIT,
        -- amount = serving count (not ABV-weighted units). subtype '' for
        -- behaviors with no subtype (kept non-NULL so the PK dedupes — SQLite
        -- treats NULLs as distinct in a PK).
        CREATE TABLE IF NOT EXISTS lifestyle_log (
            date TEXT NOT NULL,             -- local calendar date 'YYYY-MM-DD'
            name TEXT NOT NULL,             -- behavior label, e.g. 'Alcohol'
            subtype TEXT NOT NULL DEFAULT '',
            amount REAL,                    -- serving count / quantity
            source TEXT NOT NULL,           -- 'garmin' | 'manual'
            PRIMARY KEY (date, name, subtype, source)
        );

        -- VO2max estimate, one row per day Garmin updated it. Same shape the
        -- historical backfill (garmin_backfill.ensure_vo2_table) created, so
        -- this is a no-op on DBs that already have it.
        -- Garmin all-day energy (get_stats), one row per day. consumed_kcal is
        -- whatever a linked food app pushed to Garmin (calories only); NULL when
        -- nothing is linked.
        CREATE TABLE IF NOT EXISTS daily_energy (
            date TEXT NOT NULL,
            total_kcal REAL,
            active_kcal REAL,
            bmr_kcal REAL,
            consumed_kcal REAL,
            source TEXT NOT NULL,
            PRIMARY KEY (date, source)
        );

        -- Fuel planner key/value state (last refreshed activity id, pending
        -- refresh flags, pace choice, backoff timers).
        CREATE TABLE IF NOT EXISTS fuel_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );

        -- Fuel planner runs: full JSON payload for the dashboard + the snapshot
        -- that produced it (reproducible), one row per refresh.
        CREATE TABLE IF NOT EXISTS fuel_plan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            trigger TEXT,
            reason TEXT,
            plan_start TEXT,
            plan_end TEXT,
            engine_version TEXT,
            status TEXT NOT NULL,           -- ok | error
            error TEXT,
            duration_ms INTEGER,
            payload_json TEXT,
            snapshot_json TEXT
        );

        -- Daily targets per run (cheap SQL joins against intake).
        CREATE TABLE IF NOT EXISTS fuel_plan_days (
            run_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            pace TEXT NOT NULL,
            category TEXT,
            source TEXT,
            tdee REAL, kcal REAL, protein_g REAL, carb_g REAL, fat_g REAL,
            sodium_max_mg REAL, chol_max_mg REAL, satfat_max_g REAL, deficit REAL,
            PRIMARY KEY (run_id, date, pace)
        );

        -- Garmin Coach window as fetched (deduped by digest) + per-workout versions
        -- and detected changes, for projection and the Coach-adaptation experiment.
        CREATE TABLE IF NOT EXISTS coach_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at TEXT NOT NULL,
            plan_id INTEGER,
            window_start TEXT,
            window_end TEXT,
            digest TEXT UNIQUE,
            payload_json TEXT
        );
        CREATE TABLE IF NOT EXISTS coach_workout_versions (
            date TEXT NOT NULL,
            workout_uuid TEXT NOT NULL,
            digest TEXT NOT NULL,
            name TEXT,
            type TEXT,
            duration_s REAL,
            planned_kj REAL,
            workout_json TEXT,
            first_seen TEXT,
            last_seen TEXT,
            PRIMARY KEY (date, workout_uuid, digest)
        );
        CREATE TABLE IF NOT EXISTS coach_diffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT NOT NULL,
            date TEXT NOT NULL,
            change TEXT NOT NULL,           -- added | removed | changed
            before_json TEXT,
            after_json TEXT,
            context_json TEXT
        );

        -- Planner-owned workouts on the Garmin calendar (lifts; generated rides) + an audit log of
        -- every write the planner performs (or would perform, in dry_run).
        CREATE TABLE IF NOT EXISTS fuel_garmin_workouts (
            plan_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,             -- lift | ride
            date TEXT NOT NULL,
            spec_hash TEXT,
            name TEXT,
            garmin_workout_id INTEGER,
            garmin_schedule_id INTEGER,
            status TEXT NOT NULL,           -- creating | synced | moved_by_user | user_removed | removed | error
            reasons_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS fuel_garmin_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            run_id INTEGER,
            mode TEXT NOT NULL,
            op TEXT NOT NULL,
            plan_key TEXT,
            workout_id INTEGER,
            schedule_id INTEGER,
            date TEXT,
            ok INTEGER NOT NULL,
            detail TEXT
        );

        -- Ride sweat sodium -> negative-sodium MyFitnessPal entry, one row per
        -- Garmin activity. mfp_entry_id is the diary entry the planner owns, so
        -- re-runs skip or replace it instead of logging a duplicate.
        CREATE TABLE IF NOT EXISTS fuel_sweat_sodium (
            activity_id TEXT PRIMARY KEY,   -- Garmin activityId
            date TEXT NOT NULL,             -- local ride date = MFP diary day
            water_ml REAL,                  -- Garmin waterEstimated, or the duration fallback
            water_source TEXT,              -- garmin | fallback
            mg_per_l REAL,
            sodium_mg INTEGER,              -- what the planner wants on the diary (0 = no entry)
            mode TEXT NOT NULL,             -- dry_run | live (mode of the last run that touched it)
            status TEXT NOT NULL,           -- planned | logged | skipped | error | needs_concentration
            mfp_entry_id TEXT,              -- MFP diary entry uuid while one exists
            logged_mg INTEGER,              -- servings on that entry
            detail TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS vo2max (
            date TEXT NOT NULL,             -- Garmin calendarDate of the estimate
            vo2max REAL,                    -- ml/kg/min (vo2MaxPreciseValue)
            source TEXT NOT NULL,
            PRIMARY KEY (date, source)
        );
    """)
    _migrate(conn)
    conn.commit()
    conn.close()


ACTIVITY_FUEL_COLUMNS = (
    ("name", "TEXT"),
    ("duration_s", "REAL"),        # timer time (power x this = work)
    ("moving_s", "REAL"),
    ("avg_power_w", "REAL"),
    ("np_w", "REAL"),
    ("bmr_kcal", "REAL"),          # resting share of `calories`
    ("training_load", "REAL"),
    ("aerobic_te", "REAL"),
)


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
    # Fuel planner: power-meter energy needs timer/moving time, power and the
    # resting (BMR) share Garmin folds into `calories`.
    for col, typ in ACTIVITY_FUEL_COLUMNS:
        if col not in cols:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {col} {typ}")

    ncols = {row[1] for row in conn.execute("PRAGMA table_info(nutrition_daily)")}
    # sweat_sodium_mg: the planner's negative-sodium MFP entries that day, as a positive number.
    # MFP's sodium_mg is already net of it; gross intake = sodium_mg + sweat_sodium_mg.
    for col, typ in (("cholesterol_mg", "REAL"), ("updated_at", "TEXT"), ("sweat_sodium_mg", "REAL")):
        if col not in ncols:
            conn.execute(f"ALTER TABLE nutrition_daily ADD COLUMN {col} {typ}")

    bcols = {row[1] for row in conn.execute("PRAGMA table_info(body_composition)")}
    for col, typ in (("muscle_mass_kg", "REAL"), ("source_id", "TEXT")):
        if col not in bcols:
            conn.execute(f"ALTER TABLE body_composition ADD COLUMN {col} {typ}")

    # Data migrations, one-shot, tracked in PRAGMA user_version.
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        # The iPhone receiver used to tag every intraday Apple HR sample
        # context='resting', which inflated resting HR to ~80 bpm. Apple's
        # real daily resting value (receiver and full-export import alike) is
        # stamped at midnight; everything else is an intraday sample.
        conn.execute(
            "UPDATE heart_rate SET context = 'intraday' "
            "WHERE source = 'apple' AND context = 'resting' "
            "AND substr(timestamp, 11) != 'T00:00:00'")
        conn.execute("PRAGMA user_version = 1")
    if version < 2:
        # Garmin's muscleMass (the scale's skeletal-muscle estimate, ~34 kg) was
        # stored as lean_mass_kg, and the fuel planner read it as DEXA lean mass.
        conn.execute(
            "UPDATE body_composition SET muscle_mass_kg = lean_mass_kg, lean_mass_kg = NULL "
            "WHERE source = 'garmin' AND lean_mass_kg IS NOT NULL")
        conn.execute("PRAGMA user_version = 2")


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
