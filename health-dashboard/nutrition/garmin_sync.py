"""Reconcile planner workouts (lifts now, generated rides later) with the Garmin Connect calendar.

Safety rules:
- Only planner-owned items are ever touched: workout id recorded in the ledger AND name starts with
  'FP · ' AND description carries 'fuel-planner:<plan_key>:'. Garmin Coach items (fbtAdaptiveWorkout)
  and anything else are never modified.
- Past dates are never modified.
- Modes: off (nothing), dry_run (ops are computed + logged, Garmin and the ledger untouched), live.
- At most MAX_WRITES write calls per run; a Garmin error stops the run and sets a 1 h backoff, leaving
  the ledger consistent so the next run continues where this one stopped.
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

from . import store
from . import strength

PREFIX = "FP · "
MAX_WRITES = 12
PAUSE_S = 1.5
BACKOFF_S = 3600


def ledger(conn) -> dict[str, dict]:
    rows = conn.execute("SELECT plan_key, kind, date, spec_hash, name, garmin_workout_id, garmin_schedule_id, status "
                        "FROM fuel_garmin_workouts").fetchall()
    return {r[0]: {"plan_key": r[0], "kind": r[1], "date": r[2], "spec_hash": r[3], "name": r[4],
                   "workout_id": r[5], "schedule_id": r[6], "status": r[7]} for r in rows}


def owned_calendar_items(items: list[dict], led: dict[str, dict]) -> dict[int, dict]:
    """Planner-owned scheduled workouts on the Garmin calendar, keyed by schedule id."""
    wids = {v["workout_id"] for v in led.values() if v.get("workout_id")}
    out = {}
    for it in items:
        if it.get("itemType") != "workout":
            continue
        if not (it.get("title") or "").startswith(PREFIX):
            continue
        if it.get("workoutId") not in wids:
            continue
        out[it["id"]] = {"schedule_id": it["id"], "workout_id": it.get("workoutId"), "date": it.get("date")}
    return out


def diff(desired: list[dict], led: dict[str, dict], owned: dict[int, dict], today: date, window_end: date,
         keep_dates=frozenset()) -> list[dict]:
    """Pure: the operations that bring Garmin in line with `desired` inside [today, window_end].
    keep_dates: days with a completed lift; their scheduled workouts are never removed."""
    t, w = today.isoformat(), window_end.isoformat()
    want = {d["plan_key"]: d for d in desired if t <= d["date"] <= w}
    by_workout = {v["workout_id"]: v for v in owned.values()}
    ops = []
    for key, d in want.items():
        L = led.get(key)
        if L is None or L["status"] in ("removed", "error") or not L.get("workout_id"):
            ops.append({"op": "create", "plan_key": key, "date": d["date"], "spec": d})
            continue
        if L["status"] == "user_removed":
            continue  # Ian deleted it in Garmin Connect; don't fight him
        cal = by_workout.get(L["workout_id"])
        if cal is None:
            ops.append({"op": "mark_user_removed", "plan_key": key, "date": L["date"]})
            continue
        if cal["date"] != L["date"]:
            # moved by hand in Garmin Connect: adopt his date if it is still the desired lift's week
            ops.append({"op": "adopt_move", "plan_key": key, "date": cal["date"], "from": L["date"],
                        "schedule_id": cal["schedule_id"]})
            continue
        if L["spec_hash"] != d["spec_hash"]:
            ops.append({"op": "replace", "plan_key": key, "date": d["date"], "spec": d, "old": L})
        elif L["date"] != d["date"]:
            ops.append({"op": "move", "plan_key": key, "date": d["date"], "spec": d, "old": L})
    for key, L in led.items():
        if key in want or L["status"] not in ("synced", "moved_by_user") or not L.get("workout_id"):
            continue
        if L["date"] < t or L["date"] in keep_dates:
            continue  # history (and a lift done today) is never modified
        cal = by_workout.get(L["workout_id"])  # a lift moved by hand has a new calendar entry
        old = {**L, "schedule_id": cal["schedule_id"]} if cal else L
        ops.append({"op": "remove", "plan_key": key, "date": L["date"], "old": old})
    return ops


def _payload(spec: dict) -> dict:
    if spec.get("kind", "lift") == "lift":
        return strength.to_garmin_json(spec)
    from . import rides
    return rides.to_garmin_json(spec)


def _upsert(conn, spec_or_old: dict, **fields) -> None:
    now = store.now_iso()
    cur = conn.execute("SELECT 1 FROM fuel_garmin_workouts WHERE plan_key = ?", (spec_or_old["plan_key"],)).fetchone()
    if cur:
        sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ?"
        conn.execute(f"UPDATE fuel_garmin_workouts SET {sets} WHERE plan_key = ?",
                     (*fields.values(), now, spec_or_old["plan_key"]))
    else:
        conn.execute("INSERT INTO fuel_garmin_workouts (plan_key, kind, date, spec_hash, name, garmin_workout_id,"
                     " garmin_schedule_id, status, reasons_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (spec_or_old["plan_key"], spec_or_old.get("kind", "lift"), fields.get("date", spec_or_old["date"]),
                      fields.get("spec_hash"), fields.get("name"), fields.get("garmin_workout_id"),
                      fields.get("garmin_schedule_id"), fields.get("status", "creating"),
                      json.dumps(spec_or_old.get("reasons", [])), now, now))
    conn.commit()


def _log(conn, run_id, mode, op, ok, detail="", workout_id=None, schedule_id=None):
    conn.execute("INSERT INTO fuel_garmin_ops (ts, run_id, mode, op, plan_key, workout_id, schedule_id, date, ok, detail)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?)", (store.now_iso(), run_id, mode, op["op"], op["plan_key"], workout_id,
                                                   schedule_id, op["date"], int(ok), detail[:500]))
    conn.commit()


def _schedule_id(resp) -> int | None:
    if isinstance(resp, dict):
        for k in ("workoutScheduleId", "scheduleId", "id"):
            if resp.get(k):
                return int(resp[k])
    return None


def apply(client, conn, ops: list[dict], mode: str, run_id: int | None = None) -> dict:
    """Execute ops. Returns {'mode', 'planned', 'done', 'stopped'}."""
    res = {"mode": mode, "planned": len(ops), "done": 0, "stopped": None}
    if mode == "off" or not ops:
        return res
    if mode == "dry_run":
        for op in ops:
            _log(conn, run_id, mode, op, True, "dry run")
        res["done"] = len(ops)
        return res
    backoff = store.get_state(conn, "garmin_backoff_until") or 0
    if time.time() < float(backoff):
        res["stopped"] = "backoff"
        return res
    writes = 0

    def write(fn, *a):
        nonlocal writes
        if writes >= MAX_WRITES:
            raise StopIteration("write cap")
        if writes:
            time.sleep(PAUSE_S)
        writes += 1
        return fn(*a)

    for op in ops:
        try:
            kind = op["op"]
            if kind == "create":
                spec = op["spec"]
                _upsert(conn, spec, date=spec["date"], spec_hash=spec["spec_hash"], name=spec["name"], status="creating")
                wid = int(write(client.upload_workout, _payload(spec))["workoutId"])
                _upsert(conn, spec, garmin_workout_id=wid)
                sid = _schedule_id(write(client.schedule_workout, wid, spec["date"]))
                _upsert(conn, spec, garmin_schedule_id=sid, status="synced")
                _log(conn, run_id, mode, op, True, workout_id=wid, schedule_id=sid)
            elif kind == "move":
                old, spec = op["old"], op["spec"]
                if old.get("schedule_id"):
                    write(client.unschedule_workout, old["schedule_id"])
                sid = _schedule_id(write(client.schedule_workout, old["workout_id"], spec["date"]))
                _upsert(conn, spec, date=spec["date"], garmin_schedule_id=sid, status="synced")
                _log(conn, run_id, mode, op, True, workout_id=old["workout_id"], schedule_id=sid)
            elif kind == "replace":
                old, spec = op["old"], op["spec"]
                wid = int(write(client.upload_workout, _payload(spec))["workoutId"])
                sid = _schedule_id(write(client.schedule_workout, wid, spec["date"]))
                _upsert(conn, spec, date=spec["date"], spec_hash=spec["spec_hash"], name=spec["name"],
                        garmin_workout_id=wid, garmin_schedule_id=sid, status="synced")
                if old.get("schedule_id"):
                    write(client.unschedule_workout, old["schedule_id"])
                write(client.delete_workout, old["workout_id"])
                _log(conn, run_id, mode, op, True, workout_id=wid, schedule_id=sid)
            elif kind == "remove":
                old = op["old"]
                if old.get("schedule_id"):
                    write(client.unschedule_workout, old["schedule_id"])
                write(client.delete_workout, old["workout_id"])
                _upsert(conn, old, status="removed")
                _log(conn, run_id, mode, op, True, workout_id=old["workout_id"])
            elif kind == "mark_user_removed":
                _upsert(conn, op, status="user_removed")
                _log(conn, run_id, mode, op, True, "deleted in Garmin Connect; respected")
            elif kind == "adopt_move":
                _upsert(conn, op, date=op["date"], status="moved_by_user",
                        garmin_schedule_id=op.get("schedule_id"))
                _log(conn, run_id, mode, op, True, f"moved by hand from {op['from']}; adopted")
            res["done"] += 1
        except StopIteration:
            res["stopped"] = "write cap"
            break
        except Exception as e:
            _log(conn, run_id, mode, op, False, f"{type(e).__name__}: {e}")
            store.set_state(conn, "garmin_backoff_until", time.time() + BACKOFF_S)
            res["stopped"] = f"{type(e).__name__}"
            break
    return res


def calendar_items(client, today: date, window_end: date) -> list[dict]:
    months = sorted({(today.year, today.month), (window_end.year, window_end.month)})
    items = []
    for y, m in months:
        items += (client.get_scheduled_workouts(y, m) or {}).get("calendarItems", [])
    return items


def reconcile(client, conn, desired: list[dict], mode: str, today: date, sync_days: int, run_id: int | None = None,
              keep_dates=frozenset()) -> dict:
    window_end = today + timedelta(days=sync_days)
    led = ledger(conn)
    owned = owned_calendar_items(calendar_items(client, today, window_end), led) if mode != "off" else {}
    ops = diff(desired, led, owned, today, window_end, keep_dates)
    out = apply(client, conn, ops, mode, run_id)
    out["ops"] = [{k: v for k, v in op.items() if k not in ("spec", "old")} | {"name": (op.get("spec") or {}).get("name")}
                  for op in ops]
    return out
