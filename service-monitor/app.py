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
st.markdown(render_dataflow(status, queues, ollama, hdb, fdb), unsafe_allow_html=True)

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
tab_services, tab_data, tab_logs, tab_help = st.tabs(["Services", "Queues & DBs", "Logs", "Help"])

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

with tab_help:
    st.markdown("""
## How to read this dashboard

### Status indicators

| Indicator | Meaning |
|---|---|
| 🟢 | Running and healthy (last exit 0) |
| 🟡 | Running but last exit was non-zero — check logs |
| 🔴 | Not currently running |
| ⚫ | External system (Gmail, YNAB, etc.) — no status to check |

### Exit codes decoded

| Exit | Meaning |
|---|---|
| `0` | Clean exit — normal for any service |
| `-9` | SIGKILL — launchd killed it (memory pressure or manual). Will auto-restart. Normal. |
| `-15` | SIGTERM — clean shutdown signal. Will auto-restart. Normal. |
| `1` or other positive | Error exit — look at the log |

---

## Service types: what's normal

There are two kinds of services, and 🔴 means different things for each.

**KeepAlive** — always-on long-running processes. launchd restarts them if they exit.
These should almost always be 🟢. A 🔴 here means launchd tried to restart it but
something is repeatedly crashing.

| Service | What it does |
|---|---|
| `event-agg / worker` | Drains the text + OCR queues, runs Ollama, writes to GCal |
| `dispatcher` | Slack Socket Mode bot — routes image uploads to event-agg or finance |
| `finance-monitor / bot` | Slack DM bot — answers finance questions via Ollama |
| `hd / receiver :8095` | Receives Apple Health data from iPhone |
| `hd / streamlit :8501` | Hosts the health dashboard web UI |
| `service-monitor :8502` | This dashboard |

**Scheduled** — runs on a timer, then stops. The PID is `—` between runs.
🔴 with exit `0` is **completely normal** — it just ran cleanly and is waiting for
the next interval. Only worry if the last exit is non-zero.

| Service | Schedule | What it does |
|---|---|---|
| `event-agg / fetch` | every 10 min | Polls Gmail/Slack/Discord, enqueues messages |
| `finance-monitor / watcher` | every 5 min | Syncs YNAB API, scans intake/ folder |
| `hd / collect` | 7:00 + 7:20 AM | Pulls Garmin/Strava data |
| `hd / intervals-poll` | every 5 min | Syncs Intervals.icu (Suunto) data |
| `hd / staleness` | 7:00 AM + 9:00 PM | Alerts if health data is stale |

---

## When to open a Claude session

### ✅ Wait — this is self-healing

- Any **scheduled** service is 🔴 with last exit `0` — it ran fine, will run again on schedule
- Worker or dispatcher shows exit `-9` or `-15` — normal signal-based restart cycle
- Ollama shows ✗ for one refresh cycle — may be loading a model (~30–60s startup)
- `text_queue` or `ocr_queue` is > 0 but small (1–5) — worker is processing, will drain

### ⚠️ Watch for one or two more refresh cycles (1–2 min)

- 🟡 on any service — running but had a non-zero exit. Usually resolves on its own.
- Ollama still ✗ after 2 minutes — check if the process crashed
- text_queue depth climbing above 10 — worker may be stuck

### 🚨 Open a Claude session

- **KeepAlive service 🔴 for 2+ refresh cycles (>1 min)** — launchd is restarting
  it but it keeps crashing. Check the error log in the Logs tab first.
- **text_queue > 20 and not shrinking** — worker stuck or Ollama down
- **Repeated tracebacks in error log** — crash loop, needs a fix
- **health.db or finance.db not updated in 24+ hours** — data pipeline broken
- **finance-monitor bot 🔴** — you can't ask it finance questions until it's back

---

## Quick SSH reference

Open Terminal and SSH into the mini to investigate:

```bash
ssh homeserver@homeserver
```

Then run any of these:

```bash
# List all home-tools services with PID + exit status
launchctl list | grep -E "home-tools|health-dashboard"

# Tail a specific service's error log
tail -50 ~/Library/Logs/home-tools/event-aggregator-worker.log
tail -50 ~/Library/Logs/home-tools-dispatcher.log
tail -50 ~/Library/Logs/home-tools-dispatcher-error.log

# Force-restart a crashed service
launchctl unload ~/Library/LaunchAgents/com.home-tools.event-aggregator.worker.plist
launchctl load   ~/Library/LaunchAgents/com.home-tools.event-aggregator.worker.plist

# Check Ollama
curl http://127.0.0.1:11434/api/tags
ollama ps  # shows what's currently loaded

# Check event-aggregator queue depths
python3 -c "import json; d=json.load(open('Home-Tools/event-aggregator/state.json')); print('text:', len(d.get('text_queue',[])), 'ocr:', len(d.get('ocr_queue',[])))"
```

---

## Data flow summary

```
External sources        Processors          State / Output
──────────────────────────────────────────────────────────
Gmail / iMsg / Slack → event-agg/fetch → state.json (queues)
                                        → event-agg/worker → Google Calendar
                                                           → Slack replies

Slack #image-intake  → dispatcher → event-agg (events)
                                  → finance-mon/intake/ (financial docs)

iPhone Health        → hd/receiver  ┐
Strava / Garmin      → hd/collect   ├─→ health.db → Streamlit :8501
Intervals.icu        → hd/intervals ┘

YNAB API + intake/   → fin/watcher → finance.db
                                   → fin/bot → Slack DM responses

Ollama :11434 ← shared by event-agg/worker, dispatcher, finance-monitor
```
""")

    st.caption("Dashboard source: `~/Home-Tools/service-monitor/` · Port 8502 · Auto-refresh 30s")
