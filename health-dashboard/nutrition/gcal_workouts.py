"""Garmin calendar → Google Calendar mirror: Coach rides, planner lifts and Coach rest days.

    .venv/bin/python3 -m nutrition.gcal_workouts [--dry-run] [--print]

Rules:
- Only events this sync created are ever touched (extendedProperties.private.source == TAG); anything
  else on the calendar is invisible to it.
- Past dates are never modified: the window is [today, today + gcal_sync.window_days].
- State lives in the calendar itself (tag + garmin_key + digest), so a lost DB or a hand-deleted event
  is simply re-created on the next run; a hand-edited event is overwritten (its digest no longer matches).
- Google API calls run in event-aggregator's venv via collectors/gcal_write.py (one list + one apply).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import coach, store
from .settings import load_settings

log = logging.getLogger("nutrition.gcal_workouts")

TAG = "garmin-sync"
TZ = "America/Los_Angeles"
ROOT = Path(__file__).resolve().parents[1]
WRITE_SCRIPT = ROOT / "collectors" / "gcal_write.py"
DEFAULT_EA_DIR = ROOT.parent / "event-aggregator"
LOCK_PATH = ROOT / "data" / "gcal_sync.lock"
GARMIN_CAL_URL = "https://connect.garmin.com/modern/calendar"
FP_PREFIX = "FP · "
ICON = {"cycling": "🚴", "strength_training": "🏋️", "running": "🏃", "hiking": "🥾", "swimming": "🏊"}
BODY_VERSION = 2  # bump when event_body() changes shape so existing events get re-patched
FOOTER = "Synced from Garmin Connect by health-dashboard three times a day; edits made here are overwritten."


# ---------------------------------------------------------------- pure: Garmin items -> desired events
def _minutes(secs, fallback: int) -> int:
    try:
        s = int(secs or 0)
    except (TypeError, ValueError):
        s = 0
    return max(5, int(round(s / 300.0)) * 5) if s > 0 else fallback


def _start(sport: str, settings: dict, d: str | None = None) -> str:
    if sport == "strength_training" and d in (settings.get("lift_start_by_date") or {}):
        return settings["lift_start_by_date"][d]
    ride = (settings.get("ride_by_date") or {}).get(d)
    if sport == "cycling" and isinstance(ride, dict) and ride.get("start"):
        return ride["start"]
    st = (settings.get("gcal_sync") or {}).get("start_times") or {}
    if sport in st:
        return st[sport]
    if sport == "cycling" and settings.get("preferred_ride"):
        return settings["preferred_ride"]
    return st.get("default", "10:00")


def _default_minutes(sport: str, settings: dict) -> int:
    dm = (settings.get("gcal_sync") or {}).get("default_minutes") or {}
    return int(dm.get(sport, dm.get("default", 60)))


def _coach_task_index(tasks: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Adaptive-plan taskList -> ({workoutUuid: task}, {date: rest-day task})."""
    by_uuid, rest = {}, {}
    for t in tasks or []:
        tw = t.get("taskWorkout") or {}
        if tw.get("restDay"):
            if t.get("calendarDate"):
                rest[t["calendarDate"][:10]] = t
        elif tw.get("workoutUuid"):
            by_uuid[tw["workoutUuid"]] = t
    return by_uuid, rest


def _done(task: dict | None) -> bool:
    t = task or {}
    status = str(t.get("adaptiveCoachingWorkoutStatus") or (t.get("taskWorkout") or {}).get("adaptiveCoachingWorkoutStatus")
                 or "").upper()
    return status.startswith("COMPLETED") or status in ("COMPLETE", "DONE")  # Garmin also sends NOT_COMPLETE


def desired_events(items: list[dict], tasks: list[dict], settings: dict, today: date, window_end: date,
                   workout_minutes=lambda workout_id: None) -> dict[str, dict]:
    """Garmin calendar items + Coach taskList -> {key: spec}. Pure.

    spec = {key, date, summary, description, all_day, start ("HH:MM"), minutes, digest}
    """
    cfg = settings.get("gcal_sync") or {}
    t0, t1 = today.isoformat(), window_end.isoformat()
    by_uuid, rest = _coach_task_index(tasks)
    rest_by_you = {d for d, v in (settings.get("ride_by_date") or {}).items() if v == "rest"}
    out: dict[str, dict] = {}
    dated_items: set[str] = set()
    for it in items:
        d = (it.get("date") or "")[:10]
        kind = it.get("itemType")
        if kind not in ("fbtAdaptiveWorkout", "workout") or not (t0 <= d <= t1):
            continue
        sport = it.get("sportTypeKey") or "default"
        if sport == "cycling" and d in rest_by_you:
            continue
        icon = ICON.get(sport, "🏅")
        if kind == "fbtAdaptiveWorkout":
            uuid = it.get("workoutUuid")
            if not uuid:
                continue
            task = by_uuid.get(uuid)
            tw = (task or {}).get("taskWorkout") or {}
            name = tw.get("workoutName") or it.get("title") or "Coach workout"
            minutes = _minutes(tw.get("estimatedDurationInSecs"), _default_minutes(sport, settings))
            label = tw.get("workoutPhrase") or tw.get("trainingEffectLabel") or ""
            lines = [f"Garmin Coach · {name}" + (f" · {label.replace('_', ' ').title()}" if label else "")
                     + f" · ~{minutes} min"]
            if tw.get("workoutDescription"):
                lines.append(str(tw["workoutDescription"]))
            summary = f"{'✅ ' if _done(task) else ''}{icon} {name} (Coach)"
            key = f"coach:{uuid}"
        else:
            wid = it.get("workoutId")
            if wid is None:
                continue
            title = it.get("title") or "Workout"
            if title.startswith(FP_PREFIX):
                title = title[len(FP_PREFIX):]
            minutes = _minutes(workout_minutes(wid), _default_minutes(sport, settings))
            lines = [f"Garmin workout · {sport.replace('_', ' ')} · ~{minutes} min"]
            summary = f"{icon} {title}"
            key = f"workout:{it.get('id')}"
        lines += [GARMIN_CAL_URL, FOOTER]
        out[key] = _spec(key, d, summary, "\n".join(lines), False, _start(sport, settings, d), minutes)
        dated_items.add(d)
    if cfg.get("include_rest_days", True):
        for d, task in rest.items():
            if not (t0 <= d <= t1) or d in dated_items:
                continue
            phrase = ((task.get("taskWorkout") or {}).get("workoutPhrase") or "REST").replace("_", " ").title()
            key = f"rest:{d}"
            out[key] = _spec(key, d, "😴 Rest day (Coach)", "\n".join([f"Garmin Coach · {phrase}", GARMIN_CAL_URL, FOOTER]),
                             True, None, 0)
        for d in sorted(rest_by_you):
            if t0 <= d <= t1 and d not in dated_items:
                key = f"rest:{d}"
                out[key] = _spec(key, d, "😴 Rest day (you)", "\n".join(["Rest day set by you; the planned ride is skipped.",
                                                                        GARMIN_CAL_URL, FOOTER]), True, None, 0)
    return out


def _spec(key, d, summary, description, all_day, start, minutes) -> dict:
    spec = {"key": key, "date": d, "summary": summary, "description": description, "all_day": all_day,
            "start": start, "minutes": minutes}
    spec["digest"] = hashlib.sha1(json.dumps(spec | {"v": BODY_VERSION}, sort_keys=True).encode()).hexdigest()[:16]
    return spec


def event_body(spec: dict) -> dict:
    body = {"summary": spec["summary"], "description": spec["description"], "transparency": "transparent",
            "reminders": {"useDefault": False, "overrides": []},  # no notifications for mirrored workouts
            "extendedProperties": {"private": {"source": TAG, "garmin_key": spec["key"], "digest": spec["digest"]}}}
    d = date.fromisoformat(spec["date"])
    if spec["all_day"]:
        body["start"] = {"date": d.isoformat()}
        body["end"] = {"date": (d + timedelta(days=1)).isoformat()}
    else:
        h, m = (int(x) for x in spec["start"].split(":"))
        s = datetime(d.year, d.month, d.day, h, m)
        e = s + timedelta(minutes=spec["minutes"])
        body["start"] = {"dateTime": s.isoformat(), "timeZone": TZ}
        body["end"] = {"dateTime": e.isoformat(), "timeZone": TZ}
    return body


def diff(desired: dict[str, dict], existing: list[dict], today: date) -> list[dict]:
    """Pure: ops that make the tagged events in the window equal `desired`. Past dates are left alone."""
    t0 = today.isoformat()
    ops, seen = [], set()
    for e in existing:
        key = e.get("key")
        if not key or (e.get("date") or "") < t0:
            continue
        if key not in desired or key in seen:
            ops.append({"op": "delete", "key": key, "event_id": e["id"], "date": e.get("date")})
            continue
        seen.add(key)
        if e.get("digest") != desired[key]["digest"]:
            ops.append({"op": "patch", "key": key, "event_id": e["id"], "date": desired[key]["date"],
                        "body": event_body(desired[key])})
    for key, spec in desired.items():
        if key not in seen:
            ops.append({"op": "insert", "key": key, "date": spec["date"], "body": event_body(spec)})
    return sorted(ops, key=lambda o: (o["date"] or "", o["op"]))


# ---------------------------------------------------------------- I/O
def gcal(request: dict, timeout: int = 120) -> dict:
    ea = Path(os.environ.get("EVENT_AGGREGATOR_DIR", DEFAULT_EA_DIR))
    py = ea / ".venv" / "bin" / "python"
    proc = subprocess.run([str(py), "-I", str(WRITE_SCRIPT)], input=json.dumps(request), capture_output=True,
                          text=True, timeout=timeout, env={**os.environ, "EVENT_AGGREGATOR_DIR": str(ea)})
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"gcal_write printed no JSON (rc={proc.returncode}): {proc.stderr[-300:]}")
    out = json.loads(lines[-1])
    if not out.get("ok"):
        raise RuntimeError(out.get("error", "gcal_write failed"))
    return out


def fetch_garmin(client, today: date, window_end: date) -> tuple[list[dict], list[dict], dict | None]:
    items, seen_ids = [], set()
    y, m = today.year, today.month
    while (y, m) <= (window_end.year, window_end.month):
        for it in (client.get_scheduled_workouts(y, m) or {}).get("calendarItems", []):
            ident = (it.get("itemType"), it.get("id"), it.get("date"))
            if ident in seen_ids:
                continue  # Garmin repeats Coach items in adjacent months
            seen_ids.add(ident)
            items.append(it)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    plan = coach.discover_active_plan(client, today)
    tasks = []
    if plan:
        tasks = (client.get_adaptive_training_plan_by_id(plan["id"]) or {}).get("taskList") or []
    return items, tasks, plan


def run(dry_run: bool = False, settings: dict | None = None) -> dict:
    t_start = time.time()
    settings = settings or load_settings()
    cfg = settings.get("gcal_sync") or {}
    mode = "dry_run" if dry_run else cfg.get("mode", "off")
    if mode == "off":
        return {"ok": True, "skipped": "gcal_sync.mode is off"}
    from collectors.db import get_connection  # noqa
    from collectors.garmin_collector import _get_garmin_client  # noqa

    today = date.today()
    window_end = today + timedelta(days=int(cfg.get("window_days", 45)))
    client = _get_garmin_client()
    items, tasks, plan = fetch_garmin(client, today, window_end)

    def workout_minutes(wid):
        try:
            return (client.get_workout_by_id(wid) or {}).get("estimatedDurationInSecs")
        except Exception as e:  # a lift with no detail just gets the default length
            log.warning("workout %s detail failed: %s", wid, e)
            return None

    desired = desired_events(items, tasks, settings, today, window_end, workout_minutes)
    cal = cfg.get("calendar_id", "primary")
    existing = gcal({"calendar": cal, "tag": TAG,
                     "list": {"start": today.isoformat(), "end": window_end.isoformat()}})["existing"]
    ops = diff(desired, existing, today)
    out = {"ok": True, "mode": mode, "calendar": cal, "plan": (plan or {}).get("id"), "window": [today.isoformat(), window_end.isoformat()],
           "garmin_items": len(items), "desired": len(desired), "existing": len(existing),
           "ops": [{k: o[k] for k in ("op", "key", "date")} for o in ops]}
    if mode == "dry_run":
        out["bodies"] = {o["key"]: o.get("body") for o in ops if o.get("body")}
    elif ops:
        res = gcal({"calendar": cal, "tag": TAG, "ops": [{k: v for k, v in o.items() if k != "date"} for o in ops]})["results"]
        out["results"] = res
        out["ok"] = all(r.get("ok") for r in res)
    out["duration_ms"] = int((time.time() - t_start) * 1000)
    conn = get_connection()
    try:
        store.set_state(conn, "gcal_sync_last", {"at": store.now_iso(), "mode": mode, "ok": out["ok"],
                                                 "ops": len(ops), "desired": len(desired),
                                                 "errors": [r for r in out.get("results", []) if not r.get("ok")]})
    finally:
        conn.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    socket.setdefaulttimeout(30)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"ok": True, "skipped": "another sync is running"}))
            return 0
        out = run(dry_run=a.dry_run)
    print(json.dumps(out, default=str) if a.print else json.dumps({k: out.get(k) for k in ("ok", "mode", "skipped", "duration_ms")}
                                                                   | {"ops": len(out.get("ops", []))}))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
