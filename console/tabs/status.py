"""Status tab — read-only status panel + links/docs.

Moved here from the sidebar (the old "hideaway" panel) so it lives as a
first-class tab alongside Jobs/Decisions/etc. Editing settings still happens
in YAML/.env files; this is just visibility.
"""
from __future__ import annotations

import socket

import streamlit as st


def render() -> None:
    col_status, col_links = st.columns(2)

    with col_status:
        st.subheader("Status")
        st.caption(f"host · `{socket.gethostname()}`")

        # jobs-http
        from console import jobs_client
        depth = jobs_client.queue_size()
        if depth is None:
            st.caption("jobs http · :x: unreachable")
        else:
            st.caption(f"jobs http · `{jobs_client.base_url()}`")
            st.caption(f"queue · {depth} pending")

        # ollama
        try:
            import requests
            r = requests.get("http://127.0.0.1:11434/api/tags", timeout=1)
            models = r.json().get("models", [])
            st.caption(f"ollama · :white_check_mark: {len(models)} model(s)")
        except Exception:
            st.caption("ollama · :x: unreachable")

    with col_links:
        st.subheader("Links")
        st.markdown("[service-monitor](http://homeserver:8502/) — full dashboard")
        st.markdown("[Mac-mini PLAN.md](https://github.com/ianereed/Home-Tools/blob/main/Mac-mini/PLAN.md)")

        st.subheader("Docs")
        st.caption("Edit settings in `~/Home-Tools/jobs/.env` or per-project `.env`. "
                   "Restart the console for changes to take effect.")
