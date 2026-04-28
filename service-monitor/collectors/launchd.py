"""Parse `launchctl list` to determine running state of each LaunchAgent."""
import subprocess
import streamlit as st
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from services import SERVICES, SERVICES_BY_LABEL


@st.cache_data(ttl=5)
def get_status() -> dict:
    """Return {service_id: {state, pid, last_exit}} for every service in SERVICES."""
    try:
        out = subprocess.check_output(
            ["launchctl", "list"], text=True, timeout=5, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return {s.id: {"state": "unknown", "pid": None, "last_exit": None} for s in SERVICES}

    seen: dict[str, tuple] = {}
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid_str, status_str, label = parts[0], parts[1], parts[2]
        if label in SERVICES_BY_LABEL:
            seen[label] = (pid_str, status_str)

    result = {}
    for s in SERVICES:
        if s.label not in seen:
            result[s.id] = {"state": "err", "pid": None, "last_exit": None}
            continue
        pid_str, status_str = seen[s.label]
        pid = int(pid_str) if pid_str != "-" else None
        try:
            last_exit = int(status_str)
        except ValueError:
            last_exit = None
        running = pid is not None
        if running and (last_exit == 0 or last_exit is None):
            state = "ok"
        elif running:
            state = "warn"
        else:
            state = "err"
        result[s.id] = {"state": state, "pid": pid, "last_exit": last_exit}
    return result
