"""Read nas-intake state.json for files-pending / large-file / wedged counts.

Mirrors the contract of `collectors.queues.get_queues()`: returns a dict
that the flowchart and tabs consume directly. Always returns
`{"available": False, "reason": ...}` on missing/bad state — never raises.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from paths import NAS_INTAKE_STATE_PATH


def _parse_age(iso_str: str | None) -> int | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int(time.time() - dt.timestamp()))
    except (ValueError, TypeError):
        return None


def _classify(in_flight: dict, wedged: dict) -> str:
    """Roll up status to a single state code for the flowchart node.
      ok   — no wedges, no stuck-in-flight
      warn — at least one in-flight job has been running longer than 30 min
             (worth a glance, but the watchdog will kill it if hung)
      err  — at least one wedged file (user attention required)
    """
    if wedged:
        return "err"
    for entry in in_flight.values():
        age = _parse_age(entry.get("started_at"))
        if age is not None and age > 1800:
            return "warn"
    return "ok"


@st.cache_data(ttl=5)
def get_nas_intake() -> dict:
    p = NAS_INTAKE_STATE_PATH
    if not p.exists():
        return {"available": False, "reason": f"{p} not found"}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return {"available": False, "reason": f"bad json: {e}"}
    health = data.get("health", {}) or {}
    in_flight = data.get("in_flight_large", {}) or {}
    wedged = data.get("wedged", {}) or {}
    timeout_counts = data.get("timeout_counts", {}) or {}

    in_flight_ages = {
        sha: _parse_age(entry.get("started_at"))
        for sha, entry in in_flight.items()
    }
    wedged_ages = {
        sha: _parse_age(entry.get("wedged_at"))
        for sha, entry in wedged.items()
    }

    return {
        "available": True,
        "files_seen": health.get("files_seen", len(data.get("seen", {}))),
        "files_processed_total": health.get("files_processed_total",
                                            len(data.get("processed_sha256", []))),
        "files_in_flight_large": len(in_flight),
        "files_wedged": len(wedged),
        "files_with_timeouts": len(timeout_counts),
        "in_flight_ages_sec": in_flight_ages,
        "wedged_ages_sec": wedged_ages,
        "wedged_entries": wedged,
        "in_flight_entries": in_flight,
        "timeout_counts": timeout_counts,
        "status": _classify(in_flight, wedged),
        "mtime_age_sec": int(time.time() - p.stat().st_mtime),
        "computed_at_age_sec": _parse_age(health.get("computed_at")),
    }
