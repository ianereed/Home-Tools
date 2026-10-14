"""Phase 7: planner-generated rides become Garmin cycling workouts only when no Coach plan covers them."""
from datetime import date

from nutrition import engine as E
from nutrition import garmin_sync as G
from nutrition import projection, rides


def _snap(projected, coach_workouts=None):
    return {"plan_start": "2026-11-21", "plan_days": 7,
            "athlete": {"birth_date": "1980-01-01", "height_cm": 175.0, "ftp_w": 250, "lthr_bpm": 170},
            "dexa": {"total_mass_kg": 80.0, "fat_mass_kg": 20.0, "lean_mass_kg": 57.0, "bone_mass_kg": 3.0},
            "coach_plan": {"window_end": None}, "calendar": [], "coach_workouts": coach_workouts or [],
            "projected_workouts": projected}


def test_generated_days_become_ride_specs_and_valid_json():
    gen = projection.project(date(2026, 11, 21), date(2026, 11, 27), coach_window_end=None, plan_end=None,
                             coach_active=False, versions=[], athlete={"ftp_w": 250})
    days = E.build_days(_snap(gen))
    specs = rides.desired_rides(days)
    assert specs and all(s["plan_key"].startswith("ride-") and s["name"].startswith("FP · ") for s in specs)
    thr = next(s for s in specs if s["type"] == "LACTATE_THRESHOLD")
    j = rides.to_garmin_json(thr)
    assert j["sportType"]["sportTypeKey"] == "cycling"
    assert j["description"].startswith(f"fuel-planner:{thr['plan_key']}:{thr['spec_hash']}")
    grp = next(s for s in j["workoutSegments"][0]["workoutSteps"] if s["type"] == "RepeatGroupDTO")
    work = grp["workoutSteps"][0]
    assert work["targetType"]["workoutTargetTypeKey"] == "power.zone" and work["targetValueTwo"] == 250.0
    orders = []
    def walk(steps):
        for s in steps:
            orders.append(s["stepOrder"])
            walk(s.get("workoutSteps", []))
    walk(j["workoutSegments"][0]["workoutSteps"])
    assert sorted(orders) == list(range(1, len(orders) + 1))


def test_coach_and_projected_days_are_never_pushed():
    proj = [{"date": "2026-11-22", "name": "Base", "type": "BASE", "duration_s": 3600, "steps": [[3600, 150, 180]],
             "source": "projected"}]
    coach = [{"date": "2026-11-21", "name": "Threshold", "type": "LACTATE_THRESHOLD", "duration_s": 3000,
              "steps": [[3000, 200, 240]]}]
    days = E.build_days(_snap(proj, coach))
    assert rides.desired_rides(days) == []


def test_new_coach_plan_removes_previously_synced_generated_rides():
    led = {"ride-2026-11-23": {"plan_key": "ride-2026-11-23", "kind": "ride", "date": "2026-11-23", "spec_hash": "x",
                               "name": "FP · Base", "workout_id": 7, "schedule_id": 70, "status": "synced"}}
    owned = {70: {"schedule_id": 70, "workout_id": 7, "date": "2026-11-23"}}
    ops = G.diff([], led, owned, date(2026, 11, 21), date(2026, 12, 1))   # Coach took over: nothing generated wanted
    assert [o["op"] for o in ops] == ["remove"]
