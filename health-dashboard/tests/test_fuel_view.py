"""Fuel page smoke tests: empty DB, a real (synthetic) plan run, and the ?page=fuel slug route."""
import sqlite3
from datetime import date

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from tests.test_fuel_refresh import SET, FakeGarmin


def _seed_run(db_path, monkeypatch):
    from nutrition import refresh as R
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO body_composition (timestamp, weight_kg, body_fat_pct, lean_mass_kg, bone_mass_kg, source)"
                 " VALUES ('2026-10-13T00:00:00', 80.0, 25.0, 57.0, 3.0, 'garmin')")
    conn.execute("INSERT INTO daily_energy (date, total_kcal, active_kcal, bmr_kcal, source) VALUES ('2026-10-12', 2600, 400, 2200, 'garmin')")
    conn.execute("INSERT INTO nutrition_daily (date, calories_kcal, sodium_mg, cholesterol_mg, source) VALUES (?, 900, 700, 150, 'mfp')",
                 (date.today().isoformat(),))
    conn.commit()
    conn.close()
    monkeypatch.setattr(R, "load_settings", lambda: {**SET, "horizon_end": "2026-12-31"})
    monkeypatch.setattr(R, "load_constraints", lambda: {"first_food_min_after_wake": 30})
    return R.run_refresh("test", collect=False, calendar=False, client=FakeGarmin(date.today()), today=date.today())


def _render(db_path, monkeypatch):
    import dashboard.fuel_view as fv
    from dashboard import lib
    monkeypatch.setattr(lib, "DB_PATH", db_path)
    st.cache_data.clear()
    fv.render_fuel()


def test_render_fuel_empty_db(empty_db, monkeypatch):
    _render(empty_db, monkeypatch)


def test_render_fuel_with_run(fake_db, monkeypatch):
    out = _seed_run(fake_db, monkeypatch)
    assert out["ok"]
    _render(fake_db, monkeypatch)


def test_request_refresh_sets_flag(empty_db, monkeypatch):
    import dashboard.fuel_view as fv
    from dashboard import lib
    from nutrition import store
    monkeypatch.setattr(lib, "DB_PATH", empty_db)
    fv.request_refresh()
    conn = sqlite3.connect(empty_db)
    try:
        assert store.get_state(conn, "refresh_requested") == 1
    finally:
        conn.close()


def test_app_slug_routes_to_fuel(fake_db, monkeypatch):
    import collectors.db as db
    from dashboard import lib
    _seed_run(fake_db, monkeypatch)
    monkeypatch.setattr(lib, "DB_PATH", fake_db)
    monkeypatch.setattr(db, "DB_PATH", fake_db)
    at = AppTest.from_file("dashboard/app.py", default_timeout=60)
    at.query_params["page"] = "fuel"
    at.run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs][:5] == ["Today", "Next 2 weeks", "Goals", "Last 2 weeks", "Groceries"]


def test_baseline_summary_averages_past_logged_days_only():
    import dashboard.fuel_view as fv
    intake = {"2026-10-12": {"calories_kcal": 2000, "protein_g": 100, "carbs_g": 250, "fat_g": 70, "sodium_mg": 2500,
                             "cholesterol_mg": 200},
              "2026-10-13": {"calories_kcal": 2400, "protein_g": 140, "carbs_g": 270, "fat_g": 90, "sodium_mg": 1800,
                             "cholesterol_mg": 400},
              "2026-10-14": {"calories_kcal": 900, "protein_g": 40, "carbs_g": 100, "fat_g": 30}}   # today: partial
    retro = [{"date": d, "kcal": 2500, "tdee": 2800, "protein_g": 170, "carb_g": 300, "fat_g": 80}
             for d in ("2026-10-12", "2026-10-13")]
    b = fv.baseline_summary(intake, retro, "2026-10-14", 80.0)
    assert b["n"] == 2 and b["kcal"] == 2200 and b["protein"] == 120 and b["protein_per_kg"] == 1.5
    assert b["days_over_sodium"] == 1 and b["chol"] == 300
    assert b["chol_limit"] is None and b["days_over_chol"] is None          # cholesterol is display-only by default
    limited = [{**r, "chol_max_mg": 300} for r in retro]
    assert fv.baseline_summary(intake, limited, "2026-10-14", 80.0)["days_over_chol"] == 1
    assert b["target"]["kcal"] == 2500 and sum(b["split_cpf"]) in (99, 100, 101)
    assert fv.baseline_summary({}, retro, "2026-10-14", 80.0) is None
