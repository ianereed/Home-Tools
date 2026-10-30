"""Your own Garmin cycling workouts in the fuel plan: calendar filter, normalization, Coach de-duplication."""
from datetime import date, timedelta

from nutrition import engine as E
from nutrition import garmin_rides as GR
from nutrition import snapshot as S

FTP = 230
TODAY = date(2026, 11, 2)
ATHLETE = {"sex": "male", "birth_date": "1991-07-13", "height_cm": 180.0, "ftp_w": FTP, "lthr_bpm": 170,
           "available_days": None, "long_days": None}
DEXA = {"total_mass_kg": 84.6, "fat_mass_kg": 24.6, "lean_mass_kg": 56.8, "bone_mass_kg": 3.2}


def _item(d, sid, title, sport="cycling", kind="workout", wid=1):
    return {"itemType": kind, "id": sid, "date": d, "title": title, "workoutId": wid, "sportTypeKey": sport}


def _detail(name, steps, desc=""):
    return {"workoutName": name, "description": desc, "estimatedDurationInSecs": 3600,
            "workoutSegments": [{"workoutSteps": steps}]}


def _step(order, secs, lo=None, hi=None):
    s = {"type": "ExecutableStepDTO", "stepOrder": order, "endCondition": {"conditionTypeKey": "time"},
         "endConditionValue": float(secs)}
    if lo is not None:
        s |= {"targetType": {"workoutTargetTypeKey": "power.zone"}, "targetValueOne": lo, "targetValueTwo": hi}
    return s


def _repeat(order, n, steps):
    return {"type": "RepeatGroupDTO", "stepOrder": order, "numberOfIterations": n, "workoutSteps": steps}


SPRINT = _detail("Anaerobic+", [_step(1, 900, 139, 190),
                                _repeat(2, 6, [_step(1, 30, 410, 455), _step(2, 300, 114, 165)]),
                                _step(3, 900, 110, 150)])


def test_calendar_rides_keeps_only_your_cycling_workouts_in_window():
    items = [_item("2026-11-02", 1, "FTP test", wid=11), _item("2026-11-02", 2, "FP · Z2 60 min", wid=12),
             _item("2026-11-02", 3, "Lift", sport="strength_training", wid=13),
             {"itemType": "fbtAdaptiveWorkout", "id": 4, "date": "2026-11-02", "title": "Sprint", "workoutId": None,
              "sportTypeKey": "cycling"},
             _item("2026-11-01", 5, "past", wid=15), _item("2026-12-31", 6, "far", wid=16),
             {"itemType": "activity", "id": 7, "date": "2026-11-02", "title": "ride"}]
    out = GR.calendar_rides(items, TODAY, TODAY + timedelta(days=20))
    assert [r["workout_id"] for r in out] == [11]


def test_normalize_infers_type_and_fills_untargeted_steps():
    w = GR.normalize({"date": "2026-11-02", "workout_id": 9, "title": "x"}, SPRINT, FTP)
    assert w["type"] == "ANAEROBIC_SPEED" and w["duration_s"] == 900 + 6 * 330 + 900
    hr_only = GR.normalize({"date": "2026-11-02", "workout_id": 9}, _detail("Dog ride", [_step(1, 3600)]), FTP)
    assert hr_only["steps"] == [[3600.0, round(0.55 * FTP), round(0.65 * FTP)]] and hr_only["type"] == "BASE"
    thr = _detail("2x20", [_step(1, 600, 140, 180), _repeat(2, 2, [_step(1, 1200, 210, 225), _step(2, 300, 120, 150)])])
    assert GR.normalize({"date": "2026-11-02", "workout_id": 9}, thr, FTP)["type"] == "LACTATE_THRESHOLD"
    long = _detail("Long", [_step(1, 4 * 3600, 130, 160)])
    assert GR.normalize({"date": "2026-11-02", "workout_id": 9}, long, FTP)["type"] == "LONG_WORKOUT"


def _snap(coach=(), mine=(), days=7):
    return {"plan_start": TODAY.isoformat(), "plan_days": days,
            "athlete": ATHLETE, "dexa": DEXA, "coach_workouts": list(coach), "garmin_rides": list(mine),
            "coach_plan": {"window_end": (TODAY + timedelta(days=days - 1)).isoformat()}, "calendar": [],
            "projected_workouts": []}


def _coach(d, name, secs, w):
    return {"date": d, "name": name, "type": "BASE", "duration_s": secs, "avg_power_w": w}


def _mine(d, name, secs, w):
    return GR.normalize({"date": d, "workout_id": 1}, _detail(name, [_step(1, secs, w - 10, w + 10)]), FTP)


def test_harder_of_coach_and_yours_wins_and_yours_add_up():
    d0, d1, d2 = ((TODAY + timedelta(days=i)).isoformat() for i in range(3))
    days = {d["date"]: d for d in E.build_days(_snap(
        coach=[_coach(d0, "Sprint", 3240, 150), _coach(d1, "Base", 7200, 150), _coach(d2, "Base", 5400, 140)],
        mine=[_mine(d0, "FTP test", 3600, 190), _mine(d1, "Dog ride", 3600, 130),
              _mine(d2, "Dog ride", 3600, 140), _mine(d2, "Threshold", 4500, 200)]))}
    assert [s["name"] for s in days[d0]["sessions"]] == ["FTP test"] and days[d0]["source"] == "garmin"
    assert [s["name"] for s in days[d1]["sessions"]] == ["Base"] and "planning around Coach's" in days[d1]["notes"][0]["text"]
    assert [s["name"] for s in days[d2]["sessions"]] == ["Dog ride", "Threshold"]


def test_your_last_ride_extends_the_planned_window():
    rides = [_mine(TODAY.isoformat(), "Z2", 3600, 140), _mine((TODAY + timedelta(days=3)).isoformat(), "Z2", 3600, 140)]
    snap = S.assemble(today=TODAY, settings={"horizon_end": (TODAY + timedelta(days=20)).isoformat()}, constraints={},
                      athlete=ATHLETE, dexa=DEXA, weight_kg=None,
                      coach_plan=None, coach_workouts=[], coach_versions=[], activities=[], daily_stats=[], intake={},
                      calendar_events=[], garmin_rides=rides)
    assert snap["coach_plan"]["window_end"] == (TODAY + timedelta(days=3)).isoformat()
    assert min(w["date"] for w in snap["projected_workouts"]) == (TODAY + timedelta(days=4)).isoformat()
    days = {d["date"]: d for d in E.build_days(snap)}
    gap = (TODAY + timedelta(days=1)).isoformat()
    assert days[gap]["sessions"] == [] and days[gap]["source"] == "rest"
