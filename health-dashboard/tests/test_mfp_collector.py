"""MyFitnessPal collector: report/diary merge, upsert semantics, subprocess error handling."""
import json
import sqlite3
import subprocess

import pytest

from collectors import mfp_collector as M
from collectors import mfp_fetch as F
from nutrition.intake import intake_by_date


def test_build_days_unlogged_days_are_not_zero_days():
    reports = {"Calories": {"2026-10-13": 0, "2026-10-14": 258}, "Sodium": {"2026-10-13": 0, "2026-10-14": 370},
               "Potassium": {"2026-10-13": 0, "2026-10-14": 0}, "Cholesterol": {"2026-10-14": 120}}
    days = F.build_days(reports, {"2026-10-13": 0, "2026-10-14": 2})
    assert days["2026-10-13"] == {"logged": False, "entries": 0}
    d = days["2026-10-14"]
    assert d["calories_kcal"] == 258 and d["sodium_mg"] == 370 and d["cholesterol_mg"] == 120
    assert d["potassium_mg"] is None          # 0 potassium = foods lack the data
    assert d["fiber_g"] is None               # report missing entirely -> unknown


def test_upsert_writes_logged_and_removes_unlogged(empty_db):
    conn = sqlite3.connect(empty_db)
    try:
        conn.execute("INSERT INTO nutrition_daily (date, calories_kcal, source) VALUES ('2026-10-12', 999, 'mfp')")
        days = {"2026-10-12": {"logged": False, "entries": 0},
                "2026-10-14": {"logged": True, "entries": 2, "calories_kcal": 258.0, "sodium_mg": 370.0}}
        assert M.upsert(conn, days, now="2026-10-14T12:00:00") == (1, 1)
        rows = conn.execute("SELECT date, calories_kcal, sodium_mg, protein_g, updated_at FROM nutrition_daily "
                            "WHERE source='mfp'").fetchall()
    finally:
        conn.close()
    assert rows == [("2026-10-14", 258.0, 370.0, None, "2026-10-14T12:00:00")]


def _fake_run(stdout, rc=0):
    def run(*a, **k):
        return subprocess.CompletedProcess(a, rc, stdout=stdout, stderr="")
    return run


def test_fetch_auth_error_raises_mfp_auth_error(monkeypatch):
    monkeypatch.setattr(M.subprocess, "run", _fake_run(json.dumps({"ok": False, "error": "expired", "auth": True}), 2))
    with pytest.raises(M.MFPAuthError):
        M.fetch("2026-10-13", "2026-10-14")


def test_fetch_ignores_log_noise_before_json(monkeypatch):
    out = "WARNING something\n" + json.dumps({"ok": True, "days": {}, "report_errors": {}})
    monkeypatch.setattr(M.subprocess, "run", _fake_run(out))
    assert M.fetch("2026-10-13", "2026-10-14")["ok"]


def test_collect_mfp_noop_without_venv(monkeypatch, tmp_path):
    monkeypatch.setenv("FUEL_MFP_PYTHON", str(tmp_path / "missing" / "python"))
    monkeypatch.setattr(M.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    M.collect_mfp(3)


def test_intake_precedence(empty_db):
    conn = sqlite3.connect(empty_db)
    try:
        conn.executemany("INSERT INTO nutrition_daily (date, calories_kcal, sodium_mg, source) VALUES (?,?,?,?)",
                         [("2026-10-14", 2000, 1500, "garmin"), ("2026-10-14", 2100, 1600, "mfp"),
                          ("2026-10-13", 1900, 1400, "garmin")])
        conn.execute("INSERT INTO daily_energy (date, total_kcal, consumed_kcal, source) VALUES "
                     "('2026-10-12', 2500, 1800, 'garmin')")
        got = intake_by_date(conn, "2026-10-12", "2026-10-14")
    finally:
        conn.close()
    assert got["2026-10-14"]["source"] == "mfp" and got["2026-10-14"]["calories_kcal"] == 2100
    assert got["2026-10-13"]["source"] == "garmin"
    assert got["2026-10-12"] == {**got["2026-10-12"], "source": "garmin_consumed", "calories_kcal": 1800, "sodium_mg": None}


def test_goal_payload_sets_macros_and_only_existing_extras():
    from collectors import mfp_goal as MG
    cur = {"items": [{"valid_from": "2022-03-01", "valid_to": "2100-01-01", "default_group_id": 1, "updated_at": "x",
                      "default_goal": {"energy": {"value": 1910, "unit": "calories"}, "carbohydrates": 200, "protein": 100,
                                       "fat": 60, "sodium": 2300, "fiber": 30, "meal_goals": [{"x": 1}], "group_id": 0},
                      "daily_goals": [{"day_of_week": "monday", "energy": {"value": 1910, "unit": "calories"},
                                       "carbohydrates": 200, "protein": 100, "fat": 60, "sodium": 2300, "group_id": 0}]}]}
    p = MG.build_payload(cur, "2026-10-15", 2701.4, 330.2, 169.6, 70.1, {"sodium": 2300, "cholesterol": 300, "fiber": 38})
    it = p["item"]
    assert it["valid_from"] == "2026-10-15" and "valid_to" not in it and "default_group_id" not in it
    g = it["default_goal"]
    assert g["energy"] == {"value": 2701, "unit": "calories"} and (g["carbohydrates"], g["protein"], g["fat"]) == (330, 170, 70)
    assert g["sodium"] == 2300 and g["fiber"] == 38 and "cholesterol" not in g and g["meal_goals"] == []
    assert it["daily_goals"][0]["energy"]["value"] == 2701 and "group_id" not in it["daily_goals"][0]


def test_push_goal_unchanged_is_not_resent(empty_db, monkeypatch):
    from nutrition import refresh as R
    from nutrition import store
    calls = []
    monkeypatch.setattr(M, "push_goal", lambda **g: calls.append(g) or {"ok": True})
    res = {"plans": {"plan": {"energy": [{"date": "2026-10-15", "kcal": 2700, "carb_g": 330, "protein_g": 170, "fat_g": 70,
                                          "sodium_max_mg": 2300, "chol_max_mg": 300, "fiber_target": 38}]}}}
    conn = sqlite3.connect(empty_db)
    try:
        from datetime import date
        assert R.push_mfp_goal(conn, res, date(2026, 10, 15)) == "pushed"
        assert R.push_mfp_goal(conn, res, date(2026, 10, 15)) == "unchanged"
        res["plans"]["plan"]["energy"][0]["kcal"] = 2900
        assert R.push_mfp_goal(conn, res, date(2026, 10, 15)) == "pushed"
        assert store.get_state(conn, "mfp_goal_pushed")["kcal"] == 2900
    finally:
        conn.close()
    assert len(calls) == 2


def test_push_goal_auth_expired_reported(empty_db, monkeypatch):
    from datetime import date
    from nutrition import refresh as R

    def boom(**g):
        raise M.MFPAuthError("expired")
    monkeypatch.setattr(M, "push_goal", boom)
    res = {"plans": {"plan": {"energy": [{"date": "2026-10-15", "kcal": 2700, "carb_g": 330, "protein_g": 170, "fat_g": 70}]}}}
    conn = sqlite3.connect(empty_db)
    try:
        assert R.push_mfp_goal(conn, res, date(2026, 10, 15)) == "auth_expired"
    finally:
        conn.close()


def test_programming_errors_are_not_reported_as_expired_login():
    from collectors import mfp_goal as MG
    always = lambda e: True  # mfp-mcp's is_auth_error is broad
    assert MG._is_auth(TypeError("x"), always) is False
    assert MG._is_auth(PermissionError("403"), lambda e: False) is True
