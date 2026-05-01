"""Plan tab — placeholder for Phase 13 (meal-planner expansion).

Renders an honest "coming soon" panel so Anny + Ian know the slot is reserved.
TC5 (UC5 in plan terminology): Plan tab is intentionally not folded into Ask.
"""
from __future__ import annotations

import streamlit as st


def render() -> None:
    st.info(
        "**Coming in Phase 13** — meal-planner expansion (joint priority).\n\n"
        "This slot is reserved for the meal-planner UI: weekly planning at the "
        "Windows laptop, Apple Shortcut entry points from the iPhone, and a "
        "joint surface for Anny + Ian. See `Mac-mini/PLAN.md` Phase 13."
    )
    st.caption(
        "Why a placeholder instead of just hiding the tab? "
        "Reserving the slot now means the tab order locks in before Phase 13 "
        "lands — no painful UX shuffle later."
    )
