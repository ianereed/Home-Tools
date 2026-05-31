"""
Mini Ops — Streamlit console at :8503.

Ian's ops surface for the Home-Tools mini. NOT a joint surface for Anny;
that's the meal-planner expansion (Phase 14+).

Tab order (left → right, default):
  Jobs      — queue depth, recent runs, kinds list
  Decisions — cards.jsonl feed (approve/reject/dismiss)
  Ask       — free-form prompt to local LLM
  Intake    — paste/upload files for nas-intake-style processing
  Recipes   — meal-planner V0 (Phase 14)

Deep-link: ?tab=<key> opens directly on the named tab (no Jobs flicker).
Sidebar: Settings (status panel, not editable in v1) — TC2.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make repo importable when streamlit launches us as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from console.sidebar import settings  # noqa: E402
from console.tabs import ask, capture, decisions, intake, jobs, plan  # noqa: E402

st.set_page_config(
    page_title="Mini Ops",
    page_icon=":gear:",
    layout="wide",
    initial_sidebar_state="expanded",
)

_TAB_ORDER = ["jobs", "capture", "decisions", "ask", "intake", "recipes"]
_TAB_LABELS = {
    "jobs":      ":racing_car: Jobs",
    "capture":   ":camera: Capture",
    "decisions": ":card_index: Decisions",
    "ask":       ":speech_balloon: Ask",
    "intake":    ":inbox_tray: Intake",
    "recipes":   ":memo: Recipes",
}
_TAB_RENDERERS = {
    "jobs":      jobs.render,
    "capture":   capture.render,
    "decisions": decisions.render,
    "ask":       ask.render,
    "intake":    intake.render,
    "recipes":   plan.render,  # module is still console.tabs.plan
}

# Deep-link: rotate so the requested tab is index 0 (Streamlit opens tab 0 visually).
# st.tabs does not expose which tab is selected as a Python value; tab-switching
# is client-side only. Rotating on load satisfies the "no Jobs flicker" exit gate.
# The URL is for deep-linking only — no write-back inside tab blocks (that would
# execute for all tabs simultaneously and clobber with the last key).
requested = st.query_params.get("tab", "jobs")
if requested not in _TAB_ORDER:
    requested = "jobs"
pivot = _TAB_ORDER.index(requested)
ordered = _TAB_ORDER[pivot:] + _TAB_ORDER[:pivot]

st.markdown(
    "<h2 style='margin: 0; padding: 0'>Mini Ops</h2>"
    "<p style='color: #888; margin-top: 0'>Home-Tools mini · Ian's ops surface</p>",
    unsafe_allow_html=True,
)

# Sidebar — settings status panel (read-only in v1).
with st.sidebar:
    settings.render()

# Tabs — active tab is always index 0 (rotated per ?tab= query param).
tabs = st.tabs([_TAB_LABELS[k] for k in ordered])
for key, tab in zip(ordered, tabs):
    with tab:
        _TAB_RENDERERS[key]()
