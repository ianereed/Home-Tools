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
    lifts = _plan(days, cfg, load=load, progress={"A": 4, "B": 4})  # established lifter: no soreness guard
    assert len([l for l in lifts if E.monday_of(l["date"]) == TODAY.isoformat()]) == 1
    assert lifts[0]["variant"] == "light"


def test_completed_lifts_count_toward_the_week():
    days, cfg = _days(WEEK)
    lifts = _plan(days, cfg, completed=[{"date": TODAY.isoformat(), "template": "A"}])
    week1 = [l for l in lifts if E.monday_of(l["date"]) == TODAY.isoformat()]
    assert len(week1) == 1 and week1[0]["template"] == "B"


def test_prescription_levels_and_variants():
    lvl1 = ST.prescription("A", 1, "normal", False, False)
    lvl6 = ST.prescription("A", 6, "normal", False, False)
    light = ST.prescription("A", 4, "light", False, False)
    assert lvl1[0]["sets"] == 2 and lvl1[0]["reps"] == 10 and lvl6[0]["sets"] == 4 and lvl6[0]["reps"] == 5
    assert light[0]["sets"] == 3 and light[0]["rir"] == 3
    assert len(ST.prescription("B", 2, "normal", False, True)) == 9  # 5 + hip block (3) + upper-body pull


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
    assert any(x["exercise"] == "BARBELL_BENCH_PRESS" for l in other if l["template"] == "A" for x in l["exercises"]) or \
        any(x["exercise"] == "DUMBBELL_ROW" for l in other for x in l["exercises"])


# ---------------- intro level, projected progression, soreness guard ----------------

def test_levels_start_with_an_intro_and_continue_through_the_horizon():
    days, cfg = _days(WEEK * 1 + [{**w, "date": (date.fromisoformat(w["date"]) + timedelta(days=7 * k)).isoformat()}
                                  for k in (1, 2, 3) for w in WEEK], days=28)
    lifts = _plan(days, cfg)
    seq = [(l["template"], l["level"]) for l in lifts]
    assert seq[:6] == [("A", 0), ("B", 0), ("A", 1), ("B", 1), ("A", 1), ("B", 1)]
    assert seq[6:8] == [("A", 2), ("B", 2)]
    intro = lifts[0]
    assert intro["name"].startswith("FP · Lift A intro")
    main, acc = intro["exercises"][0], intro["exercises"][2]
    assert (main["sets"], main["reps"], main["rir"], main["rest_s"]) == (2, 8, 4, 90)
    assert acc["reps"] == 10 and acc["note"] == "bodyweight or light"
    desc = ST.to_garmin_json(intro)["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]["description"]
    assert desc == "Goblet squat · 4 reps in reserve · light, find your starting weight"


def test_completed_sessions_move_the_level_on():
    days, cfg = _days(WEEK)
    lifts = _plan(days, cfg, progress={"A": 1, "B": 0}, completed=[])
    assert [(l["template"], l["level"]) for l in lifts][:2] == [("A", 1), ("B", 0)]
    assert ST.level_for(0) == 0 and ST.level_for(1) == 1 and ST.level_for(3) == 2 and ST.level_for(40) == 6


def _ride_week(cats):
    """One Monday-start week from day categories: easy/moderate/key ride or rest."""
    kind = {"easy": ("BASE", 60), "moderate": ("BASE", 120), "key": ("VO2MAX", 50), "long": ("LONG_WORKOUT", 150)}
    out = []
    for i, c in enumerate(cats):
        ds = (TODAY + timedelta(days=i)).isoformat()
        out.append({"date": ds, "rest": True, "type": "REST"} if c == "rest" else _w(i, *kind[c]))
    return out


def test_new_lifter_skips_the_day_two_days_before_a_key_ride():
    # Ian's week of 2026-10-26: lift done Monday; Wed VO2 and Fri sprint are key, Sat base is moderate
    days, cfg = _days(_ride_week(["easy", "moderate", "key", "rest", "key", "moderate", "rest"]), days=7)
    cats = [E.day_category(d) for d in days]
    assert cats[2] == "key" and cats[4] == "key" and cats[5] == "moderate"
    mon = TODAY.isoformat()
    fri, wed = (TODAY + timedelta(days=4)).isoformat(), (TODAY + timedelta(days=2)).isoformat()
    prev = [{"date": wed, "plan_key": "lift-wed", "template": "B"}]   # last run had it on VO2 Wednesday
    lifts = _plan(days, cfg, completed=[{"date": mon, "template": "A"}], progress={"A": 1}, previous=prev)
    # Friday is a key day too (Saturday moderate): the lift goes after that ride
    assert [(l["date"], l["template"], l["level"]) for l in lifts] == [(fri, "B", 0)]
    assert lifts[0]["reasons"][0].startswith("hard day")
    assert any("moved off" in r and "2 days later" in r for r in lifts[0]["reasons"])
    # once lifting is established the guard is off and the kept Wednesday stays
    est = _plan(days, cfg, completed=[{"date": mon, "template": "A"}], progress={"A": 4, "B": 3}, previous=prev)
    assert [(l["date"], l["plan_key"]) for l in est] == [(wed, "lift-wed")]


def test_soreness_guard_gives_way_when_no_other_day_works():
    # only Wednesday (key ride; Friday key) can hold a normal lift: the guard must not drop it. Friday (day
    # before the long ride) is the last resort, as a light session
    days, cfg = _days(_ride_week(["rest", "rest", "key", "rest", "key", "long", "rest"]), days=7)
    lifts = _plan(days, cfg)
    assert [(l["date"], l["variant"]) for l in lifts] == [((TODAY + timedelta(days=2)).isoformat(), "normal"),
                                                          ((TODAY + timedelta(days=4)).isoformat(), "light")]
    assert "long ride tomorrow: light session" in lifts[1]["reasons"]


def test_variant_set_by_date_overrides_the_light_rules():
    days, cfg = _days(_ride_week(["rest", "rest", "key", "rest", "key", "long", "rest"]), days=7)
    fri = (TODAY + timedelta(days=4)).isoformat()
    lifts = _plan(days, cfg, settings={"lift_variant_by_date": {fri: "normal"}})
    f = next(l for l in lifts if l["date"] == fri)
    assert f["variant"] == "normal" and "light" not in f["name"]
    assert "normal session set by you" in f["reasons"]
    assert not any(r.endswith(": light session") for r in f["reasons"])


def test_kept_date_refused_by_guard_is_reused_before_a_new_one():
    days, cfg = _days(_ride_week(["rest", "rest", "key", "rest", "key", "long", "rest"]), days=7)
    wed = (TODAY + timedelta(days=2)).isoformat()
    lifts = _plan(days, cfg, previous=[{"date": wed, "plan_key": "lift-keep", "template": "A"}])
    assert [(l["date"], l["plan_key"]) for l in lifts][:1] == [(wed, "lift-keep")]
    assert not any("moved off" in r for r in lifts[0]["reasons"])
    assert "no other day this week avoids a key ride 2 days later" in lifts[0]["reasons"]


# ---------------- working weights ----------------

def test_working_weights_go_on_the_watch_step():
    w = {"GOBLET_SQUAT": {"lb": 30}, "ROMANIAN_DEADLIFT": {"lb": 75, "note": "bar + 15/side"}}
    ex = ST.prescription("A", 1, "normal", False, True, "gym", w)
    by = {x["exercise"]: x for x in ex}
    assert by["GOBLET_SQUAT"]["weight_lb"] == 30 and "note" not in by["GOBLET_SQUAT"]
    assert by["ROMANIAN_DEADLIFT"]["note"] == "bar + 15/side"
    assert "weight_lb" not in by["DEAD_BUG"] and "weight_lb" not in by["BARBELL_BENCH_PRESS"]
    lift = {"name": "FP · Lift A1", "plan_key": "k", "spec_hash": "h", "minutes": 45, "exercises": ex}
    groups = [s for s in ST.to_garmin_json(lift)["workoutSegments"][0]["workoutSteps"] if s["type"] == "RepeatGroupDTO"]
    work = groups[0]["workoutSteps"][0]
    assert work["weightValue"] == 30 and work["weightUnit"]["unitKey"] == "pound"
    assert work["description"].startswith("Goblet squat · 30 lb · 3 reps in reserve")
    assert "weightValue" not in groups[-1]["workoutSteps"][0]


def test_known_weight_replaces_the_intro_note():
    ex = ST.prescription("A", 0, "normal", False, False, "gym", {"GOBLET_SQUAT": {"lb": 30}})
    by = {x["exercise"]: x for x in ex}
    assert "note" not in by["GOBLET_SQUAT"] and by["ROMANIAN_DEADLIFT"]["note"]
    assert ST.prescription("A", 0, "normal", False, False, "home", {"AIR_SQUAT": {"lb": 30}})[0].get("weight_lb") is None


def test_weekdays_only_keeps_lifts_off_the_weekend():
    # only Saturday has a ride and no hard ride follows it: it lands there unless weekends are off
    days, cfg = _days(_ride_week(["rest", "rest", "rest", "rest", "rest", "easy", "rest"]), days=7)
    assert any(date.fromisoformat(l["date"]).weekday() >= 5 for l in _plan(days, cfg))
    assert _plan(days, cfg, settings={"lift_weekdays_only": True}) == []   # Saturday was the only day


def test_lift_start_by_date_sets_the_time():
    days, cfg = _days(WEEK)
    first = _plan(days, cfg)[0]
    lift = _plan(days, cfg, settings={"lift_start_by_date": {first["date"]: "18:30"}})[0]
    assert lift["date"] == first["date"] and lift["start"] == 18 * 60 + 30 and lift["end"] == lift["start"] + ST.LIFT_MIN


def test_pt_block_rides_along_with_every_lift_and_lengthens_it():
    days, cfg = _days(WEEK)
    pt = [{"label": "Band ER", "category": "SHOULDER_STABILITY", "exercise": "BAND_EXTERNAL_ROTATION", "sets": 3, "reps": 12},
          {"label": "Scaption", "category": "LATERAL_RAISE", "exercise": "SCAPTION", "sets": 3, "reps": 10, "weight_lb": 5}]
    lifts = _plan(days, cfg, settings={"pt_block": pt, "pt_minutes": 20})
    assert lifts and all(l["minutes"] == ST.LIFT_MIN + 20 and l["end"] == l["start"] + ST.LIFT_MIN + 20 and l["name"].endswith(" + PT") for l in lifts)
    tail = lifts[0]["exercises"][-2:]
    assert [e["label"] for e in tail] == ["PT · Band ER", "PT · Scaption"] and tail[0]["rest_s"] == 45
    steps = ST.to_garmin_json(lifts[0])["workoutSegments"][0]["workoutSteps"]
    assert steps[-2]["workoutSteps"][0]["exerciseName"] == "SCAPTION" and steps[-2]["workoutSteps"][0]["weightValue"] == 5


def test_exercise_swaps_drop_replace_and_note_until_a_date():
    days, cfg = _days(WEEK)
    pt = [{"label": "Bent-over row", "category": "ROW", "exercise": "BENT_OVER_ROW_WITH_DUMBELL", "sets": 3, "reps": 15,
           "weight_lb": 25}]
    base = _plan(days, cfg, settings={"pt_block": pt})
    until = base[0]["date"]
    swaps = {"until": until, "reason": "back strain",
             "swap": {"BENT_OVER_ROW_WITH_DUMBELL": {"label": "Scapular squeeze", "category": "SHRUG",
                                                     "exercise": "SCAPULAR_RETRACTION", "reps": 10},
                      "DUMBBELL_ROW": None},
             "note": {"GOBLET_SQUAT": "light"}}
    lifts = _plan(days, cfg, settings={"pt_block": pt, "exercise_swaps": swaps, "upper_body_cleared": True})
    first, later = lifts[0], [l for l in lifts if l["date"] > until]
    names = [e["exercise"] for e in first["exercises"]]
    assert "BENT_OVER_ROW_WITH_DUMBELL" not in names and "DUMBBELL_ROW" not in names
    sq = next(e for e in first["exercises"] if e["exercise"] == "SCAPULAR_RETRACTION")
    assert sq["label"] == "PT · Scapular squeeze" and sq["sets"] == 3 and sq["reps"] == 10 and "weight_lb" not in sq
    assert any("back strain (until" in r for r in first["reasons"])
    assert later and "BENT_OVER_ROW_WITH_DUMBELL" in [e["exercise"] for e in later[0]["exercises"]]


def test_staged_swaps_bring_an_exercise_back_from_a_date():
    ex = [{"label": "Dumbbell row", "category": "ROW", "exercise": "DUMBBELL_ROW", "sets": 3, "reps": 10, "weight_lb": 30},
          {"label": "PT · Pull-up", "category": "PULL_UP", "exercise": "BAND_ASSISTED_PULL_UP", "sets": 3, "reps": 8}]
    swaps = {"until": "2026-11-13",
             "swap": {"DUMBBELL_ROW": [{"drop": True},
                                       {"from": "2026-11-04", "label": "Cable row", "category": "ROW",
                                        "exercise": "SEATED_CABLE_ROW", "sets": 2}],
                      "BAND_ASSISTED_PULL_UP": [{"from": "2026-11-04", "drop": True}]}}
    before, _ = ST.apply_swaps(ex, swaps, "2026-11-02")
    assert [e["exercise"] for e in before] == ["BAND_ASSISTED_PULL_UP"]  # no stage yet: unchanged
    on, _ = ST.apply_swaps(ex, swaps, "2026-11-04")
    assert [e["exercise"] for e in on] == ["SEATED_CABLE_ROW"]
    assert on[0]["sets"] == 2 and on[0]["reps"] == 10 and "weight_lb" not in on[0] and "from" not in on[0]


def test_gym_lift_drops_push_ups_on_bench_day_and_groups_pt_with_its_muscles():
    pt = [{"label": "Push-up", "category": "PUSH_UP", "exercise": "PUSH_UP", "sets": 3, "reps": 10},
          {"label": "Face pull", "category": "ROW", "exercise": "FACE_PULL", "sets": 2, "reps": 15},
          {"label": "Scaption", "category": "LATERAL_RAISE", "exercise": "SCAPTION", "sets": 3, "reps": 12},
          {"label": "Scapular push-up", "category": "PUSH_UP", "exercise": "PUSH_UP_PLUS", "sets": 3, "reps": 10}]
    a = ST.arrange_gym(ST.prescription("A", 2, "normal", False, True, pt=pt))
    assert [e["exercise"] for e in a] == ["GOBLET_SQUAT", "ROMANIAN_DEADLIFT", "DUMBBELL_BULGARIAN_SPLIT_SQUAT",
                                          "STANDING_CALF_RAISE", "SINGLE_LEG_HIP_RAISE", "STANDING_CABLE_HIP_ABDUCTION",
                                          "LEG_RAISE", "DEAD_BUG", "BARBELL_BENCH_PRESS", "PUSH_UP_PLUS",
                                          "SCAPTION", "FACE_PULL"]
    b = ST.arrange_gym(ST.prescription("B", 2, "normal", False, True, pt=pt))
    assert [e["exercise"] for e in b][-5:] == ["PUSH_UP", "PUSH_UP_PLUS", "SCAPTION", "DUMBBELL_ROW", "FACE_PULL"]


def test_hip_block_starts_at_two_sets_and_builds_to_three():
    b1 = {e["exercise"]: e for e in ST.prescription("B", 1, "normal", False, True)}
    assert [k for k in b1 if k in ST.HIP_REPS] == ["STANDING_CABLE_HIP_ABDUCTION", "LEG_RAISE", "REVERSE_CRUNCH"]
    assert b1["STANDING_CABLE_HIP_ABDUCTION"]["sets"] == 2 and b1["STANDING_CABLE_HIP_ABDUCTION"]["reps"] == 15
    assert "find your starting weight" in b1["LEG_RAISE"]["note"] and "note" not in b1["REVERSE_CRUNCH"]
    a3 = {e["exercise"]: e for e in ST.prescription("A", 3, "normal", False, True,
                                                    weights={"STANDING_CABLE_HIP_ABDUCTION": {"lb": 15}})}
    assert a3["SINGLE_LEG_HIP_RAISE"]["sets"] == 3 and a3["STANDING_CABLE_HIP_ABDUCTION"]["weight_lb"] == 15
    assert ST.prescription("A", 3, "light", False, True)[4]["sets"] == 2


def test_saved_weight_label_renames_the_step():
    b = {e["exercise"]: e for e in ST.prescription("B", 3, "normal", False, True, weights={
        "STANDING_CABLE_HIP_ABDUCTION": {"lb": 30, "label": "Hip abduction machine"},
        "BARBELL_HIP_THRUST_WITH_BENCH": {"lb": 50, "label": "Hip thrust (machine)"}})}
    assert b["STANDING_CABLE_HIP_ABDUCTION"]["label"] == "Hip abduction machine"
    assert b["STANDING_CABLE_HIP_ABDUCTION"]["weight_lb"] == 30
    assert b["BARBELL_HIP_THRUST_WITH_BENCH"]["label"] == "Hip thrust (machine)"
    assert b["LEG_RAISE"]["label"].startswith("Cable knee drive")


def test_moved_lift_keeps_its_new_calendar_entry_and_a_done_day_is_kept():
    led = {"k1": _L("k1", D1, 1, 11)}
    moved = {22: {"schedule_id": 22, "workout_id": 1, "date": D2}}
    op = G.diff([_desired("k1", D1)], led, moved, TODAY, END)[0]
    assert op["op"] == "adopt_move" and op["schedule_id"] == 22
    stale = {"k1": _L("k1", D2, 1, 11, status="moved_by_user")}  # ledger still holds the old entry
    rm = G.diff([], stale, moved, TODAY, END)
    assert rm[0]["op"] == "remove" and rm[0]["old"]["schedule_id"] == 22
    assert G.diff([], stale, moved, TODAY, END, keep_dates={D2}) == []


def test_side_plank_is_two_per_side_and_push_ups_carry_zero_weight():
    for eq in ("gym", "home"):
        for lvl in (0, 3):
            plank = next(e for e in ST.prescription("B", lvl, "light", False, True, eq) if e["exercise"] == "SIDE_PLANK")
            assert plank["sets"] == 4 and "2 per side" in plank["label"]
    lift = {"name": "FP · x", "plan_key": "k", "spec_hash": "h", "minutes": 45, "exercises": [
        {"label": "Push-up", "category": "PUSH_UP", "exercise": "PUSH_UP", "sets": 3, "reps": 10, "rest_s": 45},
        {"label": "Glute bridge", "category": "HIP_RAISE", "exercise": "HIP_RAISE", "sets": 2, "reps": 10, "rest_s": 45}]}
    steps = ST.to_garmin_json(lift)["workoutSegments"][0]["workoutSteps"]
    push, bridge = steps[1]["workoutSteps"][0], steps[2]["workoutSteps"][0]
    assert push["weightValue"] == 0.0 and "weightValue" not in bridge


# ---------------- placement rules (2026-11-04) ----------------

def _at(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def test_key_day_is_first_choice_and_the_lift_waits_three_hours():
    days, cfg = _days(_ride_week(["easy", "key", "easy", "easy", "easy", "rest", "rest"]), days=7)
    lifts = _plan(days, cfg, progress={"A": 4, "B": 4})
    assert lifts[0]["date"] == _at(1) and lifts[0]["reasons"][0].startswith("hard day")
    ride = next(s for s in days[1]["sessions"] if s["kind"] == "ride")
    assert lifts[0]["start"] >= ride["end"] + 180


def test_day_with_no_ride_gets_a_lift_from_17_but_a_planned_rest_day_does_not():
    # nothing planned yet (the week after the plan runs out): lifts still get placed
    open_days, cfg = _days([], days=7)
    lifts = _plan(open_days, cfg, progress={"A": 4, "B": 4})
    assert len(lifts) == 2 and all(l["start"] == 17 * 60 for l in lifts)
    assert all("no ride planned that day" in l["reasons"] for l in lifts)
    # Coach's rest days and ride_by_date "rest" are real rest days
    rest = _ride_week(["rest"] * 7)
    days, cfg = _days(rest, days=7)
    assert _plan(days, cfg, progress={"A": 4, "B": 4}) == []
    s = {**_snap([], 7), "settings": {"ride_by_date": {_at(d): "rest" for d in range(7)}}}
    d = E.build_days(s)
    E.schedule(d, KG, E.config(s), place_lifts=False)
    assert all(x["rest_planned"] for x in d) and _plan(d, E.config(s), progress={"A": 4, "B": 4}) == []


def test_rest_weekdays_never_get_a_lift():
    days, cfg = _days(_ride_week(["easy", "easy", "rest", "easy", "rest", "rest", "rest"]), days=7)
    assert any(l["date"] == _at(0) for l in _plan(days, cfg, progress={"A": 4, "B": 4}))
    lifts = _plan(days, cfg, progress={"A": 4, "B": 4}, settings={"lift_rest_weekdays": ["Mon"]})
    assert lifts and all(l["date"] != _at(0) for l in lifts)


def test_day_before_a_key_ride_stays_off_limits_even_for_a_catch_up():
    days, cfg = _days(_ride_week(["easy", "easy", "easy", "easy", "key", "long", "rest"]), days=7)
    notes = []
    lifts = _plan(days, cfg, today=TODAY + timedelta(days=3), completed=[{"date": _at(0), "template": "A"}],
                  progress={"A": 4, "B": 3}, missed=[_at(2)], notes=notes)
    # Thursday is the day before Friday's key ride: refused. Friday (key, long ride next) takes it, light
    assert [(l["date"], l["variant"]) for l in lifts] == [(_at(4), "light")]
    assert "catch-up for the missed " + _at(2) + " lift" in lifts[0]["reasons"]
    assert notes == []


def test_missed_lift_with_no_valid_day_is_dropped_with_a_note():
    days2, cfg = _days(_ride_week(["easy", "easy", "easy", "easy", "key", "long", "rest"]), days=7)
    notes = []
    lifts = _plan(days2, cfg, today=TODAY + timedelta(days=3), completed=[{"date": _at(0), "template": "A"},
                  {"date": _at(3), "template": "B"}], progress={"A": 4, "B": 4}, missed=[_at(2)], notes=notes)
    assert lifts == [] and notes == []  # two done this week: nothing to catch up
    trip = [{"date": _at(4), "start": "00:00", "end": "23:59", "title": "Trip", "busy": False, "all_day": True,
             "trip": True}]
    days3, cfg3 = _days(_ride_week(["easy", "easy", "easy", "easy", "key", "long", "rest"]), calendar=trip, days=7)
    notes = []
    lifts = _plan(days3, cfg3, today=TODAY + timedelta(days=3), completed=[{"date": _at(0), "template": "A"}],
                  progress={"A": 4, "B": 3}, missed=[_at(2)], notes=notes)
    assert lifts == []
    assert notes and "the missed " + _at(2) + " lift is dropped, not doubled up" in notes[0]


def test_lift_phases_set_lifts_per_week():
    two_weeks = _ride_week(["easy", "key", "easy", "key", "easy", "rest", "rest"])
    two_weeks += [{**w, "date": (date.fromisoformat(w["date"]) + timedelta(days=7)).isoformat()} for w in two_weeks]
    days, cfg = _days(two_weeks, days=14)
    phases = [{"from": _at(7), "per_week": 1, "note": "enduro block: maintenance"}]
    lifts = _plan(days, cfg, progress={"A": 4, "B": 4}, settings={"lift_phases": phases})
    per = {}
    for l in lifts:
        per.setdefault(E.monday_of(l["date"]), []).append(l)
    assert len(per[_at(0)]) == 2 and len(per[_at(7)]) == 1
    assert "enduro block: maintenance: 1 lift a week" in per[_at(7)][0]["reasons"]
    taper = [{"from": _at(0), "per_week": 0, "note": "race taper"}]
    assert _plan(days, cfg, settings={"lift_phases": taper}) == []


def test_blackout_dates_get_no_lift():
    open_days, cfg = _days([], days=7)
    black = [{"from": _at(0), "to": _at(4), "note": "Trip 1"}]
    lifts = _plan(open_days, cfg, progress={"A": 4, "B": 4}, settings={"lift_blackouts": black})
    assert [l["date"] for l in lifts] == [_at(5)]
    ok, why, _ = ST._valid(1, open_days, [], {**cfg, "lift_blackouts": black})
    assert not ok and why == f"Trip 1 ({_at(0)} to {_at(4)})"


def test_new_lift_never_reuses_a_past_lifts_key_and_never_starts_in_the_past():
    days, cfg = _days(_ride_week(["easy", "easy", "key", "rest", "rest", "rest", "rest"]), days=7)
    wed = TODAY + timedelta(days=2)
    lifts = _plan(days, cfg, today=wed, completed=[{"date": _at(0), "template": "A"}], progress={"A": 4, "B": 3},
                  reserved_keys={f"lift-{_at(0)}-1"})
    assert lifts and lifts[0]["plan_key"] != f"lift-{_at(0)}-1"
    # at 19:00 on Wednesday there is no 75-min window left before 19:30: today is skipped
    late = _plan(days, cfg, today=wed, completed=[{"date": _at(0), "template": "A"}], progress={"A": 4, "B": 3},
                 now_min=19 * 60)
    assert all(l["date"] != _at(2) for l in late)
    early = _plan(days, cfg, today=wed, completed=[{"date": _at(0), "template": "A"}], progress={"A": 4, "B": 3},
                  now_min=16 * 60 + 5)
    assert early[0]["date"] == _at(2) and early[0]["start"] >= 16 * 60 + 35
