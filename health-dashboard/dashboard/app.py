"""Health Dashboard - Streamlit app."""

import sqlite3
import os
import sys

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from collectors.db import DB_PATH, init_db
from recovery.engine import get_full_recovery_report, get_connection
from recovery.advisor import get_today_summary

st.set_page_config(page_title="Health Dashboard", layout="wide")

# Compact mobile-friendly styling + dark background to prevent white flash
st.markdown("""
<meta name="theme-color" content="#0e1117">
<style>
    /* Force dark bg immediately — prevents white flash on reload */
    html { background-color: #0e1117 !important; }
    /* Top padding — enough room for nav bar */
    .block-container { padding-top: 2.5rem; padding-bottom: 0; max-width: 100%; }
    /* Prevent white flash on page reload */
    html, body, [data-testid="stAppViewContainer"], .stApp { background-color: #0e1117 !important; }
    /* Smaller subheaders */
    h1, h2, h3 { font-size: 1.1rem !important; margin-top: 0.3rem !important; margin-bottom: 0.15rem !important; }
    /* Tighter metrics */
    [data-testid="stMetric"] { padding: 0.2rem 0 !important; }
    [data-testid="stMetricValue"] { font-size: 1.3rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
    [data-testid="stMetricDelta"] { font-size: 0.7rem !important; }
    /* Tighter tabs */
    .stTabs [data-baseweb="tab-list"] { gap: 0; }
    .stTabs [data-baseweb="tab"] { padding: 0.3rem 0.6rem; font-size: 0.8rem; }
    /* Reduce chart margins */
    .stPlotlyChart { margin-top: -0.7rem; margin-bottom: -0.7rem; }
    /* Tighter columns */
    [data-testid="stHorizontalBlock"] { gap: 0.3rem; }
    /* Smaller body text */
    .stMarkdown p { font-size: 0.85rem; margin-bottom: 0.2rem; }
    /* Compact dividers */
    hr { margin: 0.3rem 0 !important; }
    /* Smaller captions */
    .stCaption { font-size: 0.65rem !important; }
    /* Tighter sidebar */
    section[data-testid="stSidebar"] { width: 14rem !important; }
    /* Compact dataframes */
    .stDataFrame { font-size: 0.75rem; }
    /* Reduce vertical gaps between elements */
    [data-testid="stVerticalBlock"] > div { gap: 0.3rem; }
    /* Protect nav bar from compact CSS */
    .nav-bar { min-height: 44px !important; display: flex !important; overflow: visible !important; }
    .nav-bar, .nav-bar * { line-height: normal !important; }
</style>
""", unsafe_allow_html=True)


from datetime import date as date_type, datetime


def freshness_badge(label: str, ts: str | None) -> str:
    """Return an HTML badge showing how fresh a data point is.

    ts can be a date string (YYYY-MM-DD) or a datetime string.
    """
    if not ts:
        return (f'<span style="display:inline-block; background:#6c757d; color:#ccc; '
                f'font-size:10px; padding:1px 6px; border-radius:3px; margin:2px 0;">'
                f'{label}: no data</span>')

    # Parse — handle both date and datetime formats
    try:
        if "T" in ts or " " in ts.strip():
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            display = dt.strftime("%b %-d %-I:%M%p").lower()
        else:
            dt = datetime.fromisoformat(ts)
            display = dt.strftime("%b %-d")
    except ValueError:
        display = ts

    # Color by age
    age_days = (datetime.now() - dt).total_seconds() / 86400
    if age_days < 1:
        color = "#28a745"  # green — fresh
    elif age_days < 2:
        color = "#fd7e14"  # orange — getting stale
    else:
        color = "#dc3545"  # red — stale

    return (f'<span style="display:inline-block; background:{color}22; border:1px solid {color}; '
            f'color:{color}; font-size:10px; padding:1px 6px; border-radius:3px; margin:2px 0;">'
            f'{label}: {display}</span>')


@st.cache_data(ttl=300)
def load_data(query: str) -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_recovery_report():
    return get_full_recovery_report()


@st.cache_data(ttl=300)
def load_today_summary():
    return get_today_summary()


# --- Sidebar ---
st.sidebar.header("Settings")
days = st.sidebar.slider("Days to show", 7, 365, 90)

# --- Swipe-based page navigation ---
PAGES = ["Today", "Recovery", "Sleep", "Heart Rate", "Activities", "Wellness"]

if "page" not in st.session_state:
    st.session_state.page = 0

# Handle swipe/nav via query params
params = st.query_params
if "p" in params:
    try:
        new_page = int(params["p"])
        if 0 <= new_page < len(PAGES):
            st.session_state.page = new_page
    except ValueError:
        pass

page_idx = st.session_state.page
page_name = PAGES[page_idx]

# Navigation — page name + horizontal dot buttons
st.markdown(f'<div style="text-align:center; font-size:14px; font-weight:600; padding-top:4px;">{page_name}</div>', unsafe_allow_html=True)

# Force the next set of columns to stay horizontal on mobile
st.markdown("""<style>
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] button[kind="secondary"]) {
    flex-wrap: nowrap !important; gap: 0 !important;
}
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] button[kind="secondary"]) [data-testid="stColumn"] {
    min-width: 0 !important; width: auto !important; flex: 1 1 0 !important;
}
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] button[kind="secondary"]) button {
    padding: 0.2rem 0 !important; font-size: 1.2rem !important; min-height: 2.2rem !important;
    min-width: 0 !important; width: 100% !important;
}
</style>""", unsafe_allow_html=True)
dot_cols = st.columns(len(PAGES))
for i, col in enumerate(dot_cols):
    with col:
        label = "●" if i == page_idx else "○"
        if st.button(label, key=f"dot_{i}", use_container_width=True, type="secondary"):
            st.session_state.page = i
            st.rerun()

# ============================================================
# TODAY PAGE
# ============================================================
if page_name == "Today":
    summary = load_today_summary()
    training = summary["training"]

    # Training recommendation banner
    color_css = {
        "green": "#28a745", "blue": "#007bff", "orange": "#fd7e14", "red": "#dc3545"
    }.get(training["color"], "#6c757d")

    st.markdown(
        f"""<div style="background-color: {color_css}; color: white; padding: 12px 16px;
        border-radius: 8px; margin-bottom: 8px;">
        <div style="font-size: 1rem; font-weight: 700; color: white; margin: 0;">{training['message']}</div>
        </div>""",
        unsafe_allow_html=True,
    )

    ts = summary.get("data_timestamps", {})

    col_sleep, col_recovery = st.columns(2)

    # --- Left column: Sleep ---
    with col_sleep:
        st.subheader("Last Night's Sleep")
        st.markdown(freshness_badge("sleep", ts.get("sleep")), unsafe_allow_html=True)
        sl = summary["sleep_last_night"]
        if sl:
            # Total + comparison
            diff_str = f"{sl['diff_from_avg']:+.1f}h vs 7-day avg"
            diff_color = "green" if sl["diff_from_avg"] >= 0 else "red"
            st.metric("Total Sleep", f"{sl['total_hours']}h", delta=diff_str)

            # Sleep stages inline bar
            total_mins = sl["deep_minutes"] + sl["rem_minutes"] + sl["light_minutes"] + sl["awake_minutes"]
            if total_mins > 0:
                deep_pct = sl["deep_minutes"] / total_mins * 100
                rem_pct = sl["rem_minutes"] / total_mins * 100
                light_pct = sl["light_minutes"] / total_mins * 100
                awake_pct = sl["awake_minutes"] / total_mins * 100

                st.markdown(
                    f"""<div style="display:flex; height:18px; border-radius:4px; overflow:hidden; margin:4px 0;">
                    <div style="width:{deep_pct}%; background:#1f77b4;" title="Deep {sl['deep_minutes']:.0f}m"></div>
                    <div style="width:{rem_pct}%; background:#ff7f0e;" title="REM {sl['rem_minutes']:.0f}m"></div>
                    <div style="width:{light_pct}%; background:#2ca02c;" title="Light {sl['light_minutes']:.0f}m"></div>
                    <div style="width:{awake_pct}%; background:#d62728;" title="Awake {sl['awake_minutes']:.0f}m"></div>
                    </div>
                    <div style="display:flex; justify-content:space-between; font-size:10px; color:#888;">
                    <span>Deep {sl['deep_minutes']:.0f}m</span>
                    <span>REM {sl['rem_minutes']:.0f}m</span>
                    <span>Light {sl['light_minutes']:.0f}m</span>
                    <span>Awake {sl['awake_minutes']:.0f}m</span>
                    </div>""",
                    unsafe_allow_html=True,
                )

            if sl.get("sleep_score"):
                st.metric("Sleep Score (Suunto)", f"{sl['sleep_score']:.0f}/100")
                st.markdown(freshness_badge("sleep score", ts.get("sleep_score")), unsafe_allow_html=True)
        else:
            st.info("No sleep data for last night.")

        # Sleep debt + recommendation
        st.divider()
        st.subheader("Tonight's Recommendation")
        debt = summary["sleep_debt_hours"]
        sleep_rec = summary["sleep_rec"]

        st.metric("Sleep Debt (7-day)", f"{debt:.1f}h")

        rec_color_css = {
            "green": "#28a745", "yellow": "#ffc107", "orange": "#fd7e14", "red": "#dc3545"
        }.get(sleep_rec["color"], "#6c757d")
        text_color = "white" if sleep_rec["color"] != "yellow" else "black"

        st.markdown(
            f"""<div style="background-color: {rec_color_css}; color: {text_color}; padding: 10px 12px;
            border-radius: 6px; margin-top: 4px; font-size: 0.85rem;">
            <strong>{sleep_rec['message']}</strong>
            </div>""",
            unsafe_allow_html=True,
        )

    # --- Right column: Recovery snapshot ---
    with col_recovery:
        st.subheader("Recovery Snapshot")
        rec = summary["recovery"]

        # TRIMP
        trimp_remaining = rec["trimp_remaining"]
        hrs_to_rec = rec["hours_to_recovered"]
        if trimp_remaining < 30:
            st.markdown(f"**Training Load:** Recovered (TRIMP {trimp_remaining:.0f})")
        else:
            days_to = hrs_to_rec / 24
            st.markdown(f"**Training Load:** {trimp_remaining:.0f} TRIMP remaining — ~{days_to:.1f} days to recovered")
        st.markdown(freshness_badge("activities", ts.get("activities")), unsafe_allow_html=True)

        # Form (TSB simplified)
        tsb_val = rec["tsb"]
        if tsb_val > 25:
            form_text = "Well rested — you may be losing fitness from inactivity"
        elif tsb_val > 10:
            form_text = "Fresh and recovered — peak performance window"
        elif tsb_val > 0:
            form_text = "Balanced — good to train normally"
        elif tsb_val > -10:
            form_text = "Mild fatigue — manageable, normal training is fine"
        elif tsb_val > -20:
            form_text = "Fatigued — building fitness, but watch recovery"
        elif tsb_val > -30:
            form_text = "Very fatigued — scale back intensity soon"
        else:
            form_text = "Overreaching — high risk of injury/illness, prioritize rest"
        st.markdown(f"**Form:** {form_text}")
        st.caption(f"TSB: {tsb_val:.1f}")

        # HRV
        hrv = rec["hrv"]
        if hrv.get("value") is not None:
            trend = {"above": "above baseline", "below": "below baseline", "normal": "at baseline"}.get(hrv.get("trend", ""), "")
            st.markdown(f"**HRV:** {hrv['value']:.0f} ms ({trend}, baseline {hrv['baseline']:.0f} ms)")
        else:
            st.markdown("**HRV:** No data")
        st.markdown(freshness_badge("hrv", ts.get("hrv")), unsafe_allow_html=True)

        # RHR
        rhr = rec["rhr"]
        if rhr.get("value") is not None:
            trend = {"lower": "below baseline (good)", "elevated": "elevated", "normal": "at baseline"}.get(rhr.get("trend", ""), "")
            st.markdown(f"**Resting HR:** {rhr['value']} bpm ({trend}, baseline {rhr['baseline']:.0f} bpm)")
        else:
            st.markdown("**Resting HR:** No data")
        st.markdown(freshness_badge("resting hr", ts.get("resting_hr")), unsafe_allow_html=True)

# ============================================================
# RECOVERY TAB
# ============================================================
if page_name == "Recovery":
    report = load_recovery_report()

    fatigue = report["fatigue"]
    tsb_data = report["tsb"]
    physio = report["physio"]

    remaining = fatigue["total_remaining_trimp"]
    hrs = fatigue["hours_to_recovered"]

    # --- Top summaries — stacked cards ---
    # Training Load
    if remaining < 30:
        trimp_short = "Recovered"
        trimp_color = "#28a745"
    elif remaining < 100:
        trimp_short = f"~{hrs/24:.1f} days to recovered"
        trimp_color = "#fd7e14"
    else:
        trimp_short = f"~{hrs/24:.1f} days to recovered — consider rest"
        trimp_color = "#dc3545"

    # Form (TSB)
    tsb_val = tsb_data["tsb"]
    if tsb_val > 25:
        form_short = "Well rested — may be losing fitness"
        form_color = "#17a2b8"
    elif tsb_val > 10:
        form_short = "Fresh — peak performance window"
        form_color = "#28a745"
    elif tsb_val > 0:
        form_short = "Balanced — good to train"
        form_color = "#28a745"
    elif tsb_val > -10:
        form_short = "Mild fatigue — manageable"
        form_color = "#fd7e14"
    elif tsb_val > -20:
        form_short = "Fatigued — watch recovery"
        form_color = "#fd7e14"
    elif tsb_val > -30:
        form_short = "Very fatigued — scale back"
        form_color = "#dc3545"
    else:
        form_short = "Overreaching — rest now"
        form_color = "#dc3545"

    # Physio
    hrv = physio["hrv"]
    rhr = physio["rhr"]
    hrv_text = f"HRV {hrv['value']:.0f}ms" if hrv.get("value") else "HRV: no data"
    rhr_text = f"RHR {rhr['value']}bpm" if rhr.get("value") else "RHR: no data"
    hrv_trend = {"above": "↑", "below": "↓", "normal": "→"}.get(hrv.get("trend", ""), "")
    rhr_trend = {"lower": "↓", "elevated": "↑", "normal": "→"}.get(rhr.get("trend", ""), "")
    physio_color = "#28a745" if hrv.get("trend") != "below" and rhr.get("trend") != "elevated" else "#fd7e14"

    st.markdown(f"""
    <div style="display:flex; flex-direction:column; gap:6px; margin-bottom:8px;">
        <div style="background:{trimp_color}22; border-left:3px solid {trimp_color}; padding:6px 10px; border-radius:4px;">
            <b style="color:{trimp_color};">Training Load:</b> {remaining:.0f} TRIMP remaining — {trimp_short}
        </div>
        <div style="background:{form_color}22; border-left:3px solid {form_color}; padding:6px 10px; border-radius:4px;">
            <b style="color:{form_color};">Form:</b> {form_short} <span style="color:#888; font-size:0.75rem;">(TSB {tsb_val:.1f})</span>
        </div>
        <div style="background:{physio_color}22; border-left:3px solid {physio_color}; padding:6px 10px; border-radius:4px;">
            <b style="color:{physio_color};">Body:</b> {hrv_text} {hrv_trend} &nbsp;|&nbsp; {rhr_text} {rhr_trend}
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- 2-column layout: Training + Form | Physiological ---
    col_left, col_right = st.columns(2)

    with col_left:
        # Training Load
        st.subheader("Training Load")
        c1, c2 = st.columns(2)
        c1.metric("Fatigue", f"{remaining:.0f} TRIMP")
        if hrs > 0:
            days_r = hrs / 24
            c2.metric("Recovery", f"{days_r:.1f}d" if days_r > 1 else f"{hrs:.0f}h")
        else:
            c2.metric("Recovery", "Done")

        # TRIMP scatter (compact)
        if fatigue["activities"]:
            act_df = pd.DataFrame(fatigue["activities"])
            fig = px.scatter(
                act_df, x="date", y="trimp", size="trimp", color="type",
                labels={"trimp": "TRIMP", "date": ""},
                size_max=20,
            )
            fig.update_layout(height=160, margin=dict(l=0, r=0, t=5, b=0), showlegend=False)
            st.plotly_chart(fig, use_container_width=True, key="chart_1")

        # Fitness-Fatigue chart
        st.subheader("Fitness-Fatigue Trend")
        tsb_history = report["tsb_history"]
        if tsb_history:
            tsb_df = pd.DataFrame(tsb_history)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=tsb_df["date"], y=tsb_df["ctl"], name="Fitness", line=dict(color="blue", width=1)))
            fig.add_trace(go.Scatter(x=tsb_df["date"], y=tsb_df["atl"], name="Fatigue", line=dict(color="red", width=1)))
            fig.add_trace(go.Scatter(x=tsb_df["date"], y=tsb_df["tsb"], name="Form", line=dict(color="green", width=2), fill="tozeroy"))
            fig.add_hline(y=0, line_dash="dash", line_color="gray")
            fig.update_layout(height=200, margin=dict(l=0, r=0, t=5, b=0), legend=dict(orientation="h", y=-0.15))
            st.plotly_chart(fig, use_container_width=True, key="chart_2")

        with st.expander("What do these numbers mean?"):
            st.markdown("""
**TSB (Training Stress Balance) = Fitness - Fatigue**
- **> 25:** Well rested — may be losing fitness from inactivity
- **10 to 25:** Fresh and recovered — peak performance window
- **0 to 10:** Balanced — good to train normally
- **-10 to 0:** Mild fatigue — manageable, keep training
- **-20 to -10:** Fatigued — building fitness, but watch recovery
- **-30 to -20:** Very fatigued — scale back intensity
- **< -30:** Overreaching — prioritize rest, injury risk

**CTL** (Chronic Training Load) = your fitness level (42-day avg)
**ATL** (Acute Training Load) = recent fatigue (7-day avg)
""")

    with col_right:
        st.subheader("Physiological")

        # HRV + RHR side by side
        h_col, r_col = st.columns(2)
        hrv = physio["hrv"]
        if hrv.get("value") is not None:
            arrow = {"above": " ↑", "below": " ↓", "normal": " →"}.get(hrv.get("trend", ""), "")
            h_col.metric("HRV", f"{hrv['value']:.0f} ms",
                         delta=f"{hrv.get('deviation', 0):+.1f}σ{arrow}" if hrv.get("deviation") else None)
            h_col.caption(f"Baseline: {hrv['baseline']:.0f} ms")
        else:
            h_col.metric("HRV", "No data")

        rhr = physio["rhr"]
        if rhr.get("value") is not None:
            arrow = {"lower": " ↓", "elevated": " ↑", "normal": " →"}.get(rhr.get("trend", ""), "")
            r_col.metric("RHR", f"{rhr['value']} bpm",
                         delta=f"{rhr.get('deviation', 0):+.1f}σ{arrow}" if rhr.get("deviation") else None,
                         delta_color="normal")
            r_col.caption(f"Baseline: {rhr['baseline']:.0f} bpm")

        # Sleep + SpO2 side by side
        sleep_p = physio["sleep"]
        s_col, o_col = st.columns(2)
        if sleep_p.get("last_night_hours") is not None:
            s_col.metric("Sleep", f"{sleep_p['last_night_hours']}h")
            s_col.caption(f"7d avg: {sleep_p['avg_7d_hours']}h")
        if sleep_p.get("sleep_score"):
            o_col.metric("Sleep Score", f"{sleep_p['sleep_score']:.0f}/100")
        elif physio.get("spo2"):
            o_col.metric("SpO2", f"{physio['spo2']['value']:.0f}%")

        # HRV trend chart
        hrv_df = load_data("SELECT date, hrv FROM wellness WHERE hrv IS NOT NULL ORDER BY date")
        if not hrv_df.empty:
            st.caption("HRV Trend")
            fig = px.line(hrv_df, x="date", y="hrv", markers=True, labels={"hrv": "ms", "date": ""})
            fig.update_layout(height=120, margin=dict(l=0, r=0, t=5, b=0), showlegend=False)
            st.plotly_chart(fig, use_container_width=True, key="chart_3")

        st.caption(f"Max HR: {report['max_hr']} bpm | Resting HR: {report['resting_hr']:.0f} bpm")

# ============================================================
# SLEEP TAB
# ============================================================
if page_name == "Sleep":
    sleep_df = load_data(f"""
        SELECT date, total_minutes, deep_minutes, rem_minutes,
               light_minutes, awake_minutes, source
        FROM sleep
        WHERE date >= date('now', '-{days} days')
        ORDER BY date
    """)

    if sleep_df.empty:
        st.info("No sleep data yet. Run the collector first.")
    else:
        sleep_df["total_hours"] = sleep_df["total_minutes"] / 60
        sleep_df["deep_hours"] = sleep_df["deep_minutes"] / 60
        sleep_df["rem_hours"] = sleep_df["rem_minutes"] / 60
        sleep_df["light_hours"] = sleep_df["light_minutes"] / 60
        sleep_df["awake_hours"] = sleep_df["awake_minutes"] / 60

        # Summary stats at top
        col1, col2, col3, col4 = st.columns(4)
        avg_7d = sleep_df.tail(7)["total_hours"].mean()
        avg_30d = sleep_df.tail(30)["total_hours"].mean()
        avg_all = sleep_df["total_hours"].mean()
        col1.metric("Last Night", f"{sleep_df.iloc[-1]['total_hours']:.1f}h" if len(sleep_df) > 0 else "N/A")
        col2.metric("7-Day Avg", f"{avg_7d:.1f}h")
        col3.metric("30-Day Avg", f"{avg_30d:.1f}h")
        col4.metric("Best Night", f"{sleep_df['total_hours'].max():.1f}h")

        # Sleep duration trend
        st.subheader("Sleep Duration")
        fig = px.bar(sleep_df, x="date", y="total_hours", color="source", barmode="group",
                     labels={"total_hours": "Hours", "date": "Date"})
        fig.add_hline(y=8, line_dash="dash", line_color="green", annotation_text="8h target")
        fig.update_layout(height=200)
        st.plotly_chart(fig, use_container_width=True, key="chart_4")

        # Sleep stages stacked area chart (Apple data only — has stage breakdown)
        apple_sleep = sleep_df[(sleep_df["source"] == "apple") & (sleep_df["deep_minutes"] > 0)].copy()
        if not apple_sleep.empty:
            st.subheader("Sleep Stages Over Time")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=apple_sleep["date"], y=apple_sleep["deep_hours"], name="Deep",
                                     stackgroup="one", fillcolor="rgba(31,119,180,0.7)"))
            fig.add_trace(go.Scatter(x=apple_sleep["date"], y=apple_sleep["rem_hours"], name="REM",
                                     stackgroup="one", fillcolor="rgba(255,127,14,0.7)"))
            fig.add_trace(go.Scatter(x=apple_sleep["date"], y=apple_sleep["light_hours"], name="Light",
                                     stackgroup="one", fillcolor="rgba(44,160,44,0.7)"))
            fig.add_trace(go.Scatter(x=apple_sleep["date"], y=apple_sleep["awake_hours"], name="Awake",
                                     stackgroup="one", fillcolor="rgba(214,39,40,0.5)"))
            fig.update_layout(height=200, yaxis_title="Hours")
            st.plotly_chart(fig, use_container_width=True, key="chart_5")

        # Sleep stage pie for selectable date
        st.subheader("Sleep Stages Breakdown")
        dates_with_stages = apple_sleep["date"].tolist() if not apple_sleep.empty else []
        if dates_with_stages:
            selected_date = st.selectbox("Select date", dates_with_stages[::-1])
            row = apple_sleep[apple_sleep["date"] == selected_date].iloc[0]
            stages = {"Deep": row["deep_minutes"], "REM": row["rem_minutes"],
                      "Light": row["light_minutes"], "Awake": row["awake_minutes"]}
            fig = go.Figure(data=[go.Pie(labels=list(stages.keys()), values=list(stages.values()),
                                         hole=0.4, marker_colors=["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"])])
            fig.update_layout(height=220, title=f"Sleep Stages — {selected_date}")
            st.plotly_chart(fig, use_container_width=True, key="chart_6")

        # Sleep score trend (from wellness)
        score_df = load_data("SELECT date, sleep_score FROM wellness WHERE sleep_score IS NOT NULL ORDER BY date")
        if not score_df.empty:
            st.subheader("Sleep Score Trend (Suunto)")
            fig = px.line(score_df, x="date", y="sleep_score", markers=True,
                          labels={"sleep_score": "Score (0-100)", "date": "Date"})
            fig.update_layout(height=200)
            st.plotly_chart(fig, use_container_width=True, key="chart_7")

        # Sleeping HR trend
        sleeping_hr_df = load_data("SELECT date, avg_sleeping_hr FROM wellness WHERE avg_sleeping_hr IS NOT NULL ORDER BY date")
        if not sleeping_hr_df.empty:
            st.subheader("Sleeping Heart Rate Trend")
            fig = px.line(sleeping_hr_df, x="date", y="avg_sleeping_hr", markers=True,
                          labels={"avg_sleeping_hr": "Avg Sleeping HR (bpm)", "date": "Date"})
            fig.update_layout(height=200)
            st.plotly_chart(fig, use_container_width=True, key="chart_8")

# ============================================================
# HEART RATE TAB
# ============================================================
if page_name == "Heart Rate":
    # Daily HR summary — much more useful than 569K raw points
    daily_hr = load_data(f"""
        SELECT substr(timestamp, 1, 10) as date,
               MIN(bpm) as min_hr,
               ROUND(AVG(bpm)) as avg_hr,
               MAX(bpm) as max_hr,
               COUNT(*) as samples
        FROM heart_rate
        WHERE timestamp >= date('now', '-{days} days')
        GROUP BY substr(timestamp, 1, 10)
        ORDER BY date
    """)

    if daily_hr.empty:
        st.info("No heart rate data yet.")
    else:
        # Summary stats
        col1, col2, col3, col4 = st.columns(4)
        latest = daily_hr.iloc[-1]
        col1.metric("Today Avg HR", f"{latest['avg_hr']:.0f} bpm")
        col2.metric("Today Min", f"{latest['min_hr']} bpm")
        col3.metric("Today Max", f"{latest['max_hr']} bpm")
        col4.metric("Today Samples", f"{latest['samples']:,.0f}")

        # Daily HR range chart
        st.subheader("Daily Heart Rate Range")
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=daily_hr["date"], y=daily_hr["max_hr"],
            name="Max", line=dict(color="rgba(214,39,40,0.3)"), showlegend=True
        ))
        fig.add_trace(go.Scatter(
            x=daily_hr["date"], y=daily_hr["min_hr"],
            name="Min", line=dict(color="rgba(44,160,44,0.3)"),
            fill="tonexty", fillcolor="rgba(100,100,200,0.1)", showlegend=True
        ))
        fig.add_trace(go.Scatter(
            x=daily_hr["date"], y=daily_hr["avg_hr"],
            name="Avg", line=dict(color="blue", width=2), showlegend=True
        ))
        fig.update_layout(height=280, yaxis_title="BPM", legend=dict(orientation="h"))
        st.plotly_chart(fig, use_container_width=True, key="chart_9")

        # Resting HR trend (smoothed)
        resting_hr = load_data(f"""
            SELECT substr(timestamp, 1, 10) as date, ROUND(AVG(bpm)) as resting_hr
            FROM heart_rate
            WHERE context = 'resting' AND timestamp >= date('now', '-{days} days')
            GROUP BY substr(timestamp, 1, 10)
            ORDER BY date
        """)
        if not resting_hr.empty and len(resting_hr) > 1:
            st.subheader("Resting Heart Rate Trend")
            resting_hr["rolling_7d"] = resting_hr["resting_hr"].rolling(7, min_periods=1).mean()
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=resting_hr["date"], y=resting_hr["resting_hr"],
                                     name="Daily", mode="markers", marker=dict(size=4, color="lightblue")))
            fig.add_trace(go.Scatter(x=resting_hr["date"], y=resting_hr["rolling_7d"],
                                     name="7-Day Avg", line=dict(color="blue", width=2)))
            fig.update_layout(height=220, yaxis_title="BPM", legend=dict(orientation="h"))
            st.plotly_chart(fig, use_container_width=True, key="chart_10")

        # HR distribution histogram
        st.subheader("Heart Rate Distribution")
        hr_sample = load_data(f"""
            SELECT bpm FROM heart_rate
            WHERE timestamp >= date('now', '-{days} days')
            AND bpm BETWEEN 35 AND 200
            ORDER BY RANDOM() LIMIT 50000
        """)
        if not hr_sample.empty:
            max_hr = 170  # from activities
            zones = [
                ("Rest (<60%)", 0, max_hr * 0.6),
                ("Fat Burn (60-70%)", max_hr * 0.6, max_hr * 0.7),
                ("Cardio (70-80%)", max_hr * 0.7, max_hr * 0.8),
                ("Hard (80-90%)", max_hr * 0.8, max_hr * 0.9),
                ("Peak (>90%)", max_hr * 0.9, 220),
            ]
            fig = px.histogram(hr_sample, x="bpm", nbins=80,
                               labels={"bpm": "Heart Rate (BPM)", "count": "Samples"})
            colors = ["green", "yellow", "orange", "red", "darkred"]
            for i, (name, lo, hi) in enumerate(zones):
                fig.add_vrect(x0=lo, x1=hi, fillcolor=colors[i], opacity=0.1,
                              annotation_text=name, annotation_position="top")
            fig.update_layout(height=200)
            st.plotly_chart(fig, use_container_width=True, key="chart_11")

# ============================================================
# ACTIVITIES TAB
# ============================================================
if page_name == "Activities":
    act_df = load_data(f"""
        SELECT date, type, duration_minutes, distance_km,
               avg_hr, max_hr, calories, source, source_id
        FROM activities
        WHERE date >= date('now', '-{days} days')
        ORDER BY date DESC
    """)

    if act_df.empty:
        st.info("No activity data yet.")
    else:
        # Summary stats
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Activities", len(act_df))
        total_hrs = act_df["duration_minutes"].sum() / 60
        col2.metric("Total Duration", f"{total_hrs:.0f}h {act_df['duration_minutes'].sum() % 60:.0f}m")
        col3.metric("Total Distance", f"{act_df['distance_km'].sum():.1f} km")
        avg_hr_val = act_df["avg_hr"].dropna().mean()
        col4.metric("Avg HR", f"{avg_hr_val:.0f} bpm" if pd.notna(avg_hr_val) else "N/A")

        # Format duration nicely
        def fmt_duration(mins):
            if pd.isna(mins) or mins == 0:
                return ""
            h, m = divmod(int(mins), 60)
            return f"{h}h {m}m" if h > 0 else f"{m}m"

        # Activity table with TRIMP
        st.subheader("Activities")
        display_df = act_df[["date", "type", "duration_minutes", "distance_km", "avg_hr", "max_hr", "calories"]].copy()
        display_df["duration"] = display_df["duration_minutes"].apply(fmt_duration)
        display_df["distance_km"] = display_df["distance_km"].round(1)
        display_df = display_df[["date", "type", "duration", "distance_km", "avg_hr", "max_hr", "calories"]]
        display_df.columns = ["Date", "Type", "Duration", "Distance (km)", "Avg HR", "Max HR", "Calories"]
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        # Per-activity HR chart (expandable)
        st.subheader("Activity Heart Rate Detail")
        activities_with_streams = act_df[act_df["source_id"].notna()].head(20)
        if not activities_with_streams.empty:
            options = [f"{r['date']} — {r['type']} ({fmt_duration(r['duration_minutes'])})"
                       for _, r in activities_with_streams.iterrows()]
            selected = st.selectbox("Select activity", options)
            if selected:
                idx = options.index(selected)
                act_id = activities_with_streams.iloc[idx]["source_id"]
                stream_df = load_data(f"""
                    SELECT timestamp_offset / 60.0 as minutes, bpm
                    FROM activity_streams
                    WHERE activity_id = '{act_id}'
                    ORDER BY timestamp_offset
                """)
                if not stream_df.empty:
                    fig = px.line(stream_df, x="minutes", y="bpm",
                                 labels={"minutes": "Minutes", "bpm": "Heart Rate (BPM)"})
                    fig.update_layout(height=220)
                    st.plotly_chart(fig, use_container_width=True, key="chart_12")
                else:
                    st.caption("No HR stream data for this activity.")

        # Weekly volume chart
        st.subheader("Weekly Volume")
        act_df["week_start"] = pd.to_datetime(act_df["date"]).dt.to_period("W").apply(lambda x: x.start_time)
        weekly = act_df.groupby("week_start").agg(
            activities=("date", "count"),
            total_hours=("duration_minutes", lambda x: x.sum() / 60),
            total_km=("distance_km", "sum"),
        ).reset_index()
        weekly["week_start"] = weekly["week_start"].dt.strftime("%Y-%m-%d")

        fig = go.Figure()
        fig.add_trace(go.Bar(x=weekly["week_start"], y=weekly["total_hours"], name="Hours", yaxis="y"))
        fig.add_trace(go.Scatter(x=weekly["week_start"], y=weekly["total_km"], name="Distance (km)",
                                  yaxis="y2", line=dict(color="red", width=2), mode="lines+markers"))
        fig.update_layout(
            height=200,
            yaxis=dict(title="Hours"),
            yaxis2=dict(title="Distance (km)", overlaying="y", side="right"),
            legend=dict(orientation="h"),
        )
        st.plotly_chart(fig, use_container_width=True, key="chart_13")

        # Activity type breakdown
        col_pie, col_stats = st.columns(2)
        with col_pie:
            st.subheader("Activity Types")
            type_counts = act_df["type"].value_counts().reset_index()
            type_counts.columns = ["Type", "Count"]
            fig = px.pie(type_counts, names="Type", values="Count", hole=0.4)
            fig.update_layout(height=220)
            st.plotly_chart(fig, use_container_width=True, key="chart_14")

        with col_stats:
            st.subheader("Stats by Type")
            type_stats = act_df.groupby("type").agg(
                count=("date", "count"),
                avg_duration=("duration_minutes", "mean"),
                avg_distance=("distance_km", "mean"),
                avg_hr=("avg_hr", "mean"),
            ).reset_index()
            type_stats["avg_duration"] = type_stats["avg_duration"].apply(fmt_duration)
            type_stats["avg_distance"] = type_stats["avg_distance"].round(1)
            type_stats["avg_hr"] = type_stats["avg_hr"].round(0)
            type_stats.columns = ["Type", "Count", "Avg Duration", "Avg Distance (km)", "Avg HR"]
            st.dataframe(type_stats, use_container_width=True, hide_index=True)

# ============================================================
# WELLNESS TAB
# ============================================================
if page_name == "Wellness":
    wellness_df = load_data("""
        SELECT date, hrv, hrv_sdnn, sleep_score, sleep_quality,
               avg_sleeping_hr, readiness, spo2, steps, source
        FROM wellness
        WHERE date IS NOT NULL
        ORDER BY date
    """)

    if wellness_df.empty:
        st.info("No wellness data yet. Wellness data comes from Suunto via Intervals.icu.")
    else:
        # Summary cards at top
        latest = wellness_df.iloc[-1]
        cols = st.columns(5)
        cols[0].metric("Steps", f"{int(latest['steps']):,}" if pd.notna(latest["steps"]) else "N/A")
        cols[1].metric("HRV", f"{latest['hrv']:.0f} ms" if pd.notna(latest["hrv"]) else "N/A")
        cols[2].metric("Sleep Score", f"{latest['sleep_score']:.0f}/100" if pd.notna(latest["sleep_score"]) else "N/A")
        cols[3].metric("SpO2", f"{latest['spo2']:.0f}%" if pd.notna(latest["spo2"]) else "N/A")
        cols[4].metric("Sleeping HR", f"{latest['avg_sleeping_hr']:.0f} bpm" if pd.notna(latest["avg_sleeping_hr"]) else "N/A")

        # Steps trend
        steps_df = wellness_df[wellness_df["steps"].notna()].copy()
        if not steps_df.empty:
            st.subheader("Daily Steps")
            steps_df["rolling_7d"] = steps_df["steps"].rolling(7, min_periods=1).mean()
            fig = go.Figure()
            fig.add_trace(go.Bar(x=steps_df["date"], y=steps_df["steps"], name="Steps", marker_color="lightblue"))
            fig.add_trace(go.Scatter(x=steps_df["date"], y=steps_df["rolling_7d"],
                                     name="7-Day Avg", line=dict(color="blue", width=2)))
            fig.update_layout(height=220, legend=dict(orientation="h"))
            st.plotly_chart(fig, use_container_width=True, key="chart_15")

        # HRV trend
        hrv_df = wellness_df[wellness_df["hrv"].notna()].copy()
        if not hrv_df.empty:
            st.subheader("HRV Trend")
            hrv_df["rolling_7d"] = hrv_df["hrv"].rolling(7, min_periods=1).mean()
            baseline = hrv_df["hrv"].mean()
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=hrv_df["date"], y=hrv_df["hrv"], name="HRV",
                                     mode="lines+markers", line=dict(color="purple")))
            fig.add_trace(go.Scatter(x=hrv_df["date"], y=hrv_df["rolling_7d"],
                                     name="7-Day Avg", line=dict(color="purple", width=2, dash="dash")))
            fig.add_hline(y=baseline, line_dash="dot", line_color="gray",
                          annotation_text=f"Baseline: {baseline:.0f} ms")
            fig.update_layout(height=220, yaxis_title="HRV (ms)", legend=dict(orientation="h"))
            st.plotly_chart(fig, use_container_width=True, key="chart_16")

        # SpO2 trend
        spo2_df = wellness_df[wellness_df["spo2"].notna()].copy()
        if not spo2_df.empty:
            st.subheader("SpO2 Trend")
            fig = px.line(spo2_df, x="date", y="spo2", markers=True,
                          labels={"spo2": "SpO2 (%)", "date": "Date"})
            fig.add_hrect(y0=95, y1=100, fillcolor="green", opacity=0.1,
                          annotation_text="Normal range", annotation_position="top left")
            fig.update_layout(height=200, yaxis=dict(range=[85, 101]))
            st.plotly_chart(fig, use_container_width=True, key="chart_17")

        # Sleep score trend
        score_df = wellness_df[wellness_df["sleep_score"].notna()].copy()
        if not score_df.empty:
            st.subheader("Sleep Score Trend (Suunto)")
            fig = px.line(score_df, x="date", y="sleep_score", markers=True,
                          labels={"sleep_score": "Score (0-100)", "date": "Date"})
            fig.update_layout(height=200)
            st.plotly_chart(fig, use_container_width=True, key="chart_18")
