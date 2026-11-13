"""Garmin calendar -> Google Calendar mirror: desired events, bodies and the reconciliation diff (pure)."""
from copy import deepcopy
from datetime import date, timedelta

from nutrition import gcal_workouts as G
from nutrition.settings import DEFAULTS

TODAY = date(2026, 10, 14)
END = TODAY + timedelta(days=45)


def _settings(**over):
    s = deepcopy(DEFAULTS)  # not load_settings(): the mini's data/fuel_settings.json would leak into the tests
    s["gcal_sync"].update(over)
    return s


def _coach_item(d, uuid, title="Sprint"):
    return {"itemType": "fbtAdaptiveWorkout", "id": 1, "date": d, "title": title, "workoutUuid": uuid,
            "trainingPlanId": 48015501, "sportTypeKey": "cycling"}


def _task(d, uuid, name="Sprint", secs=3180, desc="6 x 0:30 all-out", status=None, rest=False):
    tw = {"restDay": True, "workoutPhrase": "EASY_WEEK_LOAD_REST"} if rest else \
        {"workoutUuid": uuid, "workoutName": name, "workoutDescription": desc, "estimatedDurationInSecs": secs,
         "workoutPhrase": "ANAEROBIC_SPEED", "adaptiveCoachingWorkoutStatus": status}
    return {"calendarDate": d, "taskWorkout": tw}


LIFT = {"itemType": "workout", "id": 1809171360, "date": "2026-10-15", "title": "FP · Lift A1 light · Home bodyweight",
        "workoutId": 1730120564, "sportTypeKey": "strength_training"}
NOISE = [{"itemType": "activity", "id": 5, "date": "2026-10-14", "title": "Redwood City - Sprint"},
         {"itemType": "weight", "id": 6, "date": "2026-10-14"},
         {"itemType": "trainingPlan", "id": 48015501, "date": "2026-11-20", "title": "2026 Fitness Plan"}]


def test_desired_events_cover_coach_lifts_and_rest_days_only():
    items = [_coach_item("2026-10-14", "u1"), _coach_item("2026-10-17", "u2", "Long Ride"), LIFT, *NOISE]
    tasks = [_task("2026-10-14", "u1"), _task("2026-10-16", None, rest=True),
             _task("2026-10-17", "u2", "Long Ride", 7200, "2:00:00@150W")]
    out = G.desired_events(items, tasks, _settings(), TODAY, END, workout_minutes=lambda wid: 2700)
    assert set(out) == {"coach:u1", "coach:u2", "workout:1809171360", "rest:2026-10-16"}
    sprint = out["coach:u1"]
    assert sprint["summary"] == "🚴 Sprint (Coach)" and sprint["start"] == "10:00" and sprint["minutes"] == 55
    assert "6 x 0:30 all-out" in sprint["description"] and G.GARMIN_CAL_URL in sprint["description"]
    assert out["coach:u2"]["minutes"] == 120
    lift = out["workout:1809171360"]
    assert lift["summary"] == "🏋️ Lift A1 light · Home bodyweight" and lift["start"] == "14:00" and lift["minutes"] == 45
    rest = out["rest:2026-10-16"]
    assert rest["all_day"] and rest["summary"] == "😴 Rest day (Coach)"


def test_window_and_completion_and_rest_toggle():
    items = [_coach_item("2026-10-13", "old"), _coach_item("2026-12-25", "far"), _coach_item("2026-10-14", "u1"),
             _coach_item("2026-10-15", "u2")]
    tasks = [_task("2026-10-14", "u1", status="COMPLETED_TODAYS_WORKOUT"), _task("2026-10-15", "u2", status="NOT_COMPLETE"),
             _task("2026-10-16", None, rest=True)]
    out = G.desired_events(items, tasks, _settings(include_rest_days=False), TODAY, END)
    assert set(out) == {"coach:u1", "coach:u2"}  # past and beyond-window items ignored; rest days off
    assert out["coach:u1"]["summary"].startswith("✅ ")
    assert out["coach:u2"]["summary"] == "🚴 Sprint (Coach)"  # NOT_COMPLETE is not done
    assert out["coach:u1"]["minutes"] == 55  # 3180 s rounds to the nearest 5 min


def test_rest_day_is_skipped_when_garmin_has_an_item_that_day():
    out = G.desired_events([LIFT], [_task("2026-10-15", None, rest=True)], _settings(), TODAY, END)
    assert set(out) == {"workout:1809171360"}


def test_event_bodies_are_tagged_free_and_timed_in_local_tz():
    spec = G.desired_events([_coach_item("2026-10-14", "u1")], [_task("2026-10-14", "u1")], _settings(), TODAY, END)["coach:u1"]
    body = G.event_body(spec)
    assert body["transparency"] == "transparent"
    assert body["reminders"] == {"useDefault": False, "overrides": []}
    assert body["extendedProperties"]["private"] == {"source": "garmin-sync", "garmin_key": "coach:u1", "digest": spec["digest"],
                                                     "synced_date": "2026-10-14", "synced_start": "10:00"}
    assert body["start"] == {"dateTime": "2026-10-14T10:00:00", "timeZone": "America/Los_Angeles"}
    assert body["end"] == {"dateTime": "2026-10-14T10:55:00", "timeZone": "America/Los_Angeles"}
    rest = G.desired_events([], [_task("2026-10-16", None, rest=True)], _settings(), TODAY, END)["rest:2026-10-16"]
    rb = G.event_body(rest)
    assert rb["start"] == {"date": "2026-10-16"} and rb["end"] == {"date": "2026-10-17"}


def test_diff_inserts_patches_deletes_and_never_touches_the_past():
    desired = G.desired_events([_coach_item("2026-10-14", "u1"), _coach_item("2026-10-17", "u2", "Base")],
                               [_task("2026-10-14", "u1"), _task("2026-10-17", "u2", "Base", 3600)], _settings(), TODAY, END)
    existing = [
        {"id": "e1", "key": "coach:u1", "digest": desired["coach:u1"]["digest"], "date": "2026-10-14"},  # unchanged
        {"id": "e2", "key": "coach:u2", "digest": "stale", "date": "2026-10-16"},                        # moved by Coach
        {"id": "e3", "key": "coach:gone", "digest": "x", "date": "2026-10-20"},                          # dropped by Coach
        {"id": "e4", "key": "coach:past", "digest": "x", "date": "2026-10-10"},                          # history: untouched
        {"id": "e5", "key": "coach:u1", "digest": desired["coach:u1"]["digest"], "date": "2026-10-14"},  # duplicate
    ]
    ops = G.diff(desired, existing, TODAY)
    assert [(o["op"], o["key"]) for o in ops] == [("delete", "coach:u1"), ("patch", "coach:u2"), ("delete", "coach:gone")]
    patch = next(o for o in ops if o["op"] == "patch")
    assert patch["event_id"] == "e2" and patch["body"]["start"]["dateTime"].startswith("2026-10-17T")
    assert next(o for o in ops if o["key"] == "coach:u1")["event_id"] == "e5"
    # nothing on the calendar yet -> everything is an insert, ordered by date
    ops = G.diff(desired, [], TODAY)
    assert [(o["op"], o["date"]) for o in ops] == [("insert", "2026-10-14"), ("insert", "2026-10-17")]
    # same state twice -> no ops
    assert G.diff(desired, [{"id": "a", "key": k, "digest": s["digest"], "date": s["date"]} for k, s in desired.items()], TODAY) == []


def test_calendar_source_skips_our_own_mirror_events():
    from nutrition import calendar_source as CS
    raw = [{"summary": "🚴 Long Ride (Coach)", "start": "2026-10-17T10:00:00-07:00", "end": "2026-10-17T12:00:00-07:00",
            "all_day": False, "transparency": "transparent", "source_tag": "garmin-sync"},
           {"summary": "Group ride", "start": "2026-10-18T09:00:00-07:00", "end": "2026-10-18T12:00:00-07:00", "all_day": False}]
    out = CS.classify(raw, deepcopy(DEFAULTS))
    assert [e["title"] for e in out] == ["Group ride"]


def test_lift_start_by_date_moves_the_lift_event():
    s = _settings()
    s["lift_start_by_date"] = {"2026-10-15": "18:30"}
    out = G.desired_events([LIFT], [], s, TODAY, END, workout_minutes=lambda wid: 2700)
    assert out["workout:1809171360"]["start"] == "18:30"


def test_ride_by_date_drops_the_coach_ride_and_moves_the_group_ride():
    ride = {"itemType": "workout", "id": 77, "date": "2026-10-18", "title": "Group ride · 2h aerobic",
            "workoutId": 1745984637, "sportTypeKey": "cycling"}
    s = _settings()
    s["ride_by_date"] = {"2026-10-17": "rest", "2026-10-18": {"start": "09:00", "minutes": 120}}
    out = G.desired_events([_coach_item("2026-10-17", "u2", "Base"), ride], [_task("2026-10-17", "u2", "Base", 7200)],
                           s, TODAY, END, workout_minutes=lambda wid: 7200)
    assert set(out) == {"rest:2026-10-17", "workout:77"}
    assert out["rest:2026-10-17"]["summary"] == "😴 Rest day (you)" and out["rest:2026-10-17"]["all_day"]
    assert out["workout:77"]["start"] == "09:00"


def test_a_hand_moved_event_keeps_your_time_when_it_is_repatched():
    desired = G.desired_events([LIFT], [], _settings(), TODAY, END, workout_minutes=lambda wid: 2700)
    spec = desired["workout:1809171360"]
    # the sync put it at 14:00 on 10-15; you dragged it to 17:30 the same day; then Garmin renamed it (stale digest)
    moved = {"id": "e1", "key": spec["key"], "digest": "stale", "date": "2026-10-15",
             "start": "2026-10-15T17:30:00-07:00", "end": "2026-10-15T18:15:00-07:00",
             "synced_date": "2026-10-15", "synced_start": "14:00"}
    body = G.diff(desired, [moved], TODAY)[0]["body"]
    assert body["start"]["dateTime"] == "2026-10-15T17:30:00" and body["end"]["dateTime"] == "2026-10-15T18:15:00"
    assert body["extendedProperties"]["private"]["synced_start"] == "14:00"  # still records the sync's own slot
    # moved to another day by hand: your day and time stay
    other_day = moved | {"date": "2026-10-16", "start": "2026-10-16T07:00:00-07:00"}
    assert G.diff(desired, [other_day], TODAY)[0]["body"]["start"]["dateTime"] == "2026-10-16T07:00:00"
    # Garmin moved the workout to a new day since: the new day wins, your time of day is kept
    garmin_moved = moved | {"synced_date": "2026-10-14"}
    assert G.diff(desired, [garmin_moved], TODAY)[0]["body"]["start"]["dateTime"] == "2026-10-15T17:30:00"
    # not moved (still where the sync put it) or written before v3 (no placement): the sync's time
    same = moved | {"start": "2026-10-15T14:00:00-07:00"}
    legacy = {k: v for k, v in moved.items() if not k.startswith("synced_")}
    for e in (same, legacy):
        assert G.diff(desired, [e], TODAY)[0]["body"]["start"]["dateTime"] == "2026-10-15T14:00:00"
    # a moved event whose content did not change is left alone
    assert G.diff(desired, [moved | {"digest": spec["digest"]}], TODAY) == []


def test_workout_slots_read_our_mirror_events_as_local_times():
    from nutrition import calendar_source as CS
    raw = [{"summary": "🚴 Threshold · 3x10 min", "start": "2026-11-12T10:00:00-08:00", "end": "2026-11-12T11:10:00-08:00",
            "all_day": False, "source_tag": "garmin-sync"},
           {"summary": "✅ 🏋️ Lift A1 · Full body + PT", "start": "2026-11-12T17:00:00-08:00",
            "end": "2026-11-12T18:15:00-08:00", "all_day": False, "source_tag": "garmin-sync"},
           {"summary": "😴 Rest day (Coach)", "start": "2026-11-13", "end": "2026-11-14", "all_day": True, "source_tag": "garmin-sync"},
           {"summary": "🚴 Group ride", "start": "2026-11-15T09:00:00-08:00", "end": "2026-11-15T11:00:00-08:00", "all_day": False}]
    assert CS.workout_slots(raw) == [
        {"date": "2026-11-12", "start": "10:00", "end": "11:10", "sport": "cycling", "title": "🚴 Threshold · 3x10 min"},
        {"date": "2026-11-12", "start": "17:00", "end": "18:15", "sport": "strength_training", "title": "🏋️ Lift A1 · Full body + PT"}]


def _ev(key, placed_at, synced_date, synced_start="14:00", summary="🏋️ Lift A1 light · Home bodyweight", digest="d"):
    d, hm = placed_at
    return {"id": f"ev-{key}", "key": key, "digest": digest, "date": d, "summary": summary,
            "start": f"{d}T{hm}:00-07:00", "end": f"{d}T23:00:00-07:00",
            "synced_date": synced_date, "synced_start": synced_start}


def test_day_moves_only_for_scheduled_workouts_dragged_to_another_day():
    key = "workout:1809171360"
    assert G.day_moves([_ev(key, ("2026-10-16", "18:00"), "2026-10-15")], [LIFT], TODAY, END) == [
        {"key": key, "schedule_id": 1809171360, "workout_id": 1730120564, "sport": "strength_training",
         "title": LIFT["title"], "from": "2026-10-15", "to": "2026-10-16", "start": "18:00"}]
    none = [
        _ev(key, ("2026-10-15", "18:00"), "2026-10-15"),                    # moved in time only
        _ev(key, ("2026-10-16", "18:00"), "2026-10-14"),                    # Garmin moved it since (Garmin: 10-15)
        _ev(key, ("2026-10-13", "18:00"), "2026-10-15"),                    # dragged into the past
        {k: v for k, v in _ev(key, ("2026-10-16", "18:00"), "2026-10-15").items() if not k.startswith("synced")},  # pre-v3
        _ev("coach:u1", ("2026-10-16", "10:00"), "2026-10-15", "10:00"),   # Coach workouts never move from here
    ]
    for e in none:
        assert G.day_moves([e], [LIFT, _coach_item("2026-10-15", "u1")], TODAY, END) == []


def test_move_ride_override_follows_the_ride_and_drops_a_rest_day_there():
    group = {"start": "09:00", "minutes": 120}
    rbd, note = G.move_ride_override({"2026-10-18": group, "2026-10-17": "rest"}, "2026-10-18", "2026-10-17")
    assert rbd == {"2026-10-17": group} and "rest day 2026-10-17 dropped" in note and "moved" in note
    assert G.move_ride_override({"2026-10-19": group}, "2026-10-18", "2026-10-17") == ({"2026-10-19": group}, None)
    rbd, note = G.move_ride_override({"2026-10-18": group, "2026-10-17": {"start": "07:00"}}, "2026-10-18", "2026-10-17")
    assert rbd["2026-10-18"] == group and "kept" in note


class _FakeGarmin:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def schedule_workout(self, wid, ds):
        self.calls.append(("schedule", wid, ds))
        if self.fail == "schedule":
            raise ConnectionError("down")
        return {"workoutScheduleId": 555}

    def unschedule_workout(self, sid):
        self.calls.append(("unschedule", sid))


def _ledger_db(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "h.db")
    conn.executescript("""
        CREATE TABLE fuel_garmin_workouts (plan_key TEXT PRIMARY KEY, kind TEXT, date TEXT, spec_hash TEXT, name TEXT,
          garmin_workout_id INTEGER, garmin_schedule_id INTEGER, status TEXT, reasons_json TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE fuel_garmin_ops (id INTEGER PRIMARY KEY, ts TEXT, run_id INTEGER, mode TEXT, op TEXT, plan_key TEXT,
          workout_id INTEGER, schedule_id INTEGER, date TEXT, ok INTEGER, detail TEXT);
        CREATE TABLE fuel_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);""")
    conn.execute("INSERT INTO fuel_garmin_workouts VALUES ('lift-2026-10-12-2','lift','2026-10-15','h','n',1730120564,"
                 "1809171360,'synced','[]','t','t')")
    conn.commit()
    return conn


def test_a_lift_dragged_to_another_day_moves_in_garmin_and_keeps_your_time(tmp_path):
    from nutrition import store
    conn = _ledger_db(tmp_path)
    items, s = [dict(LIFT)], _settings()
    existing = [_ev("workout:1809171360", ("2026-10-16", "18:00"), "2026-10-15")]
    fc = _FakeGarmin()
    res = G.apply_day_moves(fc, conn, G.day_moves(existing, items, TODAY, END), items, existing, s,
                            settings_path=tmp_path / "unused.json")
    assert fc.calls == [("schedule", 1730120564, "2026-10-16"), ("unschedule", 1809171360)]
    assert res[0]["ok"] and res[0]["lift"] == "lift-2026-10-12-2" and res[0]["schedule_id"] == 555
    assert conn.execute("SELECT date, garmin_schedule_id, status FROM fuel_garmin_workouts").fetchone() == \
        ("2026-10-16", 555, "moved_by_user")
    assert store.get_state(conn, "refresh_requested") == 1
    # this run then re-patches the same event under its new key, on the new day at your time
    desired = G.desired_events(items, [], s, TODAY, END, workout_minutes=lambda wid: 2700)
    ops = G.diff(desired, existing, TODAY)
    assert [(o["op"], o["key"], o["event_id"]) for o in ops] == [("patch", "workout:555", "ev-workout:1809171360")]
    body = ops[0]["body"]
    assert body["start"]["dateTime"] == "2026-10-16T18:00:00"
    assert body["extendedProperties"]["private"]["synced_date"] == "2026-10-16"
    # a failed Garmin call changes nothing; the event keeps your day and the next run retries
    items2, existing2 = [dict(LIFT)], [_ev("workout:1809171360", ("2026-10-16", "18:00"), "2026-10-15")]
    res = G.apply_day_moves(_FakeGarmin(fail="schedule"), conn, G.day_moves(existing2, items2, TODAY, END),
                            items2, existing2, s, settings_path=tmp_path / "unused.json")
    assert not res[0]["ok"] and items2[0]["id"] == 1809171360 and existing2[0]["key"] == "workout:1809171360"
    conn.close()


def test_a_ride_dragged_to_another_day_takes_its_ride_by_date_entry(tmp_path):
    import json
    ride = {"itemType": "workout", "id": 77, "date": "2026-10-18", "title": "Group ride · 2h", "workoutId": 1745984637,
            "sportTypeKey": "cycling"}
    path = tmp_path / "fuel_settings.json"
    path.write_text(json.dumps({"ride_by_date": {"2026-10-18": {"start": "09:00", "minutes": 120}}, "x": "·"}, indent=2))
    s = _settings()
    s["ride_by_date"] = {"2026-10-18": {"start": "09:00", "minutes": 120}}
    conn = _ledger_db(tmp_path)
    items = [ride]
    existing = [_ev("workout:77", ("2026-10-17", "09:00"), "2026-10-18", "09:00", summary="🚴 Group ride · 2h")]
    res = G.apply_day_moves(_FakeGarmin(), conn, G.day_moves(existing, items, TODAY, END), items, existing, s,
                            settings_path=path)
    assert res[0]["ok"] and "lift" not in res[0]
    assert json.loads(path.read_text())["ride_by_date"] == {"2026-10-17": {"start": "09:00", "minutes": 120}}
    assert json.loads(path.read_text())["x"] == "·"
    assert (tmp_path / res[0]["settings_backup"]).exists() and s["ride_by_date"] == {"2026-10-17": {"start": "09:00", "minutes": 120}}
    conn.close()


def test_a_lift_garmin_re_created_reuses_its_event_and_your_time():
    # the planner replaced the lift (new workout, new schedule id) on the same day; you had dragged it to 17:30
    new = LIFT | {"id": 1900000000, "workoutId": 1800000000}
    desired = G.desired_events([new], [], _settings(), TODAY, END, workout_minutes=lambda wid: 2700)
    old = _ev("workout:1809171360", ("2026-10-15", "17:30"), "2026-10-15")
    ops = G.diff(desired, [old], TODAY)
    assert [(o["op"], o["key"], o["event_id"]) for o in ops] == [("patch", "workout:1900000000", "ev-workout:1809171360")]
    assert ops[0]["body"]["start"]["dateTime"] == "2026-10-15T17:30:00"
    assert ops[0]["body"]["extendedProperties"]["private"]["garmin_key"] == "workout:1900000000"
    # a different sport or another day is not the same workout: delete + insert
    ride_ev = _ev("workout:1809171360", ("2026-10-15", "17:30"), "2026-10-15", summary="🚴 Base")
    assert sorted(o["op"] for o in G.diff(desired, [ride_ev], TODAY)) == ["delete", "insert"]
