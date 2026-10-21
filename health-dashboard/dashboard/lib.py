"""Shared presentation for every dashboard page.

  1. Palette: status colors (good / drifting / worse) and one fixed color per
     metric, so HRV is the same violet on every chart and green only ever
     means "on target".
  2. One Plotly theme, also applied to the figures borrowed from the
     standalone cardiology report.
  3. One card component (value tiles everywhere: Overview, Goals levers,
     page summaries, Nutrition), one page header, one time-range picker.
  4. The "last seen" store behind the Overview's "since you last looked".
"""
from __future__ import annotations

import html
import json
import os
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.targets import KG_TO_LB  # noqa: F401  (re-exported for older imports)

# --- Palette -------------------------------------------------------------------
INK = "#e6e9ef"
MUTED = "#8b93a7"
GRID = "rgba(255,255,255,0.06)"
BG = "#0e1117"
CARD_BG = "#161b26"
CARD_BORDER = "#2a2f3a"
ACCENT = "#6ea8fe"      # UI accent (active tab, links)

# Status — used for dots, deltas, bands and goal lines only, never as a series color.
GOOD = "#4ade80"
WARN = "#fbbf24"
BAD = "#f87171"

# One color per metric, on every page.
SERIES = {
    "sleep": "#60a5fa",
    "hrv": "#a78bfa",
    "rhr": "#f472b6",
    "spo2": "#22d3ee",
    "steps": "#2dd4bf",
    "volume": "#6ea8fe",     # training hours / fitness (CTL)
    "fatigue": "#fb923c",    # ATL
    "form": "#cbd5e1",       # TSB
    "sys": "#38bdf8",
    "dia": "#e879f9",
    "weight": "#d4a373",
    "bodyfat": "#fda4af",
    "lean": "#93c5fd",
    "ldl": "#fb7185",
    "apob": "#f59e0b",
    "statin": "#38bdf8",
    "alcohol": "#c084fc",
    "sodium": "#94a3b8",
    "kcal": "#6ea8fe",
}
SLEEP_STAGES = {"Deep": "#1d4ed8", "REM": "#3b82f6", "Light": "#93c5fd", "Awake": "#64748b"}
ZONE_COLORS = {"Z1": "#475569", "Z2": "#60a5fa", "Z3": "#facc15", "Z4": "#fb923c", "Z5": "#ef4444"}

FONT = "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha:.2f})"


# --- Charts --------------------------------------------------------------------

def apply_theme(fig: go.Figure, height: int = 220, legend: bool = False) -> go.Figure:
    """Apply the house style to a Plotly figure. Returns the same figure."""
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=10, b=8),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=12, color=INK),
        showlegend=legend,
        legend=dict(orientation="h", y=-0.2, font=dict(size=10), bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False, title=None)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


# Trace-name prefixes in the cardiology report's figures -> house series colors.
_REPORT_TRACE_COLORS = [
    ("LDL", "ldl"), ("ApoB", "apob"), ("Rosuvastatin", "statin"), ("Statin", "statin"),
    ("Mod", "volume"), ("Vigorous", "fatigue"), ("Resting", "rhr"),
]


def restyle_report_fig(fig: go.Figure, height: int) -> go.Figure:
    """Bring a figure built by cardiology/build_report.py (standalone-report
    styling: in-plot title, its own background and colors) into the house
    theme. The page renders the title as a heading instead."""
    for tr in fig.data:
        name = tr.name or ""
        key = next((k for p, k in _REPORT_TRACE_COLORS if name.startswith(p)), None)
        if key and getattr(tr, "line", None) is not None:
            tr.line.color = SERIES[key]
        if key and getattr(tr, "marker", None) is not None and tr.type == "bar":
            tr.marker.color = SERIES[key]
    fig.layout.title = None          # reset entirely: an empty title renders "undefined"
    apply_theme(fig, height, legend=True)
    has_y2 = any(getattr(fig.layout[k], "overlaying", None)
                 for k in fig.layout if k.startswith("yaxis"))
    # top room for the report's event labels drawn above the plot area
    fig.update_layout(margin=dict(l=8, r=48 if has_y2 else 8, t=36, b=8))
    return fig


def line_with_avg(df: pd.DataFrame, col: str, color: str, name: str, height: int = 220,
                  window: int = 7, bars: bool = False, ref: tuple | None = None) -> go.Figure:
    """The standard daily-series chart: daily points (or bars) + a 7-day average line."""
    fig = go.Figure()
    if bars:
        fig.add_trace(go.Bar(x=df["date"], y=df[col], name=name, marker_color=color, opacity=0.45))
    else:
        fig.add_trace(go.Scatter(x=df["date"], y=df[col], name=name, mode="markers",
                                 marker=dict(size=4, color=color)))
    from dashboard.metrics import rolling_avg
    fig.add_trace(go.Scatter(x=df["date"], y=rolling_avg(df, col, window), connectgaps=False,
                             name=f"{window}-day avg", line=dict(color=color, width=2)))
    if ref:
        y, label = ref
        fig.add_hline(y=y, line_dash="dot", line_color=MUTED, annotation_text=label,
                      annotation_font_color=MUTED, annotation_font_size=10)
    return apply_theme(fig, height, legend=True)


def chart(fig: go.Figure, key: str):
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


# --- Cards ---------------------------------------------------------------------

CSS = f"""
<style>
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:8px 0 12px;}}
.cards.c3{{grid-template-columns:repeat(3,1fr);}}
@media (max-width:760px){{.cards,.cards.c3{{grid-template-columns:repeat(2,1fr);}}}}
.card{{background:{CARD_BG};border:1px solid {CARD_BORDER};border-radius:8px;padding:10px 12px 8px;min-width:0;}}
.card .cardlabel{{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:{MUTED};
  display:flex;justify-content:space-between;align-items:center;gap:6px;}}
.card .cardval{{font-size:20px;font-weight:650;margin:2px 0 0;color:{INK};font-variant-numeric:tabular-nums;}}
.card .cardval small{{font-size:12px;color:{MUTED};font-weight:500;}}
.card .cardval.hi{{color:{BAD};}}
.card .cardsub{{font-size:11px;color:{MUTED};margin:1px 0 2px;line-height:1.45;}}
.card .cardsub b{{color:{INK};}}
.card .delta{{font-size:11px;font-weight:600;}}
.card .delta.up{{color:{BAD};}} .card .delta.dn{{color:{GOOD};}}
.card .dot{{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none;}}
.card .spark{{display:block;margin-top:4px;width:100%;height:26px;}}
.hd-title{{font-size:1.5rem;font-weight:700;margin:0.2rem 0 0;letter-spacing:-0.01em;color:{INK};}}
.hd-purpose{{color:{MUTED};font-size:13px;margin:0 0 6px;}}
</style>
"""


def spark_svg(values, color: str, height: int = 26) -> str:
    """Tiny inline-SVG trend line for a card; '' with fewer than 2 points."""
    vals = [float(v) for v in (values or []) if v is not None and not pd.isna(v)]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    width = 140
    step = width / (len(vals) - 1)
    pts = " ".join(f"{i * step:.1f},{height - 3 - (v - lo) / span * (height - 6):.1f}"
                   for i, v in enumerate(vals))
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>')


def card(label: str, value: str, sub: str = "", status: str | None = None,
         spark: str = "", delta: tuple[str, str] | None = None) -> str:
    """One value tile. `value` and `sub` may hold trusted inline HTML (<small>,
    <b>); `label` is escaped. `status` is a status color for the corner dot.
    `delta` is (text, color) shown under the value."""
    dot = f'<span class="dot" style="background:{status}"></span>' if status else ""
    d = (f'<div class="delta" style="color:{delta[1]}">{delta[0]}</div>' if delta else "")
    sub_html = f'<div class="cardsub">{sub}</div>' if sub else ""
    return (f'<div class="card"><div class="cardlabel"><span>{html.escape(label)}</span>{dot}</div>'
            f'<div class="cardval">{value}</div>{d}{sub_html}{spark}</div>')


def cards(items: list[str], cols3: bool = False):
    """Render a responsive grid of card() tiles."""
    cls = "cards c3" if cols3 else "cards"
    st.markdown(f'<div class="{cls}">{"".join(items)}</div>', unsafe_allow_html=True)


def weight_value(w: dict) -> str:
    """Card value for weight: the 7-day mean and the 7-day lowest reading, side by side."""
    return (f'{w["lb"]:.1f} <small>lb mean</small> &nbsp;·&nbsp; '
            f'{w["low_lb"]:.1f} <small>low</small>')


def weight_basis(w: dict) -> str:
    """Card sub-line: what the two weight numbers cover."""
    if w["stale"]:
        return f'last weigh-in {w["age_days"]} d ago'
    return (f'last 7 days ({w["n"]} weigh-in days) · low on {w["low_date"]:%b %-d} · '
            f'latest {w["latest_lb"]:.1f}, {w["age_days"]} d ago')


def fmt(v, pattern: str = "{:.0f}", dash: str = "—") -> str:
    return dash if v is None or pd.isna(v) else pattern.format(v)


# --- Page chrome ---------------------------------------------------------------

RANGES = {"30d": 30, "90d": 90, "6mo": 180, "1y": 365, "All": 100000}
RANGE_LABELS = {"30d": "last 30 days", "90d": "last 90 days", "6mo": "last 6 months",
                "1y": "last year", "All": "all history"}


def page_header(title: str, purpose: str, with_range: bool = False) -> int | None:
    """Title + one-line purpose on every page; pages that follow the time range
    get the picker on the right. Returns the selected range in days."""
    if not with_range:
        st.markdown(f'<div class="hd-title">{html.escape(title)}</div>'
                    f'<div class="hd-purpose">{purpose}</div>', unsafe_allow_html=True)
        return None
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    left.markdown(f'<div class="hd-title">{html.escape(title)}</div>'
                  f'<div class="hd-purpose">{purpose}</div>', unsafe_allow_html=True)
    if st.session_state.get("range") not in RANGES:
        st.session_state.range = "90d"
    with right:
        st.segmented_control("Time range", list(RANGES), key="range",
                             label_visibility="collapsed", on_change=_keep_range)
    return RANGES[st.session_state.range or "90d"]


def _keep_range():
    # segmented_control can be clicked off; a range is always selected
    if not st.session_state.get("range"):
        st.session_state.range = st.session_state.get("_last_range", "90d")
    st.session_state._last_range = st.session_state.range


def range_label() -> str:
    return RANGE_LABELS.get(st.session_state.get("range") or "90d", "")


# --- "Since you last looked" store -------------------------------------------

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
