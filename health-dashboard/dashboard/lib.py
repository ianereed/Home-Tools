"""Shared presentation helpers for the health dashboard.

Two concerns live here so app.py stays focused on layout:
  1. Consistent Plotly theming (one colorway, tight dark-friendly margins).
  2. The "last seen" store that powers the Overview's "since you last looked"
     comparison — a tiny JSON file in data/.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from collectors.db import DB_PATH

# --- Palette ---------------------------------------------------------------
# One source of truth for series colors so every chart looks like a set.
INK = "#e6e9ef"
MUTED = "#8b93a7"
GRID = "rgba(255,255,255,0.06)"
ACCENT = "#6ea8fe"      # primary line / steps
GOOD = "#4ade80"        # green — improving / healthy
WARN = "#fbbf24"        # amber — drifting
BAD = "#f87171"         # red — worse / stale
HRV = "#a78bfa"         # purple — HRV
SLEEP = "#60a5fa"       # blue — sleep
COLORWAY = [ACCENT, HRV, GOOD, WARN, BAD, "#f0abfc", "#5eead4"]

FONT = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"


def apply_theme(fig: go.Figure, height: int = 220, legend: bool = False) -> go.Figure:
    """Apply the house style to a Plotly figure. Returns the same figure."""
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=10, b=8),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=12, color=INK),
        colorway=COLORWAY,
        showlegend=legend,
        legend=dict(orientation="h", y=-0.2, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False, title=None)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def sparkline(values: list[float], color: str = ACCENT, height: int = 48) -> go.Figure:
    """A tiny axis-free trend line for headline tiles."""
    fig = go.Figure(go.Scatter(
        y=values, mode="lines", line=dict(color=color, width=2),
        fill="tozeroy", fillcolor=color.replace(")", ", 0.12)").replace("rgb", "rgba")
        if color.startswith("rgb") else "rgba(110,168,254,0.12)",
        hoverinfo="skip",
    ))
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False), yaxis=dict(visible=False), showlegend=False,
    )
    return fig


def trend_arrow(direction: str) -> str:
    """Unicode arrow for a direction string ('up'/'down'/'flat')."""
    return {"up": "↑", "down": "↓", "flat": "→"}.get(direction, "")


# --- "Since you last looked" store -----------------------------------------

def _state_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "dashboard_state.json")


def read_last_seen() -> date | None:
    """Return the date of the previous visit, or None if first ever."""
    try:
        with open(_state_path()) as fh:
            raw = json.load(fh).get("last_seen")
        return datetime.fromisoformat(raw).date() if raw else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def write_last_seen(when: datetime) -> None:
    """Record this visit so the next one can diff against it. Best-effort."""
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        with open(_state_path(), "w") as fh:
            json.dump({"last_seen": when.isoformat()}, fh)
    except OSError:
        pass


# --- Cardiology helpers ------------------------------------------------------

KG_TO_LB = 2.2046226218


def bp_category(systolic: float, diastolic: float) -> tuple[str, str]:
    """AHA blood-pressure category for a single reading. Worst of the two
    numbers governs (e.g. 118/85 is Stage 1, not Normal)."""
    if systolic > 180 or diastolic > 120:
        return "Hypertensive Crisis", BAD
    if systolic >= 140 or diastolic >= 90:
        return "Stage 2", BAD
    if systolic >= 130 or diastolic >= 80:
        return "Stage 1", WARN
    if systolic >= 120:
        return "Elevated", WARN
    return "Normal", GOOD


@st.cache_data(ttl=300)
def load_df(query: str, params: tuple = ()) -> pd.DataFrame:
    """Cached query against the shared health.db — mirrors app.py::load_data.

    Lives here (not app.py) so cardiology_view.py can use it without an
    app<->cardiology_view circular import.
    """
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()
