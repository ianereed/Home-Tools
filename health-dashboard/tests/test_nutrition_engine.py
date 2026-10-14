"""Smoke tests for the fuel-plan prototype engine (nutrition/engine.py)."""
import pytest

from nutrition import engine as E

KG = 80.0


def snap():
    return {
        "plan_start": "2026-10-14", "plan_days": 7,
        "athlete": {"sex": "male", "birth_date": "1980-01-01", "height_cm": 175.0, "ftp_w": 250, "lthr_bpm": 170},
        "dexa": {"total_mass_kg": 80.0, "fat_mass_kg": 20.0, "lean_mass_kg": 57.0, "bone_mass_kg": 3.0},
        "coach_plan": {"window_end": "2026-10-20", "race_day": "2026-11-20"},
        "coach_workouts": [
            {"date": "2026-10-14", "name": "Threshold", "type": "LACTATE_THRESHOLD", "duration_s": 3000,
             "steps": [[900, 144, 196], {"repeat": 2, "steps": [[480, 236, 275], [240, 118, 170]]}, [900, 118, 170]]},
            {"date": "2026-10-15", "rest": True, "type": "FORCED_REST"},
            {"date": "2026-10-16", "name": "Long Ride", "type": "LONG_WORKOUT", "duration_s": 7200, "steps": [[7200, 118, 170]]},
        ],
        "activities_prev_14d": [], "daily_stats_prev_14d": [{"date": "2026-10-13", "total": 2500}],
        "calendar": [{"date": "2026-10-14", "start": "09:30", "end": "11:00", "title": "Dentist", "busy": True}],
    }


def test_flatten_repeats_expand():
    flat = E.flatten_steps([{"repeat": 2, "steps": [[10, 100, 200], [20, 0, 100]]}])
    assert flat == [(10, 150), (20, 50), (10, 150), (20, 50)]


def test_threshold_kj_from_step_midpoints():
    s = E.ride_session(snap()["coach_workouts"][0], 250)
    assert s["kj"] == pytest.approx(597.0, abs=1)
    assert s["category"] == "key"


def test_ride_avoids_calendar_buffer():
    days = E.build_days(snap())
    E.schedule(days, KG)
    ride = days[0]["sessions"][0]
    # dentist 09:30-11:00 + 30 min buffer -> no ride overlapping 09:00-11:30
    assert ride["start"] >= E.tmin("11:30") or ride["end"] + E.SHOWER_MIN <= E.tmin("09:00")


def test_deficit_hits_target_and_long_day_untouched():
    s = snap()
    days = E.build_days(s)
    E.schedule(days, KG)
    rows = E.plan_energy(days, s, 0.5)
    assert sum(r["deficit"] for r in rows) == pytest.approx(1750, rel=0.05)
    long_day = next(r for r in rows if r["date"] == "2026-10-16")
    assert long_day["deficit"] == 0


def test_macro_floors_hold_at_aggressive_pace():
    s = snap()
    days = E.build_days(s)
    E.schedule(days, KG)
    for r in E.plan_energy(days, s, 2.0):
        assert r["fat_g"] >= 0.7 * KG - 0.5
        assert r["protein_g"] == pytest.approx(2.0 * KG)
        assert r["kcal"] == pytest.approx(4 * r["protein_g"] + 4 * r["carb_g"] + 9 * r["fat_g"], abs=1)


def test_actual_ride_uses_power_and_only_active_kcal():
    a = {"date": "2026-10-11", "start": "09:00", "name": "Road", "type": "road_biking", "duration_s": 7200,
         "moving_s": 7000, "kcal": 1500, "bmr_kcal": 180, "avg_power_w": 180, "np_w": 230, "avg_hr": 150}
    s = E.actual_session(a, 250, 1700)
    # 180 W x 7200 s = 1296 kJ -> /(0.217*4.184) = 1427 gross, minus RMR for 7000 s (138) = 1290 active
    expected = 180 * 7200 / 1000 / (0.217 * 4.184) - 1700 / 86400 * 7000
    assert s["active_kcal"] == pytest.approx(expected, abs=0.5)
    assert s["energy_source"] == "power meter"
    assert s["garmin_active_kcal"] == 1320
    assert s["category"] == "key"  # IF 0.92


def test_actual_ride_without_power_falls_back_to_garmin_active():
    a = {"date": "2026-10-09", "start": "12:00", "name": "Road", "type": "road_biking", "duration_s": 3600,
         "moving_s": 3500, "kcal": 500, "bmr_kcal": 90, "avg_power_w": None, "np_w": None, "avg_hr": 125}
    s = E.actual_session(a, 250, 1700)
    assert s["active_kcal"] == 410


def test_completed_ride_replaces_plan_for_that_day():
    s = snap()
    s["activities_plan_window"] = [{"date": "2026-10-14", "start": "10:05", "name": "Threshold", "type": "road_biking",
                                    "duration_s": 3100, "moving_s": 3080, "kcal": 700, "bmr_kcal": 80,
                                    "avg_power_w": 200, "np_w": 215, "avg_hr": 150}]
    days = E.build_days(s)
    E.schedule(days, KG)
    rides = [x for x in days[0]["sessions"] if x["kind"] == "ride"]
    assert len(rides) == 1 and rides[0]["completed"]
    rows = E.plan_energy(days, s, 0.5)
    assert rows[0]["actual"]
    rmr = E.baseline_kcal(s)["rmr"]
    assert rides[0]["active_kcal"] == pytest.approx(200 * 3100 / 1000 / (0.217 * 4.184) - rmr / 86400 * 3080, abs=1)
    lift = sum(x["active_kcal"] for x in days[0]["sessions"] if x["kind"] == "strength")
    assert rows[0]["ex_active"] == pytest.approx(rides[0]["active_kcal"] + lift, abs=1)


def test_no_personal_values_in_committed_nutrition_code():
    """Public repo guard: values from the gitignored local snapshot must never appear in committed code."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    snap_path = root / "nutrition" / "out" / "snapshot.json"
    if not snap_path.exists():
        pytest.skip("no local snapshot to compare against")
    real = json.loads(snap_path.read_text())
    needles = {real["athlete"]["birth_date"]}
    needles |= {f'{real["dexa"][k]}' for k in ("total_mass_kg", "fat_mass_kg", "lean_mass_kg", "tissue_fat_pct")}
    needles |= {e["title"] for e in real.get("calendar", []) if len(e["title"]) > 6}
    needles |= {e["location"] for e in real.get("calendar", []) if e.get("location")}
    files = [p for p in (root / "nutrition").glob("*.py") if p.name not in {"render.py", "build_prototype.py"}]
    files.append(Path(__file__))
    for p in files:
        text = p.read_text()
        hits = [n for n in needles if n in text]
        assert not hits, f"{p.name} contains personal values ({len(hits)} hits)"
