"""Ask tab — single-shot prompt to a local Ollama model.

Phase 12 v1: bare prompt → streaming response. The model selection lives
in jobs/config (sidebar). No conversation history; that's a Phase 13 ask
once meal-planner shapes what the joint surface needs.
"""
from __future__ import annotations

import os

import requests
import streamlit as st


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def _list_models() -> list[str]:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        r.raise_for_status()
        return sorted([m["name"] for m in r.json().get("models", [])])
    except requests.RequestException:
        return []


def render() -> None:
    models = _list_models()
    if not models:
        st.warning(f"Ollama unreachable at {OLLAMA_URL}. Start it with `brew services start ollama`.")
        return
    default = "qwen3:14b" if "qwen3:14b" in models else models[0]
    model = st.selectbox("Model", models, index=models.index(default))
    prompt = st.text_area("Prompt", height=120)
    if st.button("Send", type="primary", disabled=not prompt.strip()):
        body = {"model": model, "prompt": prompt, "stream": True}
        # qwen3 needs think:false to return JSON in the rest of the system,
        # but plain prompt mode is fine.
        try:
            with requests.post(f"{OLLAMA_URL}/api/generate", json=body, stream=True, timeout=300) as r:
                r.raise_for_status()
                placeholder = st.empty()
                acc = ""
                for line in r.iter_lines():
                    if not line:
                        continue
                    import json as _json
                    chunk = _json.loads(line)
                    acc += chunk.get("response", "")
                    placeholder.markdown(acc)
        except requests.RequestException as exc:
            st.error(f"Ollama error: {exc}")
