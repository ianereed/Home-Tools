"""Tail log files for each service."""
import subprocess
import streamlit as st
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from services import SERVICES

LINES = 20


@st.cache_data(ttl=30)
def tail_all() -> dict:
    """Return {service_id: {available, lines, reason?}} for every service."""
    out = {}
    for svc in SERVICES:
        try:
            text = subprocess.check_output(
                ["tail", "-n", str(LINES), svc.log_path],
                text=True, timeout=3, stderr=subprocess.STDOUT,
            )
            out[svc.id] = {"available": True, "lines": text.splitlines()}
        except subprocess.CalledProcessError as e:
            out[svc.id] = {"available": False, "reason": (e.output or "").strip()[:200]}
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            out[svc.id] = {"available": False, "reason": str(e)}
    return out
