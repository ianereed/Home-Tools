"""Phase-0 read-only probes for the fuel planner (run on the Mac mini).

    .venv/bin/python3 -m nutrition.probe --all [--date YYYY-MM-DD]

Prints STRUCTURE (keys, types, presence flags, ids), never personal values, so the output can be journaled.
Nothing here writes to Garmin.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta


def shape(obj, depth=0, max_depth=3, max_keys=40):
    """Type skeleton of a JSON-ish object."""
    if depth >= max_depth:
        return type(obj).__name__
    if isinstance(obj, dict):
        return {k: shape(v, depth + 1, max_depth) for k, v in list(obj.items())[:max_keys]}
    if isinstance(obj, list):
        return [shape(obj[0], depth + 1, max_depth)] if obj else []
    return type(obj).__name__


def out(label, payload):
    print(f"=== {label}")
    print(json.dumps(payload, indent=1, default=str))


def try_call(label, fn):
    try:
        return fn()
    except Exception as e:  # probe: report and keep going
        out(label, {"error": f"{type(e).__name__}: {str(e)[:200]}"})
        return None


def probe_garmin(day: str, food_day: str | None = None):
    from collectors.garmin_collector import _get_garmin_client

    c = _get_garmin_client()

    plans = try_call("training_plans", c.get_training_plans)
    plan_ids = []
    if plans:
        lst = plans.get("trainingPlanList", []) if isinstance(plans, dict) else plans
        out("training_plans", [{"id": p.get("trainingPlanId"), "status": p.get("trainingStatus", {}).get("statusKey")
                                if isinstance(p.get("trainingStatus"), dict) else p.get("trainingStatus"),
                                "category": p.get("trainingPlanCategory"), "type": p.get("trainingType", {}).get("typeKey")
                                if isinstance(p.get("trainingType"), dict) else None,
                                "start": p.get("startDate"), "end": p.get("endDate")} for p in lst])
        plan_ids = [p.get("trainingPlanId") for p in lst]
        if lst:
            out("training_plan_item_shape", shape(lst[0]))

    uuid = None
    for pid in plan_ids:
        ad = try_call(f"adaptive_plan_{pid}", lambda pid=pid: c.get_adaptive_training_plan_by_id(pid))
        if ad and ad.get("taskList"):
            tl = ad["taskList"]
            out(f"adaptive_plan_{pid}_taskList", {"n": len(tl), "dates": [t.get("calendarDate") for t in tl],
                                                  "task_shape": shape(tl[0], max_depth=4)})
            for t in tl:
                tw = t.get("taskWorkout") or {}
                if tw.get("workoutUuid"):
                    uuid = tw["workoutUuid"]
                    break
            break

    if uuid:
        candidates = [f"/workout-service/fbt-adaptive/{uuid}", f"/workout-service/workout/{uuid}",
                      f"/workout-service/fbt-adaptive/workout/{uuid}", f"/trainingplan-service/fbt-adaptive/workout/{uuid}"]
        for path in candidates:
            r = try_call(f"steps {path}", lambda path=path: c.connectapi(path))
            if r:
                out(f"steps OK {path}", shape(r, max_depth=6))
                segs = r.get("workoutSegments") or []
                if segs:
                    st = (segs[0].get("workoutSteps") or [{}])[0]
                    out("first_step_target_fields", {k: st.get(k) for k in ("targetType", "targetValueOne", "targetValueTwo",
                                                                              "targetValueUnit", "zoneNumber")})
                break

    t = date.fromisoformat(day)
    sched = try_call("scheduled_month", lambda: c.get_scheduled_workouts(t.year, t.month))
    if sched:
        items = sched.get("calendarItems", [])
        types = sorted({i.get("itemType") for i in items})
        out("scheduled_month", {"n_items": len(items), "itemTypes": types})
        for ty in types:
            ex = next(i for i in items if i.get("itemType") == ty)
            out(f"scheduled_item_shape[{ty}]", shape(ex))

    prof = try_call("userprofile_settings", c.get_userprofile_settings)
    if prof:
        ud = prof.get("userData", {})
        out("userprofile_settings", {"userData_keys": sorted(ud), "userSleep": prof.get("userSleep") and sorted(prof["userSleep"]),
                                     "has_birthDate": "birthDate" in ud, "has_height": "height" in ud})

    stats = try_call("stats", lambda: c.get_stats(day))
    if stats:
        out("stats", {"has_consumedKilocalories": "consumedKilocalories" in stats,
                      "consumed_is_null": stats.get("consumedKilocalories") is None,
                      "energy_keys": sorted(k for k in stats if "alor" in k.lower())})

    nut = try_call("food_log", lambda: c.get_nutrition_daily_food_log(food_day or day))
    if nut:
        md = nut.get("mealDetails") or []
        keys = set()
        for m in md:
            keys |= set((m.get("mealNutritionContent") or {}).keys())
        out("food_log", {"n_meals": len(md), "nutrient_keys": sorted(keys), "has_cholesterol": "cholesterol" in keys})

    acts = try_call("activities", lambda: c.get_activities(0, 3))
    if acts:
        a = acts[0]
        out("activity_summary", {k: (k in a) for k in ("activityId", "activityName", "startTimeLocal", "duration",
                                                       "movingDuration", "avgPower", "normPower", "bmrCalories",
                                                       "calories", "activityTrainingLoad", "aerobicTrainingEffect")})

    ex = try_call("exercises_json", lambda: c.garth.get("connect", "/web-data/exercises/Exercises.json").json()
                  if hasattr(c, "garth") else None)
    if ex is None:
        import urllib.request
        ex = try_call("exercises_json_public", lambda: json.loads(urllib.request.urlopen(
            "https://connect.garmin.com/web-data/exercises/Exercises.json", timeout=20).read()))
    if ex:
        cats = ex.get("categories", {}) if isinstance(ex, dict) else {}
        want = ["SQUAT", "DEADLIFT", "LUNGE", "HIP_RAISE", "CALF_RAISE", "CORE", "PLANK"]
        out("exercises_json", {"top_keys": sorted(ex)[:10] if isinstance(ex, dict) else type(ex).__name__,
                               "n_categories": len(cats),
                               "sample": {k: sorted(cats[k].get("exercises", {}))[:12] for k in want if k in cats}})


def probe_calendar(calendar_id: str):
    """Run with event-aggregator's venv (it has the Google client libs)."""
    import os
    from pathlib import Path

    ea = Path(os.environ.get("EVENT_AGGREGATOR_DIR", Path(__file__).resolve().parents[2] / "event-aggregator"))
    sys.path.insert(0, str(ea))
    os.chdir(ea)  # token/credential paths in its config are relative to the project dir
    import config as ea_config  # noqa
    from connectors.google_auth import get_credentials  # noqa
    from googleapiclient.discovery import build  # noqa

    creds = get_credentials(["https://www.googleapis.com/auth/calendar.events"], ea_config.GCAL_TOKEN_JSON,
                            ea_config.GMAIL_CREDENTIALS_JSON, "gcal_token")
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    cal = try_call("calendar_get", lambda: svc.calendars().get(calendarId=calendar_id).execute())
    if cal:
        out("calendar", {"requested_readable": True, "timeZone": cal.get("timeZone")})
    prim = try_call("calendar_primary", lambda: svc.calendars().get(calendarId="primary").execute())
    if prim:
        out("token_primary_is_requested", prim.get("id") == calendar_id)
    ev = try_call("events", lambda: svc.events().list(calendarId=calendar_id, maxResults=5, singleEvents=True,
                                                      orderBy="startTime", timeMin=date.today().isoformat() + "T00:00:00Z").execute())
    if ev:
        out("events", {"n": len(ev.get("items", [])), "item_keys": sorted({k for i in ev.get("items", []) for k in i})})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--garmin", action="store_true")
    ap.add_argument("--calendar", metavar="CALENDAR_ID")
    ap.add_argument("--date", default=(date.today() - timedelta(days=1)).isoformat())
    ap.add_argument("--food-date", help="a date with Garmin food logging, to inspect nutrient keys")
    a = ap.parse_args(argv)
    if a.garmin:
        probe_garmin(a.date, a.food_date)
    if a.calendar:
        probe_calendar(a.calendar)


if __name__ == "__main__":
    main()
