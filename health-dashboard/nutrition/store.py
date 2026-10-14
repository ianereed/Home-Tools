"""Fuel planner persistence: plan runs, Coach snapshots/versions/diffs, key/value state."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import coach as C


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- state
def get_state(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM fuel_state WHERE key = ?", (key,)).fetchone()
    if row is None or row[0] is None:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return row[0]


def set_state(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO fuel_state (key, value, updated_at) VALUES (?, ?, ?)",
                 (key, json.dumps(value, default=str), now_iso()))
    conn.commit()


# ---------------------------------------------------------------- runs
def save_run(conn, *, trigger: str, reason: str, payload: dict | None, snapshot: dict | None, status: str,
             error: str | None = None, duration_ms: int | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO fuel_plan_runs (created_at, trigger, reason, plan_start, plan_end, engine_version, status, error,"
        " duration_ms, payload_json, snapshot_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (now_iso(), trigger, reason, (snapshot or {}).get("plan_start"), (payload or {}).get("plan_end"),
         (payload or {}).get("engine_version"), status, error, duration_ms,
         json.dumps(payload, default=str) if payload is not None else None,
         json.dumps(snapshot, default=str) if snapshot is not None else None))
    run_id = cur.lastrowid
    if payload and status == "ok":
        rows = []
        for pace, plan in payload.get("plans", {}).items():
            for e in plan.get("energy", []):
                rows.append((run_id, e["date"], str(pace), e.get("category"), e.get("source"), e["tdee"], e["kcal"],
                             e["protein_g"], e["carb_g"], e["fat_g"], e.get("sodium_max_mg"), e.get("chol_max_mg"),
                             e.get("satfat_max_g"), e["deficit"]))
        conn.executemany("INSERT OR REPLACE INTO fuel_plan_days VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return run_id


def latest_run(conn, status: str = "ok") -> dict | None:
    row = conn.execute("SELECT id, created_at, trigger, reason, payload_json FROM fuel_plan_runs WHERE status = ? "
                       "ORDER BY id DESC LIMIT 1", (status,)).fetchone()
    if not row:
        return None
    return {"id": row[0], "created_at": row[1], "trigger": row[2], "reason": row[3], "payload": json.loads(row[4])}


def prune_runs(conn, keep_last: int = 40, keep_daily_days: int = 120) -> int:
    """Keep the newest `keep_last` runs plus the last run of each day for `keep_daily_days`."""
    ids = [r[0] for r in conn.execute("SELECT id FROM fuel_plan_runs ORDER BY id DESC LIMIT ?", (keep_last,))]
    cutoff = (datetime.now() - timedelta(days=keep_daily_days)).isoformat()
    ids += [r[0] for r in conn.execute(
        "SELECT MAX(id) FROM fuel_plan_runs WHERE created_at >= ? GROUP BY substr(created_at, 1, 10)", (cutoff,))]
    keep = ",".join(str(i) for i in set(ids)) or "-1"
    cur = conn.execute(f"DELETE FROM fuel_plan_runs WHERE id NOT IN ({keep})")
    conn.execute(f"DELETE FROM fuel_plan_days WHERE run_id NOT IN ({keep})")
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------- coach snapshots
def _wkey(w: dict) -> dict:
    """Comparable view of a Coach workout (what matters for the experiment)."""
    if w.get("rest"):
        return {"rest": True}
    return {"type": w.get("type"), "name": w.get("name"), "duration_s": w.get("duration_s"),
            "steps": w.get("steps"), "description": w.get("description")}


def save_coach_window(conn, plan: dict | None, workouts: list[dict], context: dict | None = None) -> dict:
    """Store the fetched Coach window (dedupe by digest), upsert workout versions, record diffs vs the
    previous snapshot for dates in both windows. Returns {'new_snapshot': bool, 'diffs': n}."""
    dig = C.digest([_wkey(w) | {"date": w["date"]} for w in workouts])
    ts = now_iso()
    prev = conn.execute("SELECT payload_json FROM coach_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    exists = conn.execute("SELECT 1 FROM coach_snapshots WHERE digest = ?", (dig,)).fetchone()
    for w in workouts:
        wd = C.digest(_wkey(w))
        uuid = w.get("uuid") or f"rest-{w['date']}"
        conn.execute("INSERT INTO coach_workout_versions (date, workout_uuid, digest, name, type, duration_s, planned_kj,"
                     " workout_json, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(date, workout_uuid, digest)"
                     " DO UPDATE SET last_seen = excluded.last_seen",
                     (w["date"], uuid, wd, w.get("name"), "REST" if w.get("rest") else w.get("type"), w.get("duration_s"),
                      w.get("planned_kj"), json.dumps(w, default=str), ts, ts))
    n_diffs = 0
    if not exists:
        if prev:
            before = {w["date"]: w for w in json.loads(prev[0])}
            after = {w["date"]: w for w in workouts}
            # Only the overlap of the two windows is comparable; dates outside it just rolled in/out.
            lo, hi = (min(after) if after else ""), (max(before) if before else "")
            for ds in sorted(set(before) | set(after)):
                if not (lo <= ds <= hi):
                    continue
                b, a = before.get(ds), after.get(ds)
                if b is not None and a is not None:
                    if _wkey(b) == _wkey(a):
                        continue
                    change = "changed"
                else:
                    change = "added" if a is not None else "removed"
                conn.execute("INSERT INTO coach_diffs (detected_at, date, change, before_json, after_json, context_json)"
                             " VALUES (?,?,?,?,?,?)", (ts, ds, change, json.dumps(b, default=str) if b else None,
                                                     json.dumps(a, default=str) if a else None,
                                                     json.dumps(context or {}, default=str)))
                n_diffs += 1
        conn.execute("INSERT INTO coach_snapshots (fetched_at, plan_id, window_start, window_end, digest, payload_json)"
                     " VALUES (?,?,?,?,?,?)", (ts, (plan or {}).get("id"), workouts[0]["date"] if workouts else None,
                                              workouts[-1]["date"] if workouts else None, dig,
                                              json.dumps(workouts, default=str)))
    conn.commit()
    return {"new_snapshot": not exists, "diffs": n_diffs}


def cached_coach_detail(conn, uuid: str) -> dict | None:
    row = conn.execute("SELECT workout_json FROM coach_workout_versions WHERE workout_uuid = ? ORDER BY last_seen DESC LIMIT 1",
                       (uuid,)).fetchone()
    return json.loads(row[0]) if row else None


def coach_versions(conn, since: str) -> list[dict]:
    """Latest version per date since `since` (for the projection template)."""
    rows = conn.execute("SELECT date, workout_json, last_seen FROM coach_workout_versions WHERE date >= ? "
                        "ORDER BY date, last_seen", (since,)).fetchall()
    latest = {}
    for ds, wj, _ in rows:
        latest[ds] = json.loads(wj)
    return [latest[k] for k in sorted(latest)]
