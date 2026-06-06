"""Health Dashboard — information-first historical view.

Garmin owns day-to-day training guidance; this dashboard is for holistic,
historical, information-only tracking. The home screen ("Overview") is built for
periodic check-ins ("what changed since I last looked?") rather than a daily
glance, and every data page favours long-range trends over today's number.
"""

import os
import sqlite3
import sys
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from collectors.db import DB_PATH                                  # noqa: E402
from recovery.engine import get_full_recovery_report              # noqa: E402
from recovery.advisor import get_overview                         # noqa: E402
from dashboard import lib                                         # noqa: E402

st.set_page_config(page_title="Health", layout="wide", page_icon="❤️")

st.markdown("""
<meta name="theme-color" content="#0e1117">
<style>
    html, body, [data-testid="stAppViewContainer"], .stApp { background-color: #0e1117 !important; }
    /* Drop the default white toolbar/menu so the dark theme is seamless */
    header[data-testid="stHeader"] { background: transparent !important; height: 0 !important; }
    [data-testid="stToolbar"], #MainMenu, footer { display: none !important; }
    .block-container { padding-top: 1.4rem; padding-bottom: 1rem; max-width: 1100px; }
    h1, h2, h3 { font-weight: 650 !important; letter-spacing: -0.01em; }
    h2 { font-size: 1.05rem !important; margin: 1.1rem 0 0.2rem !important; color: #cdd3e0; }
    h3 { font-size: 0.9rem !important; margin: 0.6rem 0 0.1rem !important; color: #aab2c5; }
    [data-testid="stMetricValue"] { font-size: 1.45rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.72rem !important; color: #8b93a7 !important; }
    [data-testid="stMetricDelta"] { font-size: 0.72rem !important; }
    .stPlotlyChart { margin: -0.3rem 0; }
    [data-testid="stHorizontalBlock"] { gap: 0.6rem; }
    hr { margin: 0.5rem 0 !important; border-color: rgba(255,255,255,0.06) !important; }
    .stCaption, .stMarkdown small { color: #8b93a7 !important; }
    section[data-testid="stSidebar"] { width: 13rem !important; }
    /* nav buttons → tab strip */
    div[data-testid="stHorizontalBlock"]:has(button[kind="secondary"]) { gap: 0.2rem; flex-wrap: nowrap; }
    button[kind="secondary"] { border: none !important; background: transparent !important;
        color: #8b93a7 !important; font-size: 0.8rem !important; padding: 0.3rem 0.1rem !important; }
    button[kind="primary"] { border: none !important; background: transparent !important;
        color: #6ea8fe !important; border-bottom: 2px solid #6ea8fe !important;
        border-radius: 0 !important; font-size: 0.8rem !important; padding: 0.3rem 0.1rem !important; }
</style>
""", unsafe_allow_html=True)


# --- data access -----------------------------------------------------------

@st.cache_data(ttl=300)
def load_data(query: str, params: tuple = ()) -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


@st.cache_data(ttl=300)
def load_recovery_report():
    return get_full_recovery_report()


@st.cache_data(ttl=300)
def load_overview(since_iso: str | None):
    from datetime import date
    since = date.fromisoformat(since_iso) if since_iso else None
    return get_overview(since)


def freshness_badge(label: str, ts: str | None) -> str:
    if not ts:
        return (f'<span style="display:inline-block;background:#33384a;color:#8b93a7;font-size:10px;'
                f'padding:2px 7px;border-radius:10px;margin:2px 4px 2px 0;">{label}: no data</span>')
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace(" ", "T")) if (
            "T" in ts or " " in ts.strip()) else datetime.fromisoformat(ts)
        display = dt.strftime("%b %-d")
    except ValueError:
        dt, display = datetime.now(), ts
    age = (datetime.now() - dt.replace(tzinfo=None)).total_seconds() / 86400
    color = lib.GOOD if age < 1.5 else lib.WARN if age < 2.5 else lib.BAD
    return (f'<span style="display:inline-block;background:{color}22;border:1px solid {color}55;'
            f'color:{color};font-size:10px;padding:2px 7px;border-radius:10px;margin:2px 4px 2px 0;">'
            f'{label}: {display}</span>')


# --- since-you-last-looked: capture previous visit once per session --------

if "since_iso" not in st.session_state:
    prev = lib.read_last_seen()
    st.session_state.since_iso = prev.isoformat() if prev else None
    lib.write_last_seen(datetime.now())


# --- navigation ------------------------------------------------------------

PAGES = ["Overview", "Sleep", "Heart & HRV", "Fitness", "Activity", "Wellness"]
if "page" not in st.session_state:
    st.session_state.page = 0
if "p" in st.query_params:
    try:
        i = int(st.query_params["p"])
        if 0 <= i < len(PAGES):
            st.session_state.page = i
    except ValueError:
        pass

nav_cols = st.columns(len(PAGES))
for i, col in enumerate(nav_cols):
    with col:
        if st.button(PAGES[i], key=f"nav_{i}", use_container_width=True,
                     type="primary" if i == st.session_state.page else "secondary"):
            st.session_state.page = i
            st.query_params["p"] = str(i)
            st.rerun()
page = PAGES[st.session_state.page]

RANGES = {"30 days": 30, "90 days": 90, "6 months": 180, "1 year": 365, "All": 100000}
st.sidebar.header("View")
range_label = st.sidebar.selectbox("Time range", list(RANGES), index=1)
days = RANGES[range_label]
st.sidebar.caption("Data flows in from Garmin, Strava and Apple Health. "
                   "Training guidance lives on your Garmin — this is the long view.")


# ===========================================================================
# OVERVIEW
# ===========================================================================
def render_overview():
    ov = load_overview(st.session_state.since_iso)
    hl = ov["headline"]

    # "Since you last looked"
    if ov["is_first_visit"]:
        st.info("👋 First visit on this device — showing trends vs. 30 days ago. "
                "Next time you'll see what changed since today.")
    else:
        st.markdown(f"#### Since you last looked · {ov['compare_from']}")
        if not ov["since_lines"]:
            st.caption("Nothing notable moved. Steady as she goes.")
        else:
            for line in ov["since_lines"]:
                dot = lib.GOOD if line["good"] else lib.BAD if line["good"] is False else lib.MUTED
                st.markdown(
                    f'<div style="margin:1px 0;font-size:0.9rem;">'
                    f'<span style="color:{dot};">●</span> '
                    f'<b>{line["label"]}</b> — {line["detail"]}</div>',
                    unsafe_allow_html=True)

    # Headline tiles
    st.markdown("## At a glance")
    tile_meta = [("hrv", lib.HRV), ("rhr", lib.ACCENT), ("sleep", lib.SLEEP),
                 ("fitness", lib.GOOD), ("steps", lib.ACCENT)]
    cols = st.columns(len(tile_meta))
    for (key, color), col in zip(tile_meta, cols):
        t = hl[key]
        with col:
            val = t["latest"]
            value_str = t["fmt"].format(val) if val is not None else "—"
            unit = f" {t['unit']}" if t["unit"] and t["unit"] not in ("CTL", "") else ""
            ch = t["change"]
            delta = (f"{ch['pct']*100:+.0f}%" if ch and ch["direction"] != "flat" else None)
            st.metric(t["label"], f"{value_str}{unit}", delta=delta,
                      delta_color="normal" if t["higher_is_better"] else "inverse")
            if t["series"]:
                st.plotly_chart(lib.sparkline(t["series"], color), use_container_width=True,
                                config={"displayModeBar": False}, key=f"spark_{key}")

    # Freshness
    st.markdown("## Data freshness")
    fr = ov["freshness"]
    badges = "".join(freshness_badge(lbl, fr.get(k)) for lbl, k in [
        ("Sleep", "sleep"), ("Resting HR", "resting_hr"), ("HRV", "hrv"),
        ("Activities", "activities"), ("Sleep score", "sleep_score"), ("Steps", "steps")])
    st.markdown(badges, unsafe_allow_html=True)

    # Highlights
    if ov["highlights"]:
        st.markdown("## Highlights")
        hcols = st.columns(len(ov["highlights"]))
        for h, col in zip(ov["highlights"], hcols):
            col.metric(h["label"], h["value"], help=h.get("sub") or None)


# ===========================================================================
# SLEEP
# ===========================================================================
def render_sleep():
    df = load_data(
        "SELECT date, MAX(total_minutes) total_minutes, MAX(deep_minutes) deep_minutes, "
        "MAX(rem_minutes) rem_minutes, MAX(light_minutes) light_minutes, "
        "MAX(awake_minutes) awake_minutes FROM sleep WHERE date >= date('now', ?) "
        "GROUP BY date ORDER BY date", (f"-{days} days",))
    if df.empty:
        st.info("No sleep data in range.")
        return
    df["hours"] = df["total_minutes"] / 60
    df["roll"] = df["hours"].rolling(7, min_periods=1).mean()

    c = st.columns(4)
    c[0].metric("Last night", f"{df.iloc[-1]['hours']:.1f}h")
    c[1].metric("7-day avg", f"{df.tail(7)['hours'].mean():.1f}h")
    c[2].metric("30-day avg", f"{df.tail(30)['hours'].mean():.1f}h")
    c[3].metric("Best in range", f"{df['hours'].max():.1f}h")

    st.markdown("## Sleep duration")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["date"], y=df["hours"], name="Hours", marker_color=lib.SLEEP, opacity=0.6))
    fig.add_trace(go.Scatter(x=df["date"], y=df["roll"], name="7-day avg",
                             line=dict(color=lib.INK, width=2)))
    fig.add_hline(y=8, line_dash="dot", line_color=lib.GOOD, annotation_text="8h")
    st.plotly_chart(lib.apply_theme(fig, 240, legend=True), use_container_width=True, key="sl_dur")

    cc = st.columns(2)
    with cc[0]:
        st.markdown("### Monthly average")
        m = df.copy()
        m["month"] = pd.to_datetime(m["date"]).dt.strftime("%Y-%m")
        mm = m.groupby("month")["hours"].mean().reset_index()
        fig = px.bar(mm, x="month", y="hours")
        fig.update_traces(marker_color=lib.SLEEP)
        st.plotly_chart(lib.apply_theme(fig, 180), use_container_width=True, key="sl_mon")
    with cc[1]:
        st.markdown("### By weekday")
        w = df.copy()
        w["wd"] = pd.to_datetime(w["date"]).dt.dayofweek
        order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        ww = w.groupby("wd")["hours"].mean().reindex(range(7)).reset_index()
        ww["name"] = order
        fig = px.bar(ww, x="name", y="hours")
        fig.update_traces(marker_color=lib.HRV)
        st.plotly_chart(lib.apply_theme(fig, 180), use_container_width=True, key="sl_wd")

    stages = df[df["deep_minutes"] > 0].copy()
    if not stages.empty:
        st.markdown("## Sleep stages over time")
        fig = go.Figure()
        for col_, name, color in [("deep_minutes", "Deep", lib.SLEEP), ("rem_minutes", "REM", lib.HRV),
                                  ("light_minutes", "Light", lib.GOOD), ("awake_minutes", "Awake", lib.BAD)]:
            fig.add_trace(go.Scatter(x=stages["date"], y=stages[col_] / 60, name=name,
                                     stackgroup="one", line=dict(width=0, color=color)))
        st.plotly_chart(lib.apply_theme(fig, 220, legend=True), use_container_width=True, key="sl_stg")

    score = load_data("SELECT date, sleep_score FROM wellness WHERE sleep_score IS NOT NULL "
                      "AND date >= date('now', ?) ORDER BY date", (f"-{days} days",))
    shr = load_data("SELECT date, avg_sleeping_hr FROM wellness WHERE avg_sleeping_hr IS NOT NULL "
                    "AND date >= date('now', ?) ORDER BY date", (f"-{days} days",))
    sc = st.columns(2)
    if not score.empty:
        with sc[0]:
            st.markdown("### Sleep score (Garmin)")
            fig = px.line(score, x="date", y="sleep_score", markers=True)
            fig.update_traces(line_color=lib.GOOD)
            st.plotly_chart(lib.apply_theme(fig, 180), use_container_width=True, key="sl_score")
    if not shr.empty:
        with sc[1]:
            st.markdown("### Sleeping heart rate")
            fig = px.line(shr, x="date", y="avg_sleeping_hr", markers=True)
            fig.update_traces(line_color=lib.ACCENT)
            st.plotly_chart(lib.apply_theme(fig, 180), use_container_width=True, key="sl_shr")

    if not stages.empty:
        with st.expander("Single-night stage breakdown"):
            d = st.selectbox("Date", stages["date"].tolist()[::-1], key="sl_pick")
            r = stages[stages["date"] == d].iloc[0]
            fig = go.Figure(go.Pie(
                labels=["Deep", "REM", "Light", "Awake"],
                values=[r["deep_minutes"], r["rem_minutes"], r["light_minutes"], r["awake_minutes"]],
                hole=0.5, marker_colors=[lib.SLEEP, lib.HRV, lib.GOOD, lib.BAD]))
            st.plotly_chart(lib.apply_theme(fig, 240), use_container_width=True, key="sl_pie")


# ===========================================================================
# HEART & HRV
# ===========================================================================
def render_heart():
    hrv = load_data("SELECT date, hrv FROM wellness WHERE hrv IS NOT NULL "
                    "AND date >= date('now', ?) ORDER BY date", (f"-{days} days",))
    st.markdown("## HRV")
    if hrv.empty:
        st.caption("No HRV data in range.")
    else:
        hrv["roll"] = hrv["hrv"].rolling(7, min_periods=1).mean()
        baseline = hrv["hrv"].mean()
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=hrv["date"], y=hrv["hrv"], name="HRV", mode="markers",
                                 marker=dict(size=4, color=lib.HRV)))
        fig.add_trace(go.Scatter(x=hrv["date"], y=hrv["roll"], name="7-day avg",
                                 line=dict(color=lib.HRV, width=2)))
        fig.add_hline(y=baseline, line_dash="dot", line_color=lib.MUTED,
                      annotation_text=f"avg {baseline:.0f} ms")
        st.plotly_chart(lib.apply_theme(fig, 230, legend=True), use_container_width=True, key="hr_hrv")

    rhr = load_data("SELECT substr(timestamp,1,10) date, ROUND(AVG(bpm)) rhr FROM heart_rate "
                    "WHERE context='resting' AND timestamp >= date('now', ?) "
                    "GROUP BY date ORDER BY date", (f"-{days} days",))
    if not rhr.empty and len(rhr) > 1:
        st.markdown("## Resting heart rate")
        rhr["roll"] = rhr["rhr"].rolling(7, min_periods=1).mean()
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=rhr["date"], y=rhr["rhr"], name="Daily", mode="markers",
                                 marker=dict(size=4, color=lib.ACCENT)))
        fig.add_trace(go.Scatter(x=rhr["date"], y=rhr["roll"], name="7-day avg",
                                 line=dict(color=lib.ACCENT, width=2)))
        st.plotly_chart(lib.apply_theme(fig, 220, legend=True), use_container_width=True, key="hr_rhr")

    daily = load_data("SELECT substr(timestamp,1,10) date, MIN(bpm) lo, ROUND(AVG(bpm)) avg, "
                      "MAX(bpm) hi FROM heart_rate WHERE timestamp >= date('now', ?) "
                      "GROUP BY date ORDER BY date", (f"-{days} days",))
    if not daily.empty:
        st.markdown("## Daily heart-rate range")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=daily["date"], y=daily["hi"], name="Max",
                                 line=dict(color="rgba(248,113,113,0.35)")))
        fig.add_trace(go.Scatter(x=daily["date"], y=daily["lo"], name="Min", fill="tonexty",
                                 fillcolor="rgba(110,168,254,0.10)", line=dict(color="rgba(96,165,250,0.35)")))
        fig.add_trace(go.Scatter(x=daily["date"], y=daily["avg"], name="Avg",
                                 line=dict(color=lib.ACCENT, width=2)))
        st.plotly_chart(lib.apply_theme(fig, 240, legend=True), use_container_width=True, key="hr_range")

    sample = load_data("SELECT bpm FROM heart_rate WHERE timestamp >= date('now', ?) "
                       "AND bpm BETWEEN 35 AND 200 ORDER BY RANDOM() LIMIT 50000", (f"-{days} days",))
    if not sample.empty:
        st.markdown("## Heart-rate distribution")
        mx = 170
        fig = px.histogram(sample, x="bpm", nbins=80)
        fig.update_traces(marker_color=lib.ACCENT)
        for name, lo, hi, c in [("Rest", 0, mx*.6, lib.GOOD), ("Fat burn", mx*.6, mx*.7, "#a3e635"),
                                ("Cardio", mx*.7, mx*.8, lib.WARN), ("Hard", mx*.8, mx*.9, "#fb923c"),
                                ("Peak", mx*.9, 220, lib.BAD)]:
            fig.add_vrect(x0=lo, x1=hi, fillcolor=c, opacity=0.07,
                          annotation_text=name, annotation_position="top", line_width=0)
        st.plotly_chart(lib.apply_theme(fig, 200), use_container_width=True, key="hr_hist")


# ===========================================================================
# FITNESS (information-only — no train/rest prescription)
# ===========================================================================
def render_fitness():
    report = load_recovery_report()
    tsb = report["tsb"]
    st.markdown("## Fitness & form")
    c = st.columns(3)
    c[0].metric("Fitness (CTL)", f"{tsb['ctl']:.0f}", help="Chronic training load — 42-day average")
    c[1].metric("Fatigue (ATL)", f"{tsb['atl']:.0f}", help="Acute training load — 7-day average")
    c[2].metric("Form (TSB)", f"{tsb['tsb']:.0f}", help="Fitness minus fatigue")
    st.caption("Informational only. Fitness rises with sustained training; form dips when "
               "you're loading up and rises as you taper. Day-to-day guidance lives on your Garmin.")

    hist = report["tsb_history"]
    if hist:
        df = pd.DataFrame(hist)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["date"], y=df["ctl"], name="Fitness", line=dict(color=lib.ACCENT, width=2)))
        fig.add_trace(go.Scatter(x=df["date"], y=df["atl"], name="Fatigue", line=dict(color=lib.BAD, width=1)))
        fig.add_trace(go.Scatter(x=df["date"], y=df["tsb"], name="Form", line=dict(color=lib.GOOD, width=1.5),
                                 fill="tozeroy", fillcolor="rgba(74,222,128,0.08)"))
        fig.add_hline(y=0, line_dash="dash", line_color=lib.MUTED)
        st.plotly_chart(lib.apply_theme(fig, 280, legend=True), use_container_width=True, key="fit_curve")

    st.markdown("## Weekly training load")
    acts = load_data("SELECT date, duration_minutes, avg_hr FROM activities "
                     "WHERE date >= date('now', ?) AND dup_of IS NULL ORDER BY date",
                     (f"-{days} days",))
    if acts.empty:
        st.caption("No activities in range.")
    else:
        acts["week"] = pd.to_datetime(acts["date"]).dt.to_period("W").apply(lambda x: x.start_time)
        wk = acts.groupby("week").agg(hours=("duration_minutes", lambda x: x.sum()/60),
                                      sessions=("date", "count")).reset_index()
        wk["week"] = wk["week"].dt.strftime("%Y-%m-%d")
        fig = go.Figure()
        fig.add_trace(go.Bar(x=wk["week"], y=wk["hours"], name="Hours", marker_color=lib.ACCENT))
        fig.add_trace(go.Scatter(x=wk["week"], y=wk["sessions"], name="Sessions", yaxis="y2",
                                 line=dict(color=lib.WARN, width=2)))
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False))
        st.plotly_chart(lib.apply_theme(fig, 220, legend=True), use_container_width=True, key="fit_wk")


# ===========================================================================
# ACTIVITY
# ===========================================================================
def fmt_dur(m):
    if pd.isna(m) or m == 0:
        return ""
    h, mm = divmod(int(m), 60)
    return f"{h}h {mm}m" if h else f"{mm}m"


def render_activity():
    # dup_of IS NULL keeps only canonical rows (the recording device's copy);
    # cross-source mirrors (e.g. the Strava twin of a Garmin workout) are hidden
    # so counts and totals aren't doubled. stream_id resolves the HR stream from
    # whichever copy in the dup group actually carries it (Strava collects them).
    df = load_data(
        """SELECT date, type, duration_minutes, distance_km, avg_hr, max_hr, calories,
                  COALESCE(
                    (SELECT a.source_id WHERE EXISTS
                       (SELECT 1 FROM activity_streams s WHERE s.activity_id = a.source_id)),
                    (SELECT a2.source_id FROM activities a2
                       JOIN activity_streams s ON s.activity_id = a2.source_id
                      WHERE a2.dup_of = a.id LIMIT 1),
                    a.source_id) AS stream_id
           FROM activities a
           WHERE date >= date('now', ?) AND dup_of IS NULL ORDER BY date DESC""",
        (f"-{days} days",))
    if df.empty:
        st.info("No activity data in range.")
        return

    hidden = load_data(
        "SELECT COUNT(*) n FROM activities WHERE date >= date('now', ?) AND dup_of IS NOT NULL",
        (f"-{days} days",))
    n_hidden = int(hidden["n"].iloc[0]) if not hidden.empty else 0
    if n_hidden:
        st.caption(f"↪ {n_hidden} duplicate {'copy' if n_hidden == 1 else 'copies'} "
                   "hidden (same workout mirrored from another source; the recording "
                   "device's copy is kept).")

    c = st.columns(4)
    c[0].metric("Activities", len(df))
    c[1].metric("Total time", f"{df['duration_minutes'].sum()/60:.0f}h")
    c[2].metric("Distance", f"{df['distance_km'].sum():.0f} km")
    avg = df["avg_hr"].dropna().mean()
    c[3].metric("Avg HR", f"{avg:.0f}" if pd.notna(avg) else "—")

    st.markdown("## Activity log")
    show = df[["date", "type", "duration_minutes", "distance_km", "avg_hr", "max_hr", "calories"]].copy()
    show["duration_minutes"] = show["duration_minutes"].apply(fmt_dur)
    show["distance_km"] = show["distance_km"].round(1)
    show.columns = ["Date", "Type", "Duration", "Distance (km)", "Avg HR", "Max HR", "Cal"]
    st.dataframe(show, use_container_width=True, hide_index=True, height=280)

    cc = st.columns(2)
    with cc[0]:
        st.markdown("### Types")
        tc = df["type"].value_counts().reset_index()
        tc.columns = ["Type", "Count"]
        fig = px.pie(tc, names="Type", values="Count", hole=0.5)
        st.plotly_chart(lib.apply_theme(fig, 220, legend=True), use_container_width=True, key="ac_pie")
    with cc[1]:
        st.markdown("### Per-activity heart rate")
        streamed = df[df["stream_id"].notna()].head(25)
        if streamed.empty:
            st.caption("No HR streams available.")
        else:
            opts = [f"{r['date']} — {r['type']} ({fmt_dur(r['duration_minutes'])})"
                    for _, r in streamed.iterrows()]
            sel = st.selectbox("Activity", opts, key="ac_pick")
            act_id = str(streamed.iloc[opts.index(sel)]["stream_id"])
            stream = load_data("SELECT timestamp_offset/60.0 minutes, bpm FROM activity_streams "
                               "WHERE activity_id = ? ORDER BY timestamp_offset", (act_id,))
            if stream.empty:
                st.caption("No HR stream for this activity.")
            else:
                fig = px.line(stream, x="minutes", y="bpm")
                fig.update_traces(line_color=lib.BAD)
                st.plotly_chart(lib.apply_theme(fig, 200), use_container_width=True, key="ac_stream")


# ===========================================================================
# WELLNESS
# ===========================================================================
def render_wellness():
    df = load_data("SELECT date, spo2, steps FROM wellness WHERE date >= date('now', ?) "
                   "ORDER BY date", (f"-{days} days",))
    if df.empty:
        st.info("No wellness data in range.")
        return

    steps = df[df["steps"].notna()].copy()
    if not steps.empty:
        st.markdown("## Daily steps")
        steps["roll"] = steps["steps"].rolling(7, min_periods=1).mean()
        c = st.columns(3)
        c[0].metric("Latest", f"{int(steps.iloc[-1]['steps']):,}")
        c[1].metric("7-day avg", f"{steps.tail(7)['steps'].mean():,.0f}")
        c[2].metric("Best in range", f"{int(steps['steps'].max()):,}")
        fig = go.Figure()
        fig.add_trace(go.Bar(x=steps["date"], y=steps["steps"], name="Steps",
                             marker_color=lib.ACCENT, opacity=0.55))
        fig.add_trace(go.Scatter(x=steps["date"], y=steps["roll"], name="7-day avg",
                                 line=dict(color=lib.INK, width=2)))
        st.plotly_chart(lib.apply_theme(fig, 230, legend=True), use_container_width=True, key="we_steps")

    spo2 = df[df["spo2"].notna()].copy()
    if not spo2.empty:
        st.markdown("## Blood oxygen (SpO₂)")
        fig = px.line(spo2, x="date", y="spo2", markers=True)
        fig.update_traces(line_color=lib.SLEEP)
        fig.add_hrect(y0=95, y1=100, fillcolor=lib.GOOD, opacity=0.07, line_width=0,
                      annotation_text="normal", annotation_position="top left")
        fig.update_yaxes(range=[85, 101])
        st.plotly_chart(lib.apply_theme(fig, 200), use_container_width=True, key="we_spo2")

    st.caption("Weight & body composition are on the roadmap.")


# --- dispatch --------------------------------------------------------------
{
    "Overview": render_overview,
    "Sleep": render_sleep,
    "Heart & HRV": render_heart,
    "Fitness": render_fitness,
    "Activity": render_activity,
    "Wellness": render_wellness,
}[page]()
