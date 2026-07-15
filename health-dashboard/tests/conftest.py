"""Shared pytest fixtures for the health-dashboard test suite.

Both fixtures must do their setup before any dashboard/collector code runs so
that a test process never touches real data:

- `fake_db` points `collectors.db.DB_PATH` at a disposable tmp-path SQLite
  file (Standing rule 4 — the laptop `data/health.db` is frozen, tests must
  never write to it), initializes the schema, and seeds ~90 days of synthetic
  wearable data plus sparse synthetic cardio rows.
- `fake_clinical` builds a fake `clinical_data` module with obviously-fake
  values and installs it into `sys.modules` before anything imports
  `dashboard.cardiology_view` or `cardiology.build_report` — the real PHI
  module (`cardiology/clinical_data.py`, gitignored) must never load in a
  test process.
"""
import datetime
import random
import sqlite3
import sys
import types

import pytest

import collectors.db as db

_RNG_SEED = 20260101  # fixed seed -> deterministic synthetic data across runs


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    """Fresh tmp-path DB with the full schema and ~90 days of synthetic rows."""
    db_path = tmp_path / "health.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    _seed_synthetic(str(db_path))
    return str(db_path)


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    """Freshly-initialized tmp-path DB: schema only, zero rows — the no-scale/
    no-BP-cuff reality every UI section must render without crashing on."""
    db_path = tmp_path / "empty_health.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    return str(db_path)


def _base_fake_clinical_module():
    """Fields common to every fake clinical_data variant. Values are obviously
    fake (round numbers, placeholder names) — nothing here resembles Ian's
    real diagnosis, medications, or lab results."""
    mod = types.ModuleType("clinical_data")
    mod.PATIENT_NAME = "Test Patient"
    mod.DOB = "1990-01-01"
    mod.SEX = "M"
    mod.STATIN = "Faketatin"
    mod.DESCRIPTOR = "synthetic fixture patient — not real"
    mod.CLINICAL_SUMMARY = "Synthetic clinical summary for test fixtures only."
    mod.REFERENCE = {
        "total_chol": (0, 200), "ldl": (0, 100), "hdl": (40, 999), "trig": (0, 150),
    }
    # (date, statin_dose_mg, fasting, total_chol, trig, hdl, ldl, apob, lpa_nmol_l, note)
    mod.LIPID_PANELS = [
        ("2025-01-15", 0, True, 200, 100, 45, 130, None, None, "fake baseline"),
        ("2025-07-15", 20, True, 150, 90, 48, 65, 70, None, "fake on-therapy"),
    ]
    mod.EVENTS = []
    mod.LIFE_EVENTS = []
    # (date, dose_mg, note)
    mod.STATIN_EVENTS = [
        ("2025-02-01", 10, "fake: started 10 mg"),
        ("2025-04-01", 20, "fake: titrated to 20 mg"),
    ]
    mod.RISK_MARKERS = []
    return mod


@pytest.fixture
def fake_clinical(monkeypatch):
    """Install a synthetic clinical_data module under sys.modules['clinical_data'],
    WITH CARDIO_GOALS/MEDICATIONS set (Appendix B shape) — the
    Phase-1-updated-PHI-file case. See `fake_clinical_no_goals` for the
    pre-Phase-1 (goals-absent) case.
    """
    mod = _base_fake_clinical_module()
    mod.CARDIO_GOALS = {
        "ldl": {"target": 999, "stretch": 999, "unit": "mg/dL"},
        "bp": {"systolic": 999, "diastolic": 999, "unit": "mmHg"},
        "weight": {"baseline_kg": 0.0, "lose_lb_min": 0, "lose_lb_max": 0},
        "set_by": "Dr. Fake (fixture)",
        "set_on": "2025-01",
    }
    mod.MEDICATIONS = [
        {
            "name": "fakestatin", "brand": "Fakezor",
            "dose": "0 mg", "form": "oral tablet", "frequency": "daily",
            "start": "2025-02-01", "status": "active",
            "prescriber": "Dr. Fake", "purpose": "fixture only",
            "note": "synthetic fixture entry",
        },
        {
            "name": "fakolumab", "brand": "Fakepha",
            "dose": "0 mg/mL", "form": "SC autoinjector (click)",
            "frequency": "every 2 weeks",
            "start": None, "status": "prescribed — not yet started",
            "prescriber": "Dr. Fake", "purpose": "fixture only",
            "note": "synthetic fixture entry — exercises the no-start-date path",
        },
    ]
    monkeypatch.setitem(sys.modules, "clinical_data", mod)
    return mod


@pytest.fixture
def fake_clinical_no_goals(monkeypatch):
    """Same synthetic module as `fake_clinical` but WITHOUT CARDIO_GOALS or
    MEDICATIONS — simulates an un-updated (pre-Phase-1) clinical_data.py so
    the getattr(CD, "CARDIO_GOALS", None) / getattr(CD, "MEDICATIONS", [])
    guarded code paths render nothing instead of crashing (Standing rule 2 /
    Phase 1 spec's backward-compatibility requirement).
    """
    mod = _base_fake_clinical_module()
    monkeypatch.setitem(sys.modules, "clinical_data", mod)
    return mod


def _seed_synthetic(db_path):
    rng = random.Random(_RNG_SEED)
    conn = sqlite3.connect(db_path)
    try:
        today = datetime.date.today()
        days = [today - datetime.timedelta(days=i) for i in range(90)]
        days.reverse()

        for d in days:
            ds = d.isoformat()
            total = rng.randint(360, 480)
            deep = int(total * rng.uniform(0.12, 0.22))
            rem = int(total * rng.uniform(0.15, 0.25))
            light = total - deep - rem - 10
            conn.execute(
                "INSERT OR REPLACE INTO sleep "
                "(date, total_minutes, deep_minutes, rem_minutes, light_minutes, "
                "awake_minutes, source) VALUES (?,?,?,?,?,?,?)",
                (ds, total, deep, rem, light, 10, "garmin"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO heart_rate (timestamp, bpm, context, source) "
                "VALUES (?,?,?,?)",
                (f"{ds}T06:00:00", rng.randint(48, 62), "resting", "garmin"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO wellness "
                "(date, hrv, hrv_sdnn, sleep_score, sleep_quality, avg_sleeping_hr, "
                "readiness, spo2, steps, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ds, rng.uniform(35, 75), rng.uniform(30, 70), rng.randint(60, 95),
                 rng.uniform(2, 5), rng.uniform(50, 60), rng.randint(50, 90),
                 rng.uniform(94, 99), rng.randint(4000, 15000), "garmin"),
            )
            if rng.random() < 0.3:
                conn.execute(
                    "INSERT OR REPLACE INTO activities "
                    "(date, type, duration_minutes, distance_km, avg_hr, max_hr, "
                    "calories, source, source_id, start_time) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ds, rng.choice(["cycling", "running"]), rng.uniform(30, 120),
                     rng.uniform(8, 40), rng.randint(120, 150), rng.randint(150, 180),
                     rng.randint(300, 900), "garmin", f"fake-act-{ds}",
                     f"{ds}T07:00:00"),
                )

        # Sparse cardio rows: 12 BP readings, 6 weigh-ins, 1 DEXA body_composition row.
        bp_days = sorted(rng.sample(days, 12))
        for d in bp_days:
            sys_bp = rng.randint(110, 135)
            dia_bp = rng.randint(70, 88)
            conn.execute(
                "INSERT OR REPLACE INTO blood_pressure "
                "(timestamp, systolic, diastolic, pulse, source, source_id, notes) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"{d.isoformat()}T08:00:00", sys_bp, dia_bp, rng.randint(55, 75),
                 "garmin", f"fake-bp-{d.isoformat()}", None),
            )

        weigh_days = sorted(rng.sample(days, 6))
        for d in weigh_days:
            conn.execute(
                "INSERT OR REPLACE INTO body_weight "
                "(timestamp, weight_kg, bmi, source, source_id) VALUES (?,?,?,?,?)",
                (f"{d.isoformat()}T07:30:00", rng.uniform(78, 86), rng.uniform(23, 27),
                 "garmin", f"fake-weight-{d.isoformat()}"),
            )

        dexa_date = days[len(days) // 2].isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO body_composition "
            "(timestamp, weight_kg, body_fat_pct, lean_mass_kg, fat_mass_kg, "
            "bone_mass_kg, visceral_fat_rating, visceral_fat_mass_kg, body_water_pct, "
            "note, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"{dexa_date}T00:00:00", 82.0, 22.5, 60.0, 18.5, 3.2, None, 1.1, None,
             "fake DEXA scan", "dexa"),
        )
        # nutrition_daily left empty on purpose (Phase 7 populates it).
        conn.commit()
    finally:
        conn.close()
