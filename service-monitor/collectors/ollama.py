"""Probe local Ollama API + load-history file."""
import json
import time
from pathlib import Path

import requests
import streamlit as st

URL = "http://127.0.0.1:11434/api/tags"
HISTORY_PATH = Path(
    "~/Library/Application Support/home-tools/ollama_history.json"
).expanduser()


@st.cache_data(ttl=10)
def get_ollama() -> dict:
    t0 = time.monotonic()
    try:
        r = requests.get(URL, timeout=3)
        r.raise_for_status()
        models = [m.get("name") for m in r.json().get("models", [])]
        return {
            "ok": True,
            "models": models,
            "model_count": len(models),
            "response_ms": int((time.monotonic() - t0) * 1000),
            "history": _read_history(),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "history": _read_history(),
        }


def _read_history() -> dict:
    """Read tracker state file. Returns {} if missing/unreadable."""
    if not HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(HISTORY_PATH.read_text())
    except Exception:
        return {}
