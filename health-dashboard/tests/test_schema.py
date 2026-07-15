"""Phase 0 schema tests: init_db() idempotency, table presence, dedup convergence.

Uses the fake_db fixture exclusively — never touches data/health.db.
"""
import sqlite3

import collectors.db as db

EXPECTED_TABLES = {
    "sleep", "heart_rate", "activities", "wellness", "activity_streams",
    "blood_pressure", "body_weight", "body_composition", "nutrition_daily",
}


def _tables(db_path):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def test_init_db_creates_all_cardio_tables(fake_db):
    tables = _tables(fake_db)
    assert EXPECTED_TABLES <= tables


def test_init_db_is_idempotent(fake_db):
    before = _tables(fake_db)
    db.init_db()  # second call against the same (monkeypatched) DB_PATH
    db.init_db()  # third, for good measure
    after = _tables(fake_db)
    assert before == after
    assert EXPECTED_TABLES <= after


def test_blood_pressure_dedup_converges_on_insert_or_replace(fake_db):
    conn = sqlite3.connect(fake_db)
    try:
        ts, source = "2026-01-01T08:00:00", "manual"
        conn.execute(
            "INSERT OR REPLACE INTO blood_pressure "
            "(timestamp, systolic, diastolic, pulse, source) VALUES (?,?,?,?,?)",
            (ts, 120, 80, 60, source),
        )
        conn.execute(
            "INSERT OR REPLACE INTO blood_pressure "
            "(timestamp, systolic, diastolic, pulse, source) VALUES (?,?,?,?,?)",
            (ts, 118, 76, 58, source),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT systolic, diastolic, pulse FROM blood_pressure "
            "WHERE timestamp=? AND source=?",
            (ts, source),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0] == (118, 76, 58)
    finally:
        conn.close()


def test_body_weight_dedup_converges_on_insert_or_replace(fake_db):
    conn = sqlite3.connect(fake_db)
    try:
        ts, source = "2026-01-01T07:30:00", "manual"
        conn.execute(
            "INSERT OR REPLACE INTO body_weight (timestamp, weight_kg, source) "
            "VALUES (?,?,?)",
            (ts, 85.0, source),
        )
        conn.execute(
            "INSERT OR REPLACE INTO body_weight (timestamp, weight_kg, source) "
            "VALUES (?,?,?)",
            (ts, 84.5, source),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT weight_kg FROM body_weight WHERE timestamp=? AND source=?",
            (ts, source),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 84.5
    finally:
        conn.close()


def test_body_composition_dedup_converges_on_insert_or_replace(fake_db):
    conn = sqlite3.connect(fake_db)
    try:
        ts, source = "2026-01-01T00:00:00", "dexa"
        conn.execute(
            "INSERT OR REPLACE INTO body_composition (timestamp, body_fat_pct, source) "
            "VALUES (?,?,?)",
            (ts, 20.0, source),
        )
        conn.execute(
            "INSERT OR REPLACE INTO body_composition (timestamp, body_fat_pct, source) "
            "VALUES (?,?,?)",
            (ts, 19.5, source),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT body_fat_pct FROM body_composition WHERE timestamp=? AND source=?",
            (ts, source),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 19.5
    finally:
        conn.close()


def test_seeded_cardio_rows_present(fake_db):
    conn = sqlite3.connect(fake_db)
    try:
        bp_count = conn.execute("SELECT COUNT(*) FROM blood_pressure").fetchone()[0]
        weight_count = conn.execute("SELECT COUNT(*) FROM body_weight").fetchone()[0]
        comp_count = conn.execute("SELECT COUNT(*) FROM body_composition").fetchone()[0]
        nutrition_count = conn.execute(
            "SELECT COUNT(*) FROM nutrition_daily"
        ).fetchone()[0]
        assert bp_count == 12
        assert weight_count == 6
        assert comp_count == 1
        assert nutrition_count == 0  # left empty until Phase 7
    finally:
        conn.close()


def test_nutrition_daily_primary_key_is_date_source(fake_db):
    conn = sqlite3.connect(fake_db)
    try:
        conn.execute(
            "INSERT INTO nutrition_daily (date, calories_kcal, source) VALUES (?,?,?)",
            ("2026-01-01", 2000, "apple"),
        )
        conn.commit()
        try:
            conn.execute(
                "INSERT INTO nutrition_daily (date, calories_kcal, source) VALUES (?,?,?)",
                ("2026-01-01", 2500, "apple"),
            )
            conn.commit()
            raised = False
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "duplicate (date, source) should violate the PRIMARY KEY"
    finally:
        conn.close()


def test_fake_clinical_installs_without_importing_real_module(fake_clinical):
    import sys

    assert sys.modules["clinical_data"] is fake_clinical
    assert fake_clinical.PATIENT_NAME == "Test Patient"
    assert hasattr(fake_clinical, "CARDIO_GOALS")
    assert hasattr(fake_clinical, "MEDICATIONS")
