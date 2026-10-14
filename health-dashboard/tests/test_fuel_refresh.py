"""Phase 3: calendar classification, Coach normalization, persistence, snapshot assembly, refresh + watch."""
import fcntl
import json
import sqlite3
from datetime import date, timedelta

import pytest

from nutrition import calendar_source as CS
from nutrition import coach as C
from nutrition import refresh as R
from nutrition import snapshot as S
from nutrition import store
from nutrition import watch as W
from nutrition.settings import DEFAULTS

SET = {**DEFAULTS, "calendar_ids": ["cal"], "horizon_end": "2026-11-01"}


# ---------------- calendar ----------------

def _ev(summary, start, end, **kw):
    return {"summary": summary, "start": start, "end": end, "all_day": "T" not in start, "transparency": "opaque",
            "status": "confirmed", **kw}


def test_classify_splits_multiday_trip_and_drops_declined():
    raw = [_ev("Bike packing weekend", "2026-10-24T06:00:00-07:00", "2026-10-25T18:00:00-07:00"),
           _ev("Team sync", "2026-10-22T11:45:00-07:00", "2026-10-22T12:15:00-07:00", self_status="declined")]
    ev = CS.classify(raw, SET)
    assert [(e["date"], e["start"], e["end"], e["trip"]) for e in ev] == [
        ("2026-10-24", "06:00", "23:59", True), ("2026-10-25", "00:00", "18:00", True)]


def test_classify_meal_out_packed_lunch_workout_and_transparent_all_day():
    raw = [_ev("Watch party", "2026-10-22T18:00:00-07:00", "2026-10-22T21:00:00-07:00", location="Somewhere"),
           _ev("Long appointment", "2026-10-19T08:00:00-07:00", "2026-10-19T15:00:00-07:00"),
           _ev("Group Ride: shop", "2026-10-18T09:00:00-07:00", "2026-10-18T10:00:00-07:00"),
           _ev("Day off", "2026-10-20", "2026-10-21", transparency="transparent")]
    by = {e["title"]: e for e in CS.classify(raw, SET)}
    assert by["Watch party"]["meal_out"] == "dinner"
    assert by["Long appointment"]["packed_lunch"] is True
    assert by["Group Ride: shop"]["workout"] is True
    assert by["Day off"]["all_day"] and not by["Day off"]["busy"]


def test_trip_sessions_from_rules():
    ev = CS.classify([_ev("Bike packing weekend", "2026-10-24T06:00:00-07:00", "2026-10-25T18:00:00-07:00")], SET)
    ts = CS.trip_sessions(ev, SET)
    assert [t["date"] for t in ts] == ["2026-10-24", "2026-10-25"]
    assert ts[0]["type"] == "BIKEPACKING" and ts[0]["duration_s"] == 5 * 3600


# ---------------- coach ----------------

DETAIL = {"estimatedTrainingEffect": 3.5, "workoutSegments": [{"workoutSteps": [
    {"type": "ExecutableStepDTO", "stepOrder": 1, "endCondition": {"conditionTypeKey": "time"}, "endConditionValue": 900.0,
     "targetType": {"workoutTargetTypeKey": "power.zone"}, "targetValueOne": 140.0, "targetValueTwo": 190.0},
    {"type": "RepeatGroupDTO", "stepOrder": 4, "numberOfIterations": 2, "workoutSteps": [
        {"type": "ExecutableStepDTO", "stepOrder": 2, "endCondition": {"conditionTypeKey": "time"}, "endConditionValue": 480.0,
         "targetType": {"workoutTargetTypeKey": "power.zone"}, "targetValueOne": 230.0, "targetValueTwo": 270.0},
        {"type": "ExecutableStepDTO", "stepOrder": 3, "endCondition": {"conditionTypeKey": "time"}, "endConditionValue": 240.0,
         "targetType": {"workoutTargetTypeKey": "power.zone"}, "targetValueOne": 110.0, "targetValueTwo": 160.0}]},
    {"type": "ExecutableStepDTO", "stepOrder": 5, "endCondition": {"conditionTypeKey": "time"}, "endConditionValue": 600.0,
     "targetType": {"workoutTargetTypeKey": "no.target"}}]}]}


def _task(ds, name="Threshold", phrase="LACTATE_THRESHOLD", rest=False, uuid="u1"):
    return {"calendarDate": ds, "keyWorkout": True, "longWkt": False,
            "taskWorkout": {"workoutName": name, "workoutPhrase": phrase, "workoutUuid": uuid, "restDay": rest,
                            "estimatedDurationInSecs": 3000, "workoutDescription": "2x8"}}


def test_coach_normalize_steps_and_rest():
    w = C.normalize(_task("2026-10-15"), DETAIL)
    assert w["type"] == "LACTATE_THRESHOLD" and w["te"] == 3.5
    assert w["steps"] == [[900.0, 140.0, 190.0], {"repeat": 2, "steps": [[480.0, 230.0, 270.0], [240.0, 110.0, 160.0]]},
                          [600.0, 0.0, 0.0]]
    r = C.normalize(_task("2026-10-16", phrase="FORCED_REST", rest=True), None)
    assert r == {"date": "2026-10-16", "rest": True, "type": "FORCED_REST", "uuid": "u1"}


# ---------------- store ----------------

def test_save_and_prune_runs(empty_db):
    conn = sqlite3.connect(empty_db)
    try:
        for i in range(5):
            payload = {"engine_version": "3.0", "plan_end": "2026-12-31",
                       "plans": {"plan": {"energy": [{"date": "2026-10-14", "category": "rest", "source": "coach", "tdee": 2400,
                                                      "kcal": 2100, "protein_g": 160, "carb_g": 200, "fat_g": 70,
                                                      "deficit": 300}]}}}
            rid = store.save_run(conn, trigger="t", reason=str(i), payload=payload, snapshot={"plan_start": "2026-10-14"},
                                 status="ok")
        assert store.latest_run(conn)["id"] == rid
        assert conn.execute("SELECT COUNT(*) FROM fuel_plan_days").fetchone()[0] == 5
        store.prune_runs(conn, keep_last=2, keep_daily_days=0)
        assert conn.execute("SELECT COUNT(*) FROM fuel_plan_runs").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM fuel_plan_days").fetchone()[0] == 2
    finally:
        conn.close()


def test_coach_window_dedupe_and_diffs(empty_db):
    conn = sqlite3.connect(empty_db)
    try:
        w1 = [C.normalize(_task("2026-10-15", uuid="a"), DETAIL), C.normalize(_task("2026-10-16", rest=True, uuid="b"), None)]
        assert store.save_coach_window(conn, {"id": 1}, w1) == {"new_snapshot": True, "diffs": 0}
        assert store.save_coach_window(conn, {"id": 1}, w1) == {"new_snapshot": False, "diffs": 0}
        # Coach rolls forward one day and changes 10-16 from rest to a base ride
        w2 = [C.normalize(_task("2026-10-16", name="Base", phrase="BASE", uuid="c"), DETAIL),
              C.normalize(_task("2026-10-17", name="Long", phrase="LONG_WORKOUT", uuid="d"), DETAIL)]
        out = store.save_coach_window(conn, {"id": 1}, w2, context={"lift_48h": True})
        diffs = conn.execute("SELECT date, change, context_json FROM coach_diffs").fetchall()
    finally:
        conn.close()
    assert out == {"new_snapshot": True, "diffs": 1}           # 10-15 rolled out, 10-17 rolled in: not diffs
    assert diffs[0][:2] == ("2026-10-16", "changed") and json.loads(diffs[0][2]) == {"lift_48h": True}


# ---------------- snapshot ----------------

def test_db_body_derives_fat_from_garmin_row(empty_db):
    conn = sqlite3.connect(empty_db)
    try:
        conn.execute("INSERT INTO body_composition (timestamp, weight_kg, body_fat_pct, lean_mass_kg, bone_mass_kg, source)"
                     " VALUES ('2026-10-13T00:00:00', 80.0, 25.0, 57.0, 3.0, 'garmin')")
        dexa, weight = S.db_body(conn)
    finally:
        conn.close()
    assert dexa["fat_mass_kg"] == pytest.approx(20.0) and dexa["source"] == "garmin"
    assert weight is None


def test_earliest_food_from_wake_and_gap():
    assert S.earliest_food({"wake_s": 8 * 3600}, {"first_food_min_after_wake": 30}) == "08:30"
    assert S.earliest_food({"wake_s": 7 * 3600}, {}) == "07:00"
    assert S.earliest_food({}, {"first_food_min_after_wake": 30}) is None


# ---------------- refresh end to end (fake Garmin) ----------------

class FakeGarmin:
    def __init__(self, today):
        self.today = today

    def connectapi(self, path):
        if path.endswith("user-settings"):
            return {"userData": {"gender": "MALE", "birthDate": "1980-01-01", "height": 175.0,
                                 "lactateThresholdHeartRate": 170, "availableTrainingDays": ["MONDAY", "TUESDAY", "WEDNESDAY",
                                 "THURSDAY", "FRIDAY", "SATURDAY"], "preferredLongTrainingDays": ["SATURDAY"]},
                    "userSleep": {"wakeTime": 8 * 3600}}
        if "/fbt-adaptive/" in path:
            return DETAIL
        raise AssertionError(path)

    def get_cycling_ftp(self):
        return {"functionalThresholdPower": 250}

    def get_training_plans(self):
        end = (self.today + timedelta(days=20)).isoformat()
        return {"trainingPlanList": [{"trainingPlanId": 7, "name": "Plan", "trainingPlanCategory": "FBT_ADAPTIVE",
                                      "trainingStatus": {"statusKey": "Scheduled"}, "startDate": "2026-10-01T00:00:00.0",
                                      "endDate": end + "T00:00:00.0"}]}

    def get_adaptive_training_plan_by_id(self, pid):
        tl = []
        for i in range(7):
            ds = (self.today + timedelta(days=i)).isoformat()
            tl.append(_task(ds, uuid=f"w{i}", rest=(i % 3 == 2)))
        return {"taskList": tl}


def test_run_refresh_end_to_end(fake_db, monkeypatch, tmp_path):
    today = date(2026, 10, 14)
    conn = sqlite3.connect(fake_db)
    conn.execute("INSERT INTO body_composition (timestamp, weight_kg, body_fat_pct, lean_mass_kg, bone_mass_kg, source)"
                 " VALUES ('2026-10-13T00:00:00', 80.0, 25.0, 57.0, 3.0, 'garmin')")
    conn.execute("INSERT INTO daily_energy (date, total_kcal, active_kcal, bmr_kcal, source) VALUES ('2026-10-12', 2600, 400, 2200, 'garmin')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(R, "load_settings", lambda: {**SET, "horizon_end": "2026-11-01"})
    monkeypatch.setattr(R, "load_constraints", lambda: {"first_food_min_after_wake": 30})
    out = R.run_refresh("test", collect=False, calendar=False, client=FakeGarmin(today), today=today)
    assert out["ok"] and out["plan_end"] == "2026-11-01"
    assert out["sources"]["coach"] == "ok"
    conn = sqlite3.connect(fake_db)
    try:
        run = store.latest_run(conn)
        n_snap = conn.execute("SELECT COUNT(*) FROM coach_snapshots").fetchone()[0]
    finally:
        conn.close()
    p = run["payload"]
    assert p["config"]["earliest_food"] == "08:30"
    assert "plan" in p["plans"] and "meals" in p["plans"]["plan"] and "meals" not in p["plans"]["0.75"] \
        if "0.75" in p["plans"] else True
    srcs = {d["source"] for d in p["days"]}
    assert {"projected", "generated"} & srcs                      # beyond Coach's 7-day window
    assert n_snap == 1


# ---------------- watch + lock ----------------

def test_watch_check(empty_db):
    conn = sqlite3.connect(empty_db)
    try:
        assert W.check(conn, "1")["changed"]
        store.set_state(conn, "last_refreshed_activity_id", "1")
        assert not W.check(conn, "1")["changed"]
        store.set_state(conn, "refresh_requested", 1)
        assert W.check(conn, "1")["reason"].startswith("refresh requested")
        store.set_state(conn, "refresh_enqueued_at", 1e12)
        assert not W.check(conn, "2", now=1e12 + 60)["changed"]   # debounced
    finally:
        conn.close()


def test_refresh_lock_requests_rerun(empty_db, monkeypatch, tmp_path, capsys):
    lock = tmp_path / "fuel.lock"
    monkeypatch.setattr(R, "LOCK_PATH", lock)
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert R.main(["--reason", "x"]) == 0
    assert "rerun requested" in capsys.readouterr().out
    conn = sqlite3.connect(empty_db)
    try:
        assert store.get_state(conn, "rerun_requested") == 1
    finally:
        conn.close()
