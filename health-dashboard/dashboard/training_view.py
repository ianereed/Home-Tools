"""Training: load, volume, intensity and the activity log (information only —
day-to-day training guidance lives on the Garmin)."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard import lib
from dashboard import metrics as M
from dashboard import targets as T

_HEAVY_CAP_DAYS = M.LOAD_MAX_DAYS   # load model and HR-zone minutes scan per-second streams


def _fmt_dur(m) -> str:
    if m is None or m != m or m == 0:
        return ""
    h, mm = divmod(int(m), 60)
    return f"{h}h {mm}m" if h else f"{mm}m"


def _load_section(days: int):
    st.markdown("## Fitness & form")
    load = M.training_load_cached(min(days, _HEAVY_CAP_DAYS))
    if load.empty:
        st.caption("No activities yet.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=load["date"], y=load["ctl"], name="Fitness (CTL)",
                             line=dict(color=lib.SERIES["volume"], width=2)))
    fig.add_trace(go.Scatter(x=load["date"], y=load["atl"], name="Fatigue (ATL)",
                             line=dict(color=lib.SERIES["fatigue"], width=1)))
    fig.add_trace(go.Scatter(x=load["date"], y=load["tsb"], name="Form (TSB)",
                             line=dict(color=lib.SERIES["form"], width=1.5),
                             fill="tozeroy", fillcolor=lib.rgba(lib.SERIES["form"], 0.07)))
    fig.add_hline(y=0, line_dash="dash", line_color=lib.MUTED)
    lib.chart(lib.apply_theme(fig, 260, legend=True), "tr_load")
    note = " Covers the last year." if days > _HEAVY_CAP_DAYS else ""
    st.caption("Fitness is a 42-day average of training load (TRIMP from heart rate), fatigue a "
               "7-day average, form the difference: it dips while you build and rises as you "
               "taper. Informational only." + note)


def render_training():
    days = lib.page_header("Training", "Load, volume, intensity and the activity log. Day-to-day "
                           "guidance lives on your Garmin; this is the long view.", with_range=True)
    start = M.since(days)
    acts = M.activities(start)
    load = M.training_load_cached(60)
    ex = M.exercise_summary()
    steps = M.steps_daily(start)
    vol4 = M.weekly_volume(M.activities(M.since(5 * 7)))
    vol4 = vol4[vol4["week"] < M.this_monday()].tail(4) if not vol4.empty else vol4

    tiles = []
    if not load.empty:
        cur = load.iloc[-1]
        tiles.append(lib.card("Fitness (CTL)", f"{cur['ctl']:.0f}",
                              f"fatigue {cur['atl']:.0f} · form {cur['tsb']:+.0f}",
                              spark=lib.spark_svg(load["ctl"].tolist(), lib.SERIES["volume"])))
    tiles.append(lib.card("Training volume", lib.fmt(vol4["hours"].mean() if not vol4.empty else None,
                                                     "{:.1f}") + " <small>h/wk</small>",
                          "4-week avg, complete weeks"))
    tiles.append(lib.card("Moderate–vigorous", lib.fmt(ex["modvig"] if ex else None) + " <small>min/wk</small>",
                          f"HR zones 3–5 · 4-week avg · vigorous {lib.fmt(ex['vig'] if ex else None)}",
                          spark=lib.spark_svg(ex["spark"] if ex else [], lib.ZONE_COLORS["Z3"])))
    st_stats = M.window_stats(steps, "steps")
    tiles.append(lib.card("Steps", lib.fmt(st_stats["avg7"], "{:,.0f}"), "7-day avg",
                          spark=lib.spark_svg(steps["steps"].tolist()[-30:] if not steps.empty else [],
                                              lib.SERIES["steps"])))
    lib.cards(tiles)

    _load_section(days)

    st.markdown("## Weekly volume")
    wk = M.weekly_volume(acts)
    if wk.empty:
        st.caption("No activities in range.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=wk["week"], y=wk["hours"], name="Hours", marker_color=lib.SERIES["volume"]))
        fig.add_trace(go.Scatter(x=wk["week"], y=wk["sessions"], name="Sessions", yaxis="y2",
                                 line=dict(color=lib.MUTED, width=2)))
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False))
        lib.chart(lib.apply_theme(fig, 220, legend=True), "tr_volume")

    st.markdown("## Intensity — minutes per HR zone")
    zw = M.zone_minutes_weekly(M.since(min(days, _HEAVY_CAP_DAYS)))
    if zw.empty:
        st.caption("No activities with heart-rate streams in range.")
    else:
        fig = go.Figure()
        for z in T.ZONE_PCT:
            fig.add_trace(go.Bar(x=zw["week"], y=zw[z], name=z, marker_color=lib.ZONE_COLORS[z]))
        fig.add_trace(go.Scatter(x=zw["week"], y=zw["modvig"], name="Z3–5 total",
                                 line=dict(color=lib.INK, width=1.5, dash="dot")))
        fig.add_hline(y=T.AHA_MODERATE_MIN_WK, line_dash="dash", line_color=lib.MUTED,
                      annotation_text=f"AHA {T.AHA_MODERATE_MIN_WK} min/wk",
                      annotation_font_color=lib.MUTED, annotation_font_size=10)
        fig.update_layout(barmode="stack")
        lib.chart(lib.apply_theme(fig, 260, legend=True), "tr_zones")
        pct = T.ZONE_PCT
        st.caption(f"Zones are % of max HR {T.HRMAX} bpm (set on the Garmin): "
                   + " · ".join(f"{z} ≥{int(p * 100)}%" for z, p in list(pct.items())[1:])
                   + ". Counted from each activity's heart-rate samples.")

    if not steps.empty:
        st.markdown("## Daily steps")
        lib.chart(lib.line_with_avg(steps, "steps", lib.SERIES["steps"], "Steps", 220, bars=True), "tr_steps")

    st.markdown("## Activity log")
    if acts.empty:
        st.caption("No activities in range.")
        return
    hidden = M.read("SELECT COUNT(*) n FROM activities WHERE date >= ? AND dup_of IS NOT NULL", (start,))
    n_hidden = int(hidden["n"].iloc[0]) if not hidden.empty else 0
    st.caption(f"{len(acts)} sessions · {acts['duration_minutes'].sum() / 60:.0f} h · "
               f"{M.distance_km(acts):,.0f} km in {lib.range_label()}"
               + (f" · {n_hidden} cross-source duplicate(s) hidden" if n_hidden else "")
               + ". Distance leaves out rows that can't be real (no duration, or over 80 km/h).")
    show = acts[["date", "type", "duration_minutes", "distance_km", "avg_hr", "max_hr", "calories"]].copy()
    show["date"] = show["date"].dt.strftime("%Y-%m-%d")
    show["type"] = show["type"].str.replace("_", " ")
    show["duration_minutes"] = show["duration_minutes"].apply(_fmt_dur)
    show.loc[~acts["counts_distance"], "distance_km"] = None
    show.columns = ["Date", "Type", "Duration", "Distance (km)", "Avg HR", "Max HR", "kcal"]
    st.dataframe(show, use_container_width=True, hide_index=True, height=280,
                 column_config={"Distance (km)": st.column_config.NumberColumn(format="%.1f"),
                                "Avg HR": st.column_config.NumberColumn(format="%d"),
                                "Max HR": st.column_config.NumberColumn(format="%d"),
                                "kcal": st.column_config.NumberColumn(format="%d")})

    cc = st.columns(2)
    with cc[0]:
        st.markdown("### Hours by type")
        mix = (acts.assign(type=acts["type"].str.replace("_", " "))
                   .groupby("type")["duration_minutes"].sum().div(60).sort_values())
        fig = go.Figure(go.Bar(x=mix.values, y=mix.index, orientation="h",
                               marker_color=lib.SERIES["volume"]))
        fig = lib.apply_theme(fig, max(160, 22 * len(mix)))
        fig.update_layout(hovermode="y unified")
        lib.chart(fig, "tr_mix")
    with cc[1]:
        st.markdown("### Heart rate during an activity")
        streamed = acts[acts["stream_id"].notna()].head(25)
        if streamed.empty:
            st.caption("No heart-rate streams in range.")
        else:
            opts = [f"{r['date']:%Y-%m-%d} — {r['type'].replace('_', ' ')} ({_fmt_dur(r['duration_minutes'])})"
                    for _, r in streamed.iterrows()]
            sel = st.selectbox("Activity", opts, key="tr_pick")
            sid = str(streamed.iloc[opts.index(sel)]["stream_id"])
            s = M.read("SELECT timestamp_offset/60.0 AS minutes, bpm FROM activity_streams "
                       "WHERE activity_id = ? ORDER BY timestamp_offset", (sid,))
            fig = go.Figure(go.Scatter(x=s["minutes"], y=s["bpm"], mode="lines",
                                       line=dict(color=lib.SERIES["rhr"], width=1.5)))
            for z, p in list(T.ZONE_PCT.items())[2:]:
                fig.add_hline(y=p * T.HRMAX, line_dash="dot", line_color=lib.ZONE_COLORS[z], opacity=0.5)
            lib.chart(lib.apply_theme(fig, 220), "tr_stream")
