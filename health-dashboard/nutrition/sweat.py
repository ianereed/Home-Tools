"""Ride sweat sodium -> a negative-sodium MyFitnessPal entry on the ride's day.

    .venv/bin/python3 -m nutrition.sweat [--mode dry_run|live] [--print]

For each recent Garmin ride (settings.sweat_sodium.activity_types) the loss is
    sodium_mg = sweat litres x mg_per_l
with sweat litres from Garmin's activity detail (summaryDTO.waterEstimated, mL; present on Ian's
Edge rides, probed 2026-10-16) or, when missing, duration x fallback_sweat_ml_per_h. mg_per_l is
Ian's number and has no default: while it is unset nothing is logged. override_mg pins one ride.

The ledger (fuel_sweat_sodium) keeps one row per activity with the MFP entry the planner owns, so a
re-run skips an unchanged ride, replaces an entry whose mg changed (delete, then log) and never
logs a second one. Runs inside nutrition.refresh after the Garmin collect and before the MFP fetch,
so the fetched day totals already include the entry. dry_run records what it would log and never
calls MFP.
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
from datetime import date, datetime, timedelta

log = logging.getLogger("nutrition.sweat")


def water_from_detail(detail: dict | None) -> float | None:
    v = ((detail or {}).get("summaryDTO") or {}).get("waterEstimated")
    return float(v) if v else None


def estimate(activity: dict, water_ml: float | None, cfg: dict) -> dict:
    """-> {'water_ml', 'water_source', 'mg_per_l', 'sodium_mg' (None while mg_per_l is unset), 'override'}."""
    source = "garmin"
    if not water_ml:
        water_ml = (activity.get("duration_s") or 0) / 3600 * float(cfg.get("fallback_sweat_ml_per_h") or 0)
        source = "fallback"
    conc = cfg.get("mg_per_l")
    override = (cfg.get("override_mg") or {}).get(str(activity["id"]))
    if override is not None:
        mg = int(round(float(override)))
    elif conc:
        mg = int(round(water_ml / 1000 * float(conc)))
    else:
        mg = None
    if mg is not None and mg < int(cfg.get("min_mg") or 0):
        mg = 0
    return {"water_ml": round(water_ml), "water_source": source, "mg_per_l": conc, "sodium_mg": mg,
            "override": override is not None}


def candidates(conn, cfg: dict, today: date) -> list[dict]:
    lo = (today - timedelta(days=int(cfg.get("lookback_days", 3)))).isoformat()
    if cfg.get("since") and cfg["since"] > lo:
        lo = cfg["since"]
    types = list(cfg.get("activity_types") or [])
    if not types:
        return []
    rows = conn.execute(
        f"SELECT source_id, date, type, name, COALESCE(duration_s, duration_minutes * 60) FROM activities "
        f"WHERE source = 'garmin' AND dup_of IS NULL AND date BETWEEN ? AND ? AND type IN ({','.join('?' * len(types))}) "
        f"ORDER BY start_time", (lo, today.isoformat(), *types)).fetchall()
    min_s = float(cfg.get("min_minutes") or 0) * 60
    return [{"id": str(r[0]), "date": r[1], "type": r[2], "name": r[3], "duration_s": r[4]}
            for r in rows if (r[4] or 0) >= min_s]


def ledger(conn) -> dict[str, dict]:
    cols = ("activity_id", "date", "water_ml", "water_source", "mg_per_l", "sodium_mg", "mode", "status",
            "mfp_entry_id", "logged_mg", "detail", "updated_at")
    return {r[0]: dict(zip(cols, r)) for r in conn.execute(f"SELECT {', '.join(cols)} FROM fuel_sweat_sodium")}


def _save(conn, row: dict) -> None:
    row = {**row, "updated_at": datetime.now().isoformat(timespec="seconds")}
    cols = list(row)
    conn.execute(f"INSERT INTO fuel_sweat_sodium ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                 f"ON CONFLICT(activity_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols[1:])}",
                 [row[c] for c in cols])


def plan_ops(act: dict, est: dict, prev: dict | None) -> list[dict]:
    """MFP ops that turn the diary into the estimate for this ride."""
    want = est["sodium_mg"]
    have_id = (prev or {}).get("mfp_entry_id")
    have_mg = (prev or {}).get("logged_mg")
    if want is None:  # no concentration yet: leave whatever is there alone
        return []
    if have_id and have_mg == want and (prev or {}).get("date") == act["date"]:
        return []
    ops = [{"op": "delete", "key": act["id"], "entry_id": have_id}] if have_id else []
    if want > 0:
        ops.append({"op": "log", "key": act["id"], "date": act["date"], "mg": want})
    return ops


def sync(conn, client, settings: dict, today: date | None = None, mode: str | None = None,
         sweat_ops=None) -> dict:
    """Estimate + reconcile recent rides. Returns a summary for refresh's payload / the CLI."""
    cfg = settings.get("sweat_sodium") or {}
    mode = mode or cfg.get("mode", "off")
    today = today or date.today()
    if mode == "off":
        return {"mode": "off"}
    if mode not in ("dry_run", "live"):
        return {"mode": mode, "error": "mode must be off | dry_run | live"}
    led = ledger(conn)
    rides, ops = [], []
    for act in candidates(conn, cfg, today):
        prev = led.get(act["id"])
        water = (prev or {}).get("water_ml") if (prev or {}).get("water_source") == "garmin" else None
        if water is None:
            try:
                water = water_from_detail(client.get_activity(act["id"]))
            except Exception as e:  # fall back to duration rather than skip the ride
                log.warning("activity %s detail failed: %s", act["id"], e)
        est = estimate(act, water, cfg)
        act_ops = plan_ops(act, est, prev)  # dry_run reports them, live sends them
        status = ("needs_concentration" if est["sodium_mg"] is None else
                  "planned" if act_ops else
                  "skipped" if est["sodium_mg"] == 0 else (prev or {}).get("status") or "logged")
        _save(conn, {"activity_id": act["id"], "date": act["date"], "water_ml": est["water_ml"],
                     "water_source": est["water_source"], "mg_per_l": est["mg_per_l"], "sodium_mg": est["sodium_mg"],
                     "mode": mode, "status": status})
        if mode == "live":
            ops += act_ops
        rides.append({"activity_id": act["id"], "date": act["date"], "type": act["type"], "name": act["name"],
                      "minutes": round((act["duration_s"] or 0) / 60), **est, "ops": [o["op"] for o in act_ops],
                      "mfp_entry_id": (prev or {}).get("mfp_entry_id")})
    out = {"mode": mode, "rides": rides, "ops": len(ops)}
    if ops:
        if sweat_ops is None:
            from collectors.mfp_collector import sweat_ops
        res = sweat_ops(ops)
        out["mfp"] = _apply_results(conn, ops, res)
    conn.commit()
    return out


def _apply_results(conn, ops: list[dict], res: dict) -> str:
    """Record entry ids from whatever ops ran (also on a mid-batch auth failure)."""
    by_key: dict[str, list] = {}
    for op, r in zip(ops, res.get("results") or []):
        by_key.setdefault(op["key"], []).append((op, r))
    for key, pairs in by_key.items():
        row = conn.execute("SELECT mfp_entry_id, logged_mg FROM fuel_sweat_sodium WHERE activity_id = ?",
                           (key,)).fetchone()
        entry_id, logged_mg = row
        status, detail = "logged", None
        for op, r in pairs:
            if not r.get("ok"):
                status, detail = "error", r.get("error")
                break
            if op["op"] == "delete":
                entry_id, logged_mg = None, None
            else:
                entry_id, logged_mg = r["entry_id"], op["mg"]
        if status == "logged" and entry_id is None:
            status = "skipped"  # removed and nothing to log (estimate fell under min_mg)
        conn.execute("UPDATE fuel_sweat_sodium SET mfp_entry_id = ?, logged_mg = ?, status = ?, detail = ? "
                     "WHERE activity_id = ?", (entry_id, logged_mg, status, detail, key))
    if res.get("skipped"):
        return res["skipped"]
    if not res.get("ok"):
        return "auth_expired" if res.get("auth") else f"error ({res.get('error', '?')[:80]})"
    return "ok"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dry_run", "live"], help="override settings.sweat_sodium.mode")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    socket.setdefaulttimeout(30)
    import fcntl

    from collectors.db import get_connection, init_db
    from collectors.garmin_collector import _get_garmin_client

    from .refresh import LOCK_PATH
    from .settings import load_settings
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as lf:  # one MFP session user at a time: never beside a refresh
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"skipped": "a refresh is running; retry in a minute"}))
            return 1
        init_db()
        conn = get_connection()
        try:
            out = sync(conn, _get_garmin_client(), load_settings(), mode=a.mode)
        finally:
            conn.close()
    print(json.dumps(out, indent=2 if a.print else None, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
