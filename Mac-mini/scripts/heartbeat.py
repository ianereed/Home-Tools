#!/usr/bin/env python3
"""Mac mini heartbeat / liveness check.

Runs every 30 minutes via com.home-tools.heartbeat.plist (StartInterval=1800).
Probes launchd agents, HTTP endpoints, and database freshness. Emits a
state-change event to ~/Home-Tools/logs/incidents.jsonl ONLY when an
observation differs from the previous run. Maintains its own private
state in ~/Home-Tools/run/heartbeat-state.json.

Does NOT push notifications. The companion script daily-digest.py reads
incidents.jsonl at 07:00 each morning and posts a Slack summary via
slack-post.sh.

Phase 6 — see Mac-mini/PHASE6.md.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ["HOME"])
RUN_DIR = HOME / "Home-Tools" / "run"
LOGS_DIR = HOME / "Home-Tools" / "logs"
STATE_FILE = RUN_DIR / "heartbeat-state.json"
INCIDENTS_FILE = LOGS_DIR / "incidents.jsonl"
HEALTH_DB = HOME / "Home-Tools" / "health-dashboard" / "data" / "health.db"
HEALTH_DB_STALE_S = 25 * 3600  # 25h

# Long-running KeepAlive agents we expect to see in `launchctl list` with a PID.
# Periodic agents (StartInterval / StartCalendarInterval) drop their PID between
# fires — listing them as expected here would generate false "down" incidents.
# Source of truth for the agent labels: service-monitor/services.py — keep this
# list in sync with the KeepAlive entries there.
EXPECTED_AGENTS = [
    "com.home-tools.dispatcher",
    "com.home-tools.event-aggregator.worker",
    "com.home-tools.finance-monitor",
    "com.home-tools.service-monitor",
    "com.health-dashboard.receiver",
    "com.health-dashboard.streamlit",
]

# (short_name, url) — these endpoints should respond 200 to a simple GET.
ENDPOINTS = [
    ("receiver-8095", "http://127.0.0.1:8095/"),
    ("health-streamlit-8501", "http://127.0.0.1:8501/"),
    ("service-monitor-8502", "http://127.0.0.1:8502/_stcore/health"),
    ("ollama-11434", "http://127.0.0.1:11434/api/tags"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def check_agent(label: str) -> str:
    """Return 'up' if the agent is in launchctl list with a PID, else 'down'."""
    try:
        out = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except Exception:
        return "down"
    for line in out.splitlines():
        # Format: "<pid>\t<status>\t<label>"
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2].strip() == label:
            pid = parts[0].strip()
            return "up" if pid != "-" else "down"
    return "down"


def check_endpoint(url: str) -> str:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if 200 <= resp.status < 300:
                return "up"
            return "down"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return "down"


def check_db_freshness(path: Path, stale_s: int) -> str:
    if not path.exists():
        return "missing"
    age = time.time() - path.stat().st_mtime
    return "fresh" if age < stale_s else "stale"


def collect_current() -> dict[str, str]:
    state: dict[str, str] = {}
    for label in EXPECTED_AGENTS:
        state[f"agent:{label}"] = check_agent(label)
    for name, url in ENDPOINTS:
        state[f"endpoint:{name}"] = check_endpoint(url)
    state["db:health"] = check_db_freshness(HEALTH_DB, HEALTH_DB_STALE_S)
    return state


def read_prior() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_state_atomic(state: dict[str, str]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=str(RUN_DIR), prefix="heartbeat-state.", suffix=".tmp", delete=False
    )
    try:
        json.dump(state, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        shutil.move(tmp.name, STATE_FILE)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


def append_event(event: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with INCIDENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def is_bad_state(value: str) -> bool:
    return value not in ("up", "fresh")


def main() -> int:
    current = collect_current()
    prior = read_prior()
    ts = now_iso()
    new_events = 0

    for key, cur in current.items():
        pri = prior.get(key)
        if pri == cur:
            continue
        if pri is None:
            # First-ever observation. Only emit if state is bad — clean cold-starts
            # shouldn't flood the log on first install.
            if is_bad_state(cur):
                append_event({
                    "ts": ts,
                    "kind": "first_seen_bad",
                    "key": key,
                    "prior": None,
                    "current": cur,
                })
                new_events += 1
        else:
            append_event({
                "ts": ts,
                "kind": "state_change",
                "key": key,
                "prior": pri,
                "current": cur,
            })
            new_events += 1

    write_state_atomic(current)
    bad = sum(1 for v in current.values() if is_bad_state(v))
    print(
        f"heartbeat ok ts={ts} keys={len(current)} bad={bad} new_events={new_events}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
