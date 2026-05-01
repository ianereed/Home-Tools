"""
Mini Ops — Streamlit console at :8503.

Ian's ops surface for the Home-Tools mini. NOT a joint surface for Anny;
that's Phase 13's meal-planner expansion (see Plan tab placeholder).

Tab order (left → right):
  Jobs      — queue depth, recent runs, kinds list
  Decisions — cards.jsonl feed (approve/reject/dismiss)
  Ask       — free-form prompt to local LLM (Phase 12 just wires it; the
              real model selection belongs in a future commit)
  Intake    — paste/upload files for nas-intake-style processing
  Plan      — placeholder for Phase 13 meal-planner

Sidebar: Settings (status panel, not editable in v1) — TC2.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make repo importable when streamlit launches us as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from console.sidebar import settings  # noqa: E402
from console.tabs import ask, decisions, intake, jobs, plan_placeholder  # noqa: E402

st.set_page_config(
    page_title="Mini Ops",
    page_icon=":gear:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    "<h2 style='margin: 0; padding: 0'>Mini Ops</h2>"
    "<p style='color: #888; margin-top: 0'>Home-Tools mini · Ian's ops surface</p>",
    unsafe_allow_html=True,
)

# Sidebar — settings status panel (read-only in v1).
with st.sidebar:
    settings.render()

# Tabs — Jobs lands here (TC3).
tab_jobs, tab_decisions, tab_ask, tab_intake, tab_plan = st.tabs(
    [":racing_car: Jobs", ":card_index: Decisions", ":speech_balloon: Ask", ":inbox_tray: Intake", ":memo: Plan"]
)

with tab_jobs:
    jobs.render()
with tab_decisions:
    decisions.render()
with tab_ask:
    ask.render()
with tab_intake:
    intake.render()
with tab_plan:
    plan_placeholder.render()
