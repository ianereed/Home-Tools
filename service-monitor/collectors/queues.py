"""Read event-aggregator state.json for queue depths + last_run times."""
import json
import time
import streamlit as st
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from paths import EVT_STATE_PATH


def _parse_age(iso_str: str | None) -> int | None:
    """Return age in seconds from ISO timestamp, or None on missing/bad value."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int(time.time() - dt.timestamp()))
    except (ValueError, TypeError):
        return None


@st.cache_data(ttl=5)
def get_queues() -> dict:
    p = EVT_STATE_PATH
    if not p.exists():
        return {"available": False, "reason": "state.json not found"}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return {"available": False, "reason": f"bad json: {e}"}
    written_events = data.get("written_events", {})
    return {
        "available": True,
        "text_queue_depth": len(data.get("text_queue", [])),
        "ocr_queue_depth": len(data.get("ocr_queue", [])),
        "last_run": data.get("last_run", {}),
        "ollama_health": data.get("ollama_health", {}),
        "pending_proposals_count": sum(
            1 for b in data.get("pending_proposals", [])
            for i in b.get("items", [])
            if i.get("status") == "pending"
        ),
        "file_size_bytes": p.stat().st_size,
        "mtime_age_sec": int(time.time() - p.stat().st_mtime),
        "last_run_ages_sec": {
            src: _parse_age(ts)
            for src, ts in data.get("last_run", {}).items()
        },
        "worker_updated_age_sec": _parse_age(
            data.get("worker_status", {}).get("updated_at")
        ),
        "last_event_written_age_sec": _parse_age(
            max(
                (v.get("created_at", "") for v in written_events.values()),
                default=None,
            ) if written_events else None
        ),
    }
