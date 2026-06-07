"""Status tab — read-only status panel + links/docs.

Moved here from the sidebar (the old "hideaway" panel) so it lives as a
first-class tab alongside Jobs/Decisions/etc. Editing settings still happens
in YAML/.env files; this is just visibility.
"""
from __future__ import annotations

import socket

import streamlit as st


# app.py renders ALL tabs on every script run (st.tabs renders all panels, not
# just the active one), so any network I/O here fires on every page load and
# every rerun. Cache with a short TTL so a rerun storm (or an autosave loop)
# doesn't hammer the local services on every iteration.

@st.cache_data(ttl=10)
def _jobs_queue_size() -> int | None:
    from console import jobs_client
    return jobs_client.queue_size()


@st.cache_data(ttl=10)
def _jobs_base_url() -> str:
    from console import jobs_client
    return jobs_client.base_url()


@st.cache_data(ttl=15)
def _ollama_model_count() -> int | None:
    """Return number of loaded Ollama models, or None if unreachable."""
    try:
        import requests
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=1)
        return len(r.json().get("models", []))
    except Exception:
        return None


def render() -> None:
    col_status, col_links = st.columns(2)

    with col_status:
        st.subheader("Status")
        st.caption(f"host · `{socket.gethostname()}`")

        # jobs-http
        depth = _jobs_queue_size()
        if depth is None:
            st.caption("jobs http · :x: unreachable")
        else:
            st.caption(f"jobs http · `{_jobs_base_url()}`")
            st.caption(f"queue · {depth} pending")

        # ollama
        n = _ollama_model_count()
        if n is None:
            st.caption("ollama · :x: unreachable")
        else:
            st.caption(f"ollama · :white_check_mark: {n} model(s)")

    with col_links:
        st.subheader("Links")
        st.markdown("[service-monitor](http://homeserver:8502/) — full dashboard")
        st.markdown("[Mac-mini PLAN.md](https://github.com/ianereed/Home-Tools/blob/main/Mac-mini/PLAN.md)")

        st.subheader("Docs")
        st.caption("Edit settings in `~/Home-Tools/jobs/.env` or per-project `.env`. "
                   "Restart the console for changes to take effect.")
