"""Cheap poll: has a new Garmin activity synced (or a refresh been requested, or a new day started) since the last refresh?

    .venv/bin/python3 -m nutrition.watch [--mark-enqueued]

One Garmin call. Prints {"changed": bool, "reason": str}. The jobs kind enqueues a refresh when changed.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from datetime import date

from collectors.db import get_connection, init_db

from . import store

DEBOUNCE_S = 20 * 60


def check(conn, latest_id: str | None, now: float | None = None, today: str | None = None) -> dict:
    now = now or time.time()
    enq = store.get_state(conn, "refresh_enqueued_at") or 0
    if now - float(enq) < DEBOUNCE_S:
        return {"changed": False, "reason": "refresh already enqueued recently"}
    if store.get_state(conn, "last_refresh_day") != (today or date.today().isoformat()):
        return {"changed": True, "reason": "first refresh of the day (resets MFP to the no-exercise goal)"}
    if store.get_state(conn, "refresh_requested"):
        return {"changed": True, "reason": "refresh requested from the dashboard"}
    last = store.get_state(conn, "last_refreshed_activity_id")
    if latest_id and str(latest_id) != str(last):
        return {"changed": True, "reason": f"new activity {latest_id}"}
    return {"changed": False, "reason": "no new activity"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark-enqueued", action="store_true")
    a = ap.parse_args(argv)
    socket.setdefaulttimeout(30)
    init_db()
    conn = get_connection()
    try:
        if a.mark_enqueued:
            store.set_state(conn, "refresh_enqueued_at", time.time())
            print(json.dumps({"ok": True}))
            return 0
        from collectors.garmin_collector import _get_garmin_client
        acts = _get_garmin_client().get_activities(0, 1) or []
        latest = str(acts[0].get("activityId")) if acts else None
        out = check(conn, latest)
        out["latest_activity_id"] = latest
        print(json.dumps(out))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
