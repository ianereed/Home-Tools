"""Probe local Ollama API."""
import time
import requests
import streamlit as st

URL = "http://127.0.0.1:11434/api/tags"


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
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
