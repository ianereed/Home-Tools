"""One fuel-planner refresh: collect fresh data, assemble the snapshot, run the engine, persist the run.

    .venv/bin/python3 -m nutrition.refresh --reason manual [--print] [--no-calendar] [--no-collect]

Garmin writes (strength/ride sync) are not part of this phase. Exit code is non-zero only on fatal
errors (DB, engine); individual sources fall back and are reported in payload['sources'].
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import socket
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from collectors.db import get_connection, init_db

from . import calendar_source, coach, engine as E, snapshot as S, store
from .intake import intake_by_date
from .settings import ROOT, load_constraints, load_settings

log = logging.getLogger("nutrition.refresh")
LOCK_PATH = ROOT / "data" / "fuel_refresh.lock"
PROFILE_TTL_S = 24 * 3600


def _ftp_from(raw) -> float | None:
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, dict):
        for k in ("functionalThresholdPower", "functional_threshold_power_watts", "ftp"):
            if raw.get(k):
                return float(raw[k])
    return None


def _cached(conn, key, ttl_s, fetch):
    hit = store.get_state(conn, key)
    if hit and time.time() - hit.get("_ts", 0) < ttl_s:
        return hit["value"], "cached"
    try:
        val = fetch()
        store.set_state(conn, key, {"_ts": time.time(), "value": val})
        return val, "ok"
    except Exception as e:
        log.warning("%s fetch failed: %s", key, e)
        return (hit or {}).get("value"), f"stale ({type(e).__name__})"


def run_refresh(reason: str, trigger: str = "manual", collect: bool = True, calendar: bool = True,
                client=None, today: date | None = None) -> dict:
    t0 = time.time()
    today = today or date.today()
    settings = load_settings()
    constraints = load_constraints()
    sources: dict[str, str] = {}
    init_db()
    conn = get_connection()
    try:
        if client is None:
            from collectors.garmin_collector import _get_garmin_client
            client = _get_garmin_client()

        # 1) fresh activity + energy + food rows
        if collect:
            from collectors import garmin_collector as G, mfp_collector as M
            try:
                G.collect_activities(client, (today - timedelta(days=3)).isoformat(), today.isoformat())
                for i in (1, 0):
                    G.collect_daily_energy(client, (today - timedelta(days=i)).isoformat())
                sources["garmin_activities"] = "ok"
            except Exception as e:
                sources["garmin_activities"] = f"error ({type(e).__name__})"
            try:
                M.collect_mfp(3)
                sources["mfp"] = "ok" if M.mfp_python().exists() else "not installed"
            except M.MFPAuthError:
                sources["mfp"] = "auth_expired"
            except Exception as e:
                sources["mfp"] = f"error ({type(e).__name__})"

        # 2) athlete profile (cached a day)
        us, sources["garmin_profile"] = _cached(
            conn, "garmin_user_settings", PROFILE_TTL_S,
            lambda: client.connectapi("/userprofile-service/userprofile/user-settings"))
        ftp_raw, sources["garmin_ftp"] = _cached(conn, "garmin_ftp", PROFILE_TTL_S, client.get_cycling_ftp)
        athlete = S.athlete_from_garmin(us, _ftp_from(ftp_raw), settings.get("athlete_fallback", {}))

        # 3) Coach window (+ snapshot/diffs for the experiment)
        plan, workouts = None, []
        try:
            plan = coach.discover_active_plan(client, today)
            if plan:
                workouts = coach.fetch_window(client, plan["id"], lambda u: store.cached_coach_detail(conn, u))
                for w in workouts:
                    w["planned_kj"] = round(sum(d * p for d, p in E.flatten_steps(w["steps"])) / 1000) if w.get("steps") else None
                store.save_coach_window(conn, plan, workouts)
            sources["coach"] = "ok" if plan else "no active plan"
        except Exception as e:
            log.warning("Coach fetch failed: %s", e)
            sources["coach"] = f"stale ({type(e).__name__})"
            cached = store.get_state(conn, "coach_window_cache") or {}
            plan, workouts = cached.get("plan"), cached.get("workouts", [])
        else:
            store.set_state(conn, "coach_window_cache", {"plan": plan, "workouts": workouts})
        versions = store.coach_versions(conn, (today - timedelta(days=21)).isoformat())
        if plan is None:
            gen_since = store.get_state(conn, "generated_since")
            if not gen_since:
                gen_since = (today - timedelta(days=today.weekday())).isoformat()
                store.set_state(conn, "generated_since", gen_since)
        else:
            gen_since = plan["end"]

        # 4) calendar (cached fallback)
        events = []
        if calendar and settings.get("calendar_ids"):
            try:
                raw = calendar_source.fetch_raw(settings["calendar_ids"], today.isoformat(), settings["horizon_end"])
                events = calendar_source.classify(raw, settings)
                store.set_state(conn, "calendar_cache", events)
                sources["calendar"] = "ok"
            except Exception as e:
                log.warning("calendar fetch failed: %s", e)
                events = store.get_state(conn, "calendar_cache") or []
                sources["calendar"] = f"stale ({type(e).__name__})"
        else:
            sources["calendar"] = "off"

        # 5) DB-backed inputs
        dexa, weight = S.db_body(conn)
        if dexa is None:
            raise RuntimeError("no body composition (DEXA or Garmin lean mass) in body_composition")
        lo = (today - timedelta(days=14)).isoformat()
        acts = S.db_activities(conn, lo, settings["horizon_end"])
        stats = S.db_daily_stats(conn, lo, today.isoformat())
        intake = intake_by_date(conn, lo, today.isoformat())
        gen_d = date.fromisoformat(gen_since) if isinstance(gen_since, str) else None
        gen_d = gen_d + timedelta(days=(7 - gen_d.weekday()) % 7) if (gen_d and plan) else gen_d

        snap = S.assemble(today=today, settings=settings, constraints=constraints, athlete=athlete, dexa=dexa,
                          weight_kg=weight, coach_plan=plan, coach_workouts=workouts, coach_versions=versions,
                          activities=acts, daily_stats=stats, intake=intake, calendar_events=events,
                          generated_since=gen_d)
        paces = list(settings.get("paces", [])) + ["plan"]
        if snap["daily_stats_prev_14d"]:
            hist = E.history(snap, ["plan"])
            snap["week_to_date"] = S.week_to_date(snap, hist["retro"]["plan"])

        # 6) engine
        res = E.run(snap, paces, meal_paces=["plan"])
        payload = {**res, "sources": sources, "reason": reason, "trigger": trigger,
                   "pace_schedule": settings.get("pace_schedule"), "intake": intake,
                   "coach": {"plan": plan, "window": workouts}, "dexa": dexa, "weight_kg": weight,
                   "athlete": {k: v for k, v in athlete.items() if k not in ("birth_date",)}}
        dur = int((time.time() - t0) * 1000)
        run_id = store.save_run(conn, trigger=trigger, reason=reason, payload=payload, snapshot=snap, status="ok",
                                duration_ms=dur)
        store.prune_runs(conn)
        latest = conn.execute("SELECT source_id FROM activities WHERE source='garmin' ORDER BY start_time DESC LIMIT 1").fetchone()
        if latest:
            store.set_state(conn, "last_refreshed_activity_id", latest[0])
        store.set_state(conn, "refresh_requested", 0)
        return {"ok": True, "run_id": run_id, "duration_ms": dur, "sources": sources, "plan_start": res["plan_start"],
                "plan_end": res["plan_end"], "payload_bytes": len(json.dumps(payload, default=str))}
    except Exception as e:
        dur = int((time.time() - t0) * 1000)
        store.save_run(conn, trigger=trigger, reason=reason, payload=None, snapshot=None, status="error",
                       error=f"{type(e).__name__}: {e}", duration_ms=dur)
        raise
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="manual")
    ap.add_argument("--trigger", default="manual")
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--no-calendar", action="store_true")
    ap.add_argument("--no-collect", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    socket.setdefaulttimeout(30)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            conn = get_connection()
            try:
                store.set_state(conn, "rerun_requested", 1)
            finally:
                conn.close()
            print(json.dumps({"ok": True, "skipped": "another refresh is running; rerun requested"}))
            return 0
        out = run_refresh(a.reason, a.trigger, collect=not a.no_collect, calendar=not a.no_calendar)
    conn = get_connection()
    try:
        rerun = store.get_state(conn, "rerun_requested")
        if rerun:
            store.set_state(conn, "rerun_requested", 0)
    finally:
        conn.close()
    if rerun:
        out["rerun"] = run_refresh("rerun", a.trigger, collect=not a.no_collect, calendar=not a.no_calendar)
    print(json.dumps(out, default=str) if a.print else json.dumps({k: out[k] for k in ("ok", "run_id", "duration_ms")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
