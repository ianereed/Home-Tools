"""Settings sidebar — read-only status panel (TC2). Editing settings happens
in YAML files; this is just visibility."""
from __future__ import annotations

import socket
from pathlib import Path

import streamlit as st


def render() -> None:
    st.markdown("### Status")
    st.caption(f"host · `{socket.gethostname()}`")

    # huey storage
    try:
        from jobs import huey
        st.caption(f"jobs db · `…/{Path(huey.storage.filename).name}`")
        st.caption(f"queue · {huey.storage.queue_size()} pending")
    except Exception as exc:
        st.caption(f"jobs db · :x: {exc}")

    # ollama
    try:
        import requests
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=1)
        models = r.json().get("models", [])
        st.caption(f"ollama · :white_check_mark: {len(models)} model(s)")
    except Exception:
        st.caption("ollama · :x: unreachable")

    # service-monitor link
    st.markdown("### Links")
    st.markdown("[service-monitor](http://homeserver:8502/) — full dashboard")
    st.markdown("[Mac-mini PLAN.md](https://github.com/ianereed/Home-Tools/blob/main/Mac-mini/PLAN.md)")

    # docs pointer
    st.markdown("### Docs")
    st.caption("Edit settings in `~/Home-Tools/jobs/.env` or per-project `.env`. "
               "Restart the console for changes to take effect.")
