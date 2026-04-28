"""Service monitor dashboard — single Streamlit page."""
import sys
import os

# Ensure project root is on sys.path for absolute imports
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from collectors.launchd import get_status
from collectors.queues import get_queues
from collectors.databases import get_health_db, get_finance_db
from collectors.logs import tail_all
from collectors.ollama import get_ollama
from services import SERVICES
from flowchart import render_dataflow

st.set_page_config(page_title="Mac mini Services", layout="wide")

# Compact dark CSS — matches health-dashboard visual style
st.markdown("""
<meta name="theme-color" content="#0e1117">
<style>
html { background-color: #0e1117 !important; }
.block-container { padding-top: 1.5rem; padding-bottom: 0; max-width: 100%; }
html, body, [data-testid="stAppViewContainer"], .stApp { background-color: #0e1117 !important; }
h1, h2, h3 { font-size: 1.1rem !important; margin-top: 0.3rem !important; margin-bottom: 0.15rem !important; }
[data-testid="stMetric"] { padding: 0.2rem 0 !important; }
[data-testid="stMetricValue"] { font-size: 1.3rem !important; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
.stTabs [data-baseweb="tab-list"] { gap: 0; }
.stTabs [data-baseweb="tab"] { padding: 0.3rem 0.6rem; font-size: 0.85rem; }
.stMarkdown p { font-size: 0.85rem; margin-bottom: 0.2rem; }
hr { margin: 0.3rem 0 !important; }
[data-testid="stHorizontalBlock"] { gap: 0.3rem; }
[data-testid="stVerticalBlock"] > div { gap: 0.3rem; }
.stDataFrame { font-size: 0.8rem; }
</style>
""", unsafe_allow_html=True)

# 30-second auto-refresh
st_autorefresh(interval=30_000, key="svc_monitor_refresh")

# Collect all data — each function handles its own errors
try:
    status = get_status()
except Exception as e:
    status = {}
    st.warning(f"launchd collector error: {e}")

try:
    queues = get_queues()
except Exception as e:
    queues = {"available": False, "reason": str(e)}

try:
    hdb = get_health_db()
except Exception as e:
    hdb = {"available": False, "reason": str(e)}

try:
    fdb = get_finance_db()
except Exception as e:
    fdb = {"available": False, "reason": str(e)}

try:
    ollama = get_ollama()
except Exception as e:
    ollama = {"ok": False, "error": str(e)}

# Global status indicator
states = {s.get("state") for s in status.values()} if status else {"unknown"}
if "err" in states:
    global_emoji, global_label = "🔴", "service down"
elif "warn" in states:
    global_emoji, global_label = "🟡", "warnings"
elif "unknown" in states and len(states) == 1:
    global_emoji, global_label = "⚫", "unknown"
else:
    global_emoji, global_label = "🟢", "all systems ok"

st.markdown(
    f"## Mac mini Services &nbsp;"
    f'<span style="font-size:0.9rem;font-weight:normal;color:#8b95a5;">'
    f"{global_emoji} {global_label} · auto-refresh 30s</span>",
    unsafe_allow_html=True,
)

# Data-flow swim lanes
st.markdown(render_dataflow(status, queues, ollama), unsafe_allow_html=True)

st.divider()

# Quick metrics row
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("text_queue",    queues.get("text_queue_depth", "?") if queues.get("available") else "—")
c2.metric("ocr_queue",     queues.get("ocr_queue_depth",  "?") if queues.get("available") else "—")
c3.metric("Ollama models", ollama.get("model_count", "✗") if ollama.get("ok") else "✗")

hdb_size = f"{hdb.get('size_bytes', 0) // (1024*1024)} MB" if hdb.get("available") else "—"
fdb_size = f"{fdb.get('size_bytes', 0) // (1024*1024)} MB" if fdb.get("available") else "—"
c4.metric("health.db",  hdb_size)
c5.metric("finance.db", fdb_size)

st.divider()

# Tabs
tab_services, tab_data, tab_logs = st.tabs(["Services", "Queues & DBs", "Logs"])

with tab_services:
    rows = []
    for svc in SERVICES:
        s = status.get(svc.id, {})
        state = s.get("state", "unknown")
        emoji = {"ok": "🟢", "warn": "🟡", "err": "🔴", "unknown": "⚫"}.get(state, "⚫")
        pid_val = s.get("pid")
        exit_val = s.get("last_exit")
        rows.append({
            "Service": f"{emoji}  {svc.label}",
            "Project": svc.project,
            "Schedule": svc.schedule,
            "PID": str(pid_val) if pid_val is not None else "—",
            "Last exit": str(exit_val) if exit_val is not None else "—",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

with tab_data:
    st.subheader("event-aggregator queues")
    if queues.get("available"):
        def _age(sec: int) -> str:
            if sec < 60:
                return f"{sec}s ago"
            elif sec < 3600:
                return f"{sec // 60}m ago"
            return f"{sec // 3600}h ago"

        st.json({
            "text_queue_depth": queues["text_queue_depth"],
            "ocr_queue_depth": queues["ocr_queue_depth"],
            "pending_proposals": queues["pending_proposals_count"],
            "ollama_health": queues["ollama_health"] or "OK",
            "last_run": queues["last_run"],
            "state_file_updated": _age(queues["mtime_age_sec"]),
        })
    else:
        st.warning(f"state.json unavailable: {queues.get('reason')}")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("health.db")
        if hdb.get("available"):
            def _age_str(sec: int) -> str:
                if sec < 120:
                    return f"{sec}s ago"
                elif sec < 7200:
                    return f"{sec // 60}m ago"
                return f"{sec // 3600}h ago"
            st.json({
                "size": f"{hdb['size_bytes'] // (1024*1024)} MB",
                "last_modified": _age_str(hdb["mtime_age_sec"]),
                "rows": hdb["tables"],
            })
        else:
            st.warning(hdb.get("reason", "unavailable"))

    with c2:
        st.subheader("finance.db")
        if fdb.get("available"):
            st.json({
                "size": f"{fdb['size_bytes'] // (1024*1024)} MB",
                "last_modified": _age_str(fdb["mtime_age_sec"]),
                "rows": fdb["tables"],
            })
        else:
            st.warning(fdb.get("reason", "unavailable"))

    st.subheader("Ollama")
    if ollama.get("ok"):
        st.json({
            "models": ollama["models"],
            "response_ms": ollama["response_ms"],
        })
    else:
        st.error(f"Ollama unreachable: {ollama.get('error')}")

with tab_logs:
    try:
        logs = tail_all()
    except Exception as e:
        logs = {}
        st.warning(f"log collector error: {e}")

    for svc in SERVICES:
        with st.expander(svc.label, expanded=False):
            entry = logs.get(svc.id, {})
            if entry.get("available"):
                content = "\n".join(entry["lines"])
                st.code(content or "(empty log)", language=None)
            else:
                st.caption(f"unavailable: {entry.get('reason', 'unknown')}")
            st.caption(f"path: `{svc.log_path}`")
