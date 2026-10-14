"""Phase 6: strength planning rules, Garmin workout JSON, and calendar reconciliation (dry run + fake live)."""
import sqlite3
from datetime import date, timedelta

import pytest

from nutrition import engine as E
from nutrition import garmin_sync as G
from nutrition import store, strength as ST

KG = 80.0
TODAY = date(2026, 10, 12)  # Monday


def _snap(workouts, days=14, calendar=None):
    return {"plan_start": TODAY.isoformat(), "plan_days": days,
            "athlete": {"birth_date": "1980-01-01", "height_cm": 175.0, "ftp_w": 250, "lthr_bpm": 170},
            "dexa": {"total_mass_kg": KG, "fat_mass_kg": 20.0, "lean_mass_kg": 57.0, "bone_mass_kg": 3.0},
            "coach_plan": {"window_end": (TODAY + timedelta(days=days)).isoformat()}, "calendar": calendar or [],
            "coach_workouts": workouts, "projected_workouts": []}


def _w(offset, wtype, minutes):
    return {"date": (TODAY + timedelta(days=offset)).isoformat(), "name": wtype.title(), "type": wtype,
            "duration_s": minutes * 60, "steps": [[minutes * 60, 150, 180]]}


WEEK = [_w(0, "BASE", 60), _w(1, "VO2MAX", 60), _w(2, "BASE", 75), _w(3, "LACTATE_THRESHOLD", 60),
        {"date": (TODAY + timedelta(days=4)).isoformat(), "rest": True, "type": "REST"},
        _w(5, "LONG_WORKOUT", 150), {"date": (TODAY + timedelta(days=6)).isoformat(), "rest": True, "type": "REST"}]


def _days(workouts, calendar=None, days=14):
    s = _snap(workouts, days, calendar)
    d = E.build_days(s)
    E.schedule(d, KG, E.config(s), place_lifts=False)
    return d, E.config(s)


def _plan(days, cfg, **kw):
    args = dict(today=TODAY, cfg=cfg, settings={}, completed=[], previous=[], load={}, readiness=None, progress={})
    args.update(kw)
    return ST.plan_lifts(days, **args)


def test_two_lifts_per_week_respecting_hard_constraints():
    days, cfg = _days(WEEK + [{**w, "date": (date.fromisoformat(w["date"]) + timedelta(days=7)).isoformat()} for w in WEEK])
    lifts = _plan(days, cfg)
    by_week = {}
    for l in lifts:
        by_week.setdefault(E.monday_of(l["date"]), []).append(l)
    assert all(len(v) == 2 for v in by_week.values())
    cats = {d["date"]: E.day_category(d) for d in days}
    for l in lifts:
        i = next(k for k, d in enumerate(days) if d["date"] == l["date"])
        assert cats[l["date"]] not in ("rest", "long")
        assert i + 1 >= len(days) or E.day_category(days[i + 1]) not in ("key", "long")
    dates = sorted(date.fromisoformat(l["date"]) for l in lifts)
    assert all((b - a).days >= 2 for a, b in zip(dates, dates[1:]))
    assert [l["template"] for l in lifts][:2] == ["A", "B"]


def test_previous_dates_are_sticky_and_keys_stable():
    days, cfg = _days(WEEK)
    first = _plan(days, cfg)
    second = _plan(days, cfg, previous=[{"date": l["date"], "plan_key": l["plan_key"], "template": l["template"]} for l in first])
    assert [(l["date"], l["plan_key"]) for l in first] == [(l["date"], l["plan_key"]) for l in second]
    assert all("kept from the previous plan" in l["reasons"] for l in second)


def test_pinned_date_is_kept_even_if_planner_would_not_choose_it():
    days, cfg = _days(WEEK)
    friday = (TODAY + timedelta(days=4)).isoformat()  # rest day: never chosen by the planner
    lifts = _plan(days, cfg, previous=[{"date": friday, "plan_key": "lift-x", "template": "A", "pinned": True}])
    assert any(l["date"] == friday and l["plan_key"] == "lift-x" for l in lifts)


def test_load_ratio_needs_three_weeks_of_history():
    short = {(TODAY - timedelta(days=i)).isoformat(): 100 for i in range(1, 10)}
    assert ST.acwr(short, TODAY) is None
    assert ST.daily_load([{"date": "2026-10-01", "duration_s": 3600}], 250) == {}   # pre-load-field rows: unknown


def test_high_load_ratio_drops_to_one_lift_and_light():
    days, cfg = _days(WEEK)
    load = {(TODAY - timedelta(days=i)).isoformat(): (150 if i <= 7 else 30) for i in range(1, 29)}
    lifts = _plan(days, cfg, load=load)
    assert len([l for l in lifts if E.monday_of(l["date"]) == TODAY.isoformat()]) == 1
    assert lifts[0]["variant"] == "light"


def test_completed_lifts_count_toward_the_week():
    days, cfg = _days(WEEK)
    lifts = _plan(days, cfg, completed=[{"date": TODAY.isoformat(), "template": "A"}])
    assert len(lifts) == 1 and lifts[0]["template"] == "B"


def test_prescription_levels_and_variants():
    lvl1 = ST.prescription("A", 1, "normal", False, False)
    lvl6 = ST.prescription("A", 6, "normal", False, False)
    light = ST.prescription("A", 4, "light", False, False)
    assert lvl1[0]["sets"] == 2 and lvl1[0]["reps"] == 10 and lvl6[0]["sets"] == 4 and lvl6[0]["reps"] == 5
    assert light[0]["sets"] == 3 and light[0]["rir"] == 3
    assert len(ST.prescription("B", 2, "normal", False, True)) == 6  # + upper-body pull once cleared


def test_garmin_json_shape():
    days, cfg = _days(WEEK)
    lift = _plan(days, cfg)[0]
    j = ST.to_garmin_json(lift)
    assert j["workoutName"].startswith("FP · ") and j["sportType"]["sportTypeKey"] == "strength_training"
    assert j["description"].startswith(f"fuel-planner:{lift['plan_key']}:{lift['spec_hash']}")
    groups = [s for s in j["workoutSegments"][0]["workoutSteps"] if s["type"] == "RepeatGroupDTO"]
    assert len(groups) == len(lift["exercises"])
    work = groups[0]["workoutSteps"][0]
    assert work["endCondition"]["conditionTypeId"] == 10 and work["category"] == "SQUAT"
    assert work["exerciseName"] == "GOBLET_SQUAT" and groups[0]["numberOfIterations"] == lift["exercises"][0]["sets"]
    orders = []
    def walk(steps):
        for s in steps:
            orders.append(s["stepOrder"])
            walk(s.get("workoutSteps", []))
    walk(j["workoutSegments"][0]["workoutSteps"])
    assert sorted(orders) == list(range(1, len(orders) + 1))  # unique, contiguous step orders


# ---------------- reconciliation ----------------

def _desired(key, ds, h="h1"):
    return {"plan_key": key, "kind": "lift", "date": ds, "spec_hash": h, "name": f"FP · Lift {key}", "minutes": 45,
            "template": "A", "level": 1, "variant": "normal", "deload": False, "upper": False,
            "exercises": ST.prescription("A", 1, "normal", False, False), "reasons": []}


def _L(key, ds, wid, sid, h="h1", status="synced"):
    return {"plan_key": key, "kind": "lift", "date": ds, "spec_hash": h, "name": "FP · x", "workout_id": wid,
            "schedule_id": sid, "status": status}


D1, D2, PAST = "2026-10-14", "2026-10-16", "2026-10-10"
END = date(2026, 10, 22)


def test_diff_scenarios():
    owned = {11: {"schedule_id": 11, "workout_id": 1, "date": D1}}
    led = {"k1": _L("k1", D1, 1, 11)}
    assert G.diff([_desired("k1", D1)], led, owned, TODAY, END) == []                                   # noop
    assert [o["op"] for o in G.diff([_desired("k2", D2)], {}, {}, TODAY, END)] == ["create"]             # fresh
    assert [o["op"] for o in G.diff([_desired("k1", D2)], led, owned, TODAY, END)] == ["move"]           # date change
    assert [o["op"] for o in G.diff([_desired("k1", D1, "h2")], led, owned, TODAY, END)] == ["replace"]  # content
    assert [o["op"] for o in G.diff([], led, owned, TODAY, END)] == ["remove"]                           # not wanted
    assert [o["op"] for o in G.diff([_desired("k1", D1)], led, {}, TODAY, END)] == ["mark_user_removed"]  # deleted by hand
    moved = {11: {"schedule_id": 11, "workout_id": 1, "date": D2}}
    assert [o["op"] for o in G.diff([_desired("k1", D1)], led, moved, TODAY, END)] == ["adopt_move"]    # moved by hand
    past = {"kp": _L("kp", PAST, 3, 33)}
    assert G.diff([], past, {}, TODAY, END) == []                                                          # history untouched
    late = [_desired("k9", "2026-11-30")]
    assert G.diff(late, {}, {}, TODAY, END) == []                                                          # outside window


def test_owned_items_exclude_coach_and_foreign_workouts():
    led = {"k1": _L("k1", D1, 1, 11)}
    items = [{"itemType": "workout", "id": 11, "workoutId": 1, "title": "FP · Lift A1", "date": D1},
             {"itemType": "fbtAdaptiveWorkout", "id": 12, "workoutId": 1, "title": "FP · fake", "date": D1},
             {"itemType": "workout", "id": 13, "workoutId": 1, "title": "My own workout", "date": D1},
             {"itemType": "workout", "id": 14, "workoutId": 99, "title": "FP · not ours", "date": D1}]
    assert list(G.owned_calendar_items(items, led)) == [11]


class FakeClient:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on, self.n = [], fail_on, 100

    def upload_workout(self, payload):
        self.calls.append(("upload", payload["workoutName"]))
        if self.fail_on == "upload":
            raise ConnectionError("API Error 429")
        self.n += 1
        return {"workoutId": self.n}

    def schedule_workout(self, wid, ds):
        self.calls.append(("schedule", wid, ds))
        return {"workoutScheduleId": wid * 10}

    def unschedule_workout(self, sid):
        self.calls.append(("unschedule", sid))
        return {}

    def delete_workout(self, wid):
        self.calls.append(("delete", wid))
        return {}


def test_apply_dry_run_touches_nothing(empty_db):
    conn = sqlite3.connect(empty_db)
    try:
        ops = G.diff([_desired("k1", D1)], {}, {}, TODAY, END)
        fc = FakeClient()
        res = G.apply(fc, conn, ops, "dry_run")
        assert res["done"] == 1 and fc.calls == []
        assert G.ledger(conn) == {}
        assert conn.execute("SELECT mode, op, ok FROM fuel_garmin_ops").fetchall() == [("dry_run", "create", 1)]
    finally:
        conn.close()


def test_apply_live_create_then_replace(empty_db, monkeypatch):
    monkeypatch.setattr(G, "PAUSE_S", 0)
    conn = sqlite3.connect(empty_db)
    try:
        fc = FakeClient()
        G.apply(fc, conn, G.diff([_desired("k1", D1)], {}, {}, TODAY, END), "live")
        L = G.ledger(conn)["k1"]
        assert L["status"] == "synced" and L["workout_id"] == 101 and L["schedule_id"] == 1010
        owned = {1010: {"schedule_id": 1010, "workout_id": 101, "date": D1}}
        ops = G.diff([_desired("k1", D1, "h2")], G.ledger(conn), owned, TODAY, END)
        G.apply(fc, conn, ops, "live")
        assert [c[0] for c in fc.calls] == ["upload", "schedule", "upload", "schedule", "unschedule", "delete"]
        assert G.ledger(conn)["k1"]["workout_id"] == 102
    finally:
        conn.close()


def test_apply_live_error_sets_backoff_and_stops(empty_db, monkeypatch):
    monkeypatch.setattr(G, "PAUSE_S", 0)
    conn = sqlite3.connect(empty_db)
    try:
        ops = G.diff([_desired("k1", D1), _desired("k2", D2)], {}, {}, TODAY, END)
        res = G.apply(FakeClient(fail_on="upload"), conn, ops, "live")
        assert res["stopped"] == "ConnectionError" and res["done"] == 0
        assert store.get_state(conn, "garmin_backoff_until") > 0
        assert G.apply(FakeClient(), conn, ops, "live")["stopped"] == "backoff"
    finally:
        conn.close()


def test_apply_write_cap(empty_db, monkeypatch):
    monkeypatch.setattr(G, "PAUSE_S", 0)
    monkeypatch.setattr(G, "MAX_WRITES", 3)
    conn = sqlite3.connect(empty_db)
    try:
        ops = G.diff([_desired(f"k{i}", D1) for i in range(4)], {}, {}, TODAY, END)
        res = G.apply(FakeClient(), conn, ops, "live")
        assert res["stopped"] == "write cap" and res["done"] == 1
    finally:
        conn.close()


def test_home_bodyweight_override_for_a_date():
    days, cfg = _days(WEEK)
    first = _plan(days, cfg)[0]
    lifts = _plan(days, cfg, settings={"lift_equipment_by_date": {first["date"]: "home"}, "upper_body_cleared": True})
    home = next(l for l in lifts if l["date"] == first["date"])
    assert home["equipment"] == "home" and "Home bodyweight" in home["name"]
    names = [x["exercise"] for x in home["exercises"]]
    assert "PUSH_UP" in names and "AIR_SQUAT" in names
    assert all(x["sets"] >= 2 and (x["reps"] or x["seconds"]) for x in home["exercises"])
    j = ST.to_garmin_json(home)
    cats = {s["workoutSteps"][0]["category"] for s in j["workoutSegments"][0]["workoutSteps"] if s["type"] == "RepeatGroupDTO"}
    assert {"SQUAT", "PUSH_UP", "LUNGE", "HIP_RAISE"} <= cats
    other = [l for l in lifts if l["date"] != first["date"]]
    assert all(l["equipment"] == "gym" for l in other)
    assert any(x["exercise"] == "DUMBBELL_BENCH_PRESS" for l in other if l["template"] == "A" for x in l["exercises"]) or \
        any(x["exercise"] == "DUMBBELL_ROW" for l in other for x in l["exercises"])
