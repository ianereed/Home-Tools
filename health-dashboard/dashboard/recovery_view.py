"""Recovery: overnight and autonomic signals — sleep, HRV, resting HR, SpO2."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard import lib
from dashboard import metrics as M
from dashboard import targets as T


def _card(label, stats, fmt, unit, color, series):
    u = f" <small>{unit}</small>" if unit else ""
    value = lib.fmt(stats["avg7"], fmt) + (u if stats["avg7"] is not None else "")
    sub = (f"7-day avg · latest {lib.fmt(stats['latest'], fmt)}"
           f" ({stats['latest_date']:%b %-d}) · 30-day {lib.fmt(stats['avg30'], fmt)}"
           if stats["latest"] is not None else "no data in range")
    return lib.card(label, value, sub, spark=lib.spark_svg(series[-30:], lib.SERIES[color]))


def render_recovery():
    days = lib.page_header("Recovery", "Overnight and autonomic signals: sleep, HRV, resting heart "
                           "rate, blood oxygen.", with_range=True)
    start = M.since(days)
    sleep = M.sleep_nightly(start)
    hrv = M.hrv_daily(start)
    rhr = M.resting_hr_daily(start)
    spo2 = M.spo2_daily(start)

    lib.cards([
        _card("Sleep", M.window_stats(sleep, "hours"), "{:.1f}", "h", "sleep",
              sleep["hours"].tolist() if not sleep.empty else []),
        _card("HRV", M.window_stats(hrv, "hrv"), "{:.0f}", "ms", "hrv",
              hrv["hrv"].tolist() if not hrv.empty else []),
        _card("Resting HR", M.window_stats(rhr, "rhr"), "{:.0f}", "bpm", "rhr",
              rhr["rhr"].tolist() if not rhr.empty else []),
        _card("SpO₂", M.window_stats(spo2, "spo2"), "{:.0f}", "%", "spo2",
              spo2["spo2"].tolist() if not spo2.empty else []),
    ])

    # ---- sleep ------------------------------------------------------------
    st.markdown("## Sleep")
    if sleep.empty:
        st.caption("No sleep data in range.")
    else:
        best = sleep.loc[sleep["hours"].idxmax()]
        st.caption(f"{lib.range_label().capitalize()}: average {sleep['hours'].mean():.1f} h, "
                   f"longest {best['hours']:.1f} h ({best['date']:%b %-d}). Garmin first, then Apple; "
                   "nights over 14 h are device artifacts and left out.")
        lib.chart(lib.line_with_avg(sleep, "hours", lib.SERIES["sleep"], "Hours", 240, bars=True,
                                    ref=(T.SLEEP_TARGET_H, f"{T.SLEEP_TARGET_H:.0f} h")), "rc_sleep")

        stages = sleep[sleep["deep_h"].fillna(0) > 0]
        if not stages.empty:
            st.markdown("### Stages")
            fig = go.Figure()
            for col_, name in (("deep_h", "Deep"), ("rem_h", "REM"), ("light_h", "Light"), ("awake_h", "Awake")):
                fig.add_trace(go.Scatter(x=stages["date"], y=stages[col_], name=name, stackgroup="one",
                                         line=dict(width=0, color=lib.SLEEP_STAGES[name])))
            lib.chart(lib.apply_theme(fig, 220, legend=True), "rc_stages")

        cc = st.columns(2)
        with cc[0]:
            st.markdown("### Monthly average")
            m = sleep.groupby(sleep["date"].dt.strftime("%Y-%m"))["hours"].mean().reset_index()
            fig = px.bar(m, x="date", y="hours")
            fig.update_traces(marker_color=lib.SERIES["sleep"])
            lib.chart(lib.apply_theme(fig, 180), "rc_month")
        with cc[1]:
            st.markdown("### By weekday")
            w = sleep.groupby(sleep["date"].dt.dayofweek)["hours"].mean().reindex(range(7))
            fig = px.bar(x=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], y=w.values)
            fig.update_traces(marker_color=lib.SERIES["sleep"])
            lib.chart(lib.apply_theme(fig, 180), "rc_weekday")

        score, shr = M.sleep_score_daily(start), M.sleeping_hr_daily(start)
        sc = st.columns(2)
        if not score.empty:
            with sc[0]:
                st.markdown("### Sleep score (Garmin)")
                lib.chart(lib.line_with_avg(score, "sleep_score", lib.SERIES["sleep"], "Score", 180), "rc_score")
        if not shr.empty:
            with sc[1]:
                st.markdown("### Sleeping heart rate")
                lib.chart(lib.line_with_avg(shr, "avg_sleeping_hr", lib.SERIES["rhr"], "bpm", 180), "rc_shr")

        if not stages.empty:
            with st.expander("Single-night stage breakdown"):
                labels = [f"{d:%Y-%m-%d}" for d in stages["date"]][::-1]
                pick = st.selectbox("Night", labels, key="rc_pick")
                r = stages[stages["date"] == pd.Timestamp(pick)].iloc[0]
                names = ["Deep", "REM", "Light", "Awake"]
                fig = go.Figure(go.Pie(labels=names, hole=0.5,
                                       values=[r["deep_h"], r["rem_h"], r["light_h"], r["awake_h"]],
                                       marker_colors=[lib.SLEEP_STAGES[n] for n in names]))
                lib.chart(lib.apply_theme(fig, 240), "rc_pie")

    # ---- heart ------------------------------------------------------------
    st.markdown("## Heart-rate variability")
    if hrv.empty:
        st.caption("No HRV data in range.")
    else:
        lib.chart(lib.line_with_avg(hrv, "hrv", lib.SERIES["hrv"], "HRV", 230,
                                    ref=(hrv["hrv"].mean(), f"range avg {hrv['hrv'].mean():.0f} ms")), "rc_hrv")

    st.markdown("## Resting heart rate")
    if rhr.empty:
        st.caption("No resting heart rate in range.")
    else:
        st.caption("One value per day: Garmin's daily resting HR, Apple's when Garmin has none.")
        lib.chart(lib.line_with_avg(rhr, "rhr", lib.SERIES["rhr"], "Daily", 220), "rc_rhr")

    if not spo2.empty:
        st.markdown("## Blood oxygen (SpO₂)")
        fig = lib.line_with_avg(spo2, "spo2", lib.SERIES["spo2"], "SpO₂", 200)
        fig.add_hrect(y0=95, y1=100, fillcolor=lib.GOOD, opacity=0.06, line_width=0,
                      annotation_text="normal", annotation_position="top left")
        fig.update_yaxes(range=[85, 101])
        lib.chart(fig, "rc_spo2")
