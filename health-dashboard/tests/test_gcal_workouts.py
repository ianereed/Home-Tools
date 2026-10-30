"""Garmin calendar -> Google Calendar mirror: desired events, bodies and the reconciliation diff (pure)."""
from datetime import date, timedelta

from nutrition import gcal_workouts as G
from nutrition.settings import load_settings

TODAY = date(2026, 10, 14)
END = TODAY + timedelta(days=45)


def _settings(**over):
    s = load_settings(path=None)
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
    assert lift["summary"] == "🏋️ Lift A1 light · Home bodyweight" and lift["start"] == "17:00" and lift["minutes"] == 45
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
    assert body["extendedProperties"]["private"] == {"source": "garmin-sync", "garmin_key": "coach:u1", "digest": spec["digest"]}
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
    out = CS.classify(raw, load_settings(path=None))
    assert [e["title"] for e in out] == ["Group ride"]


def test_lift_start_by_date_moves_the_lift_event():
    s = _settings()
    s["lift_start_by_date"] = {"2026-10-15": "18:30"}
    out = G.desired_events([LIFT], [], s, TODAY, END, workout_minutes=lambda wid: 2700)
    assert out["workout:1809171360"]["start"] == "18:30"
