"""Cardiology dashboard page: the clinical record and long-term history.

Labs, therapy × lipids, blood-pressure history and its morning/evening
analysis, risk markers and life context. Current-vs-target numbers live on
Goals; weight and body composition on Nutrition; recent sleep, resting HR and
training on Recovery and Training. It reuses the figure and table builders in
`cardiology/build_report.py` so this page and the standalone HTML report never
drift; the report's figures are restyled into the dashboard theme here.

The cardiology module carries PHI (real lipid panels) and is intentionally NOT
committed to git — it is deployed to the homeserver out of band. app.py only
wires this page in when `cardiology/clinical_data.py` is present, and the import
below is lazy, so a checkout without the PHI files simply omits the page.
"""
import datetime
import os
import sqlite3
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_HD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CARDIO_DIR = os.path.join(_HD_ROOT, "cardiology")
if _CARDIO_DIR not in sys.path:
    sys.path.insert(0, _CARDIO_DIR)

import build_report as br      # noqa: E402  (cardiology/build_report.py)
import clinical_data as CD     # noqa: E402  (cardiology/clinical_data.py)
import collectors.db as db     # noqa: E402
from dashboard import lib      # noqa: E402
from dashboard import metrics as M   # noqa: E402
from dashboard import targets as T   # noqa: E402


# Clinical tables and the summary box (cards come from lib.CSS, which also
# styles the report's stat cards: same .cards/.card markup).
_CARD_CSS = """
<style>
.cardio.summary{background:#10261a;border-left:3px solid #4ade80;padding:12px 16px;
  border-radius:6px;font-size:14px;line-height:1.55;margin:6px 0 14px;}
table.lipid{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0;}
table.lipid th,table.lipid td{border:1px solid #2a2f3a;padding:5px 8px;text-align:left;}
table.lipid th{background:#1a1f2b;} table.lipid td.hi{color:#f87171;font-weight:600;}
table.qtab td,table.qtab th{text-align:right;}
table.qtab td:first-child,table.qtab th:first-child{text-align:left;}
table.qtab tr.empty td{color:#4a5163;}
ul.rm{font-size:13px;line-height:1.6;} .meta{color:#8b93a7;font-size:12px;}
</style>
"""


@st.cache_data(ttl=1800, show_spinner="Building cardiology dataset…")
def _frames():
    """Heavy daily->quarter/week aggregation (scans activity_streams). Cached so
    it only runs on first view / after the TTL, not on every Streamlit rerun."""
    con = sqlite3.connect(db.DB_PATH)
    try:
        cal = br.build_daily_frame(con)          # opens/closes its own apple sidecar
        q = br.reindex_full(br.summarize(cal, "quarter"), "quarter")
        w = br.reindex_full(br.summarize(cal, "week"), "week")
        w = w[pd.to_datetime(w["bucket_start"]) >= pd.Timestamp(br.WEEKLY_START)] \
            .reset_index(drop=True)
        n_streams = int(br.load(
            con, "SELECT COUNT(DISTINCT activity_id) n FROM activity_streams").iloc[0]["n"])
        data_min = cal["date"].min().date().isoformat()
        data_max = cal["date"].max().date().isoformat()
    finally:
        con.close()
    return q, w, {"data_min": data_min, "data_max": data_max, "acts_with_streams": n_streams}


def _lab_table_html(lip: pd.DataFrame) -> str:
    """Complete per-draw lab panel table (mirrors the report's labs section)."""
    d = lip.copy()
    d["date"] = d["date"].dt.strftime("%Y-%m-%d")
    d["statin"] = d["statin_dose_mg"].apply(lambda x: f"{int(x)} mg" if x else "—")
    cols = [("date", "Date"), ("statin", "Statin"), ("total_chol", "TC"), ("trig", "Trig"),
            ("hdl", "HDL"), ("ldl", "LDL"), ("apob", "ApoB"), ("lpa_nmol_l", "Lp(a)"),
            ("note", "Context")]
    thead = "".join(f"<th>{lbl}</th>" for _, lbl in cols)
    ints = ("total_chol", "trig", "hdl", "ldl", "apob")
    rows = ""
    for _, r in d.iterrows():
        cells = ""
        for col, _lbl in cols:
            v = r[col]
            v = "" if pd.isna(v) else (str(int(v)) if col in ints and pd.notna(v) else v)
            flag = ""
            if col == "ldl" and r["ldl"] and r["ldl"] > 99:
                flag = " class=hi"
            if col == "apob" and pd.notna(r["apob"]) and r["apob"] >= 90:
                flag = " class=hi"
            cells += f"<td{flag}>{v}</td>"
        rows += f"<tr>{cells}</tr>"
    return f"<table class=lipid><thead><tr>{thead}</tr></thead><tbody>{rows}</tbody></table>"


def _timing_history(m: dict) -> str:
    """'<br>bedtime from 2026-08-27 → morning from 2026-10-04' when the dosing time changed."""
    history = m.get("dosing_history") or []
    if len(history) < 2:
        return ""
    return "<br><span class=meta>" + " → ".join(
        f"{_short_timing(t)} from {d}" for d, t in history) + "</span>"


def _medication_history_html() -> str:
    """Every recorded medication, current and past, oldest first — the record,
    not the regimen (current cards are on Goals)."""
    meds = sorted(getattr(CD, "MEDICATIONS", []), key=lambda m: m.get("start") or "9999")
    if not meds:
        return ""
    rows = "".join(
        f'<tr><td>{m.get("name", "")}{" (" + m["brand"] + ")" if m.get("brand") else ""}</td>'
        f'<td>{m.get("dose", "")} · {m.get("frequency", "")}{_timing_history(m)}</td>'
        f'<td>{m.get("start") or "—"}</td><td>{m.get("stop") or ""}</td>'
        f'<td class=meta>{m.get("status", "")} · {m.get("purpose", "")}</td></tr>' for m in meds)
    return ('<table class=lipid><thead><tr><th>Medication</th><th>Dose</th><th>Start</th>'
            f'<th>Stop</th><th>Status · purpose</th></tr></thead><tbody>{rows}</tbody></table>')


def _bp_daypart_stats(bp: pd.DataFrame, window_days: int = 90,
                      now: pd.Timestamp | None = None) -> dict:
    """Morning (before noon) vs afternoon/evening (noon on) means over the
    trailing window. Returns {"n", "am", "pm", "gap"}; a cohort with zero
    readings in the window is None, and "gap" (AM minus PM, per component)
    needs both. Pure function so the split logic tests without Streamlit."""
    now = now if now is not None else pd.Timestamp.now()
    win = bp[bp["timestamp"] >= now - pd.Timedelta(days=window_days)]
    out = {"n": len(win)}
    for key, sub in (("am", win[win["timestamp"].dt.hour < 12]),
                     ("pm", win[win["timestamp"].dt.hour >= 12])):
        out[key] = (None if sub.empty else
                    {"n": len(sub), "sys": sub["systolic"].mean(),
                     "dia": sub["diastolic"].mean()})
    out["gap"] = ((out["am"]["sys"] - out["pm"]["sys"],
                   out["am"]["dia"] - out["pm"]["dia"])
                  if out["am"] and out["pm"] else None)
    return out


def _bp_med_starts() -> list[tuple[pd.Timestamp, str, str]]:
    """(start, display name, frequency) of BP-purpose medications, ascending.
    Read from CD at runtime — committed code carries no med-name literals.
    The start carries a clock time when the PHI file records one (optional
    "start_time" key): a bedtime regimen can begin after midnight, which
    changes which mornings count as post-med."""
    starts = []
    for m in _bp_meds():
        ts = pd.Timestamp(f'{m["start"]} {m.get("start_time") or "00:00"}')
        history = m.get("dosing_history") or []
        timing = history[0][1] if history else (m.get("frequency") or "")
        starts.append((ts, m.get("brand") or m.get("name", ""), timing.lower()))
    return sorted(starts)


def _bp_meds() -> list[dict]:
    return [m for m in getattr(CD, "MEDICATIONS", [])
            if any(k in (m.get("purpose") or "").lower() for k in ("blood-pressure", "blood pressure"))
            and m.get("start")]


def _bp_med_timing_changes() -> list[tuple[pd.Timestamp, str, str]]:
    """(date, display name, new timing) for every change of dosing time after
    the first, from the optional "dosing_history" key: [(date, timing), ...]."""
    out = []
    for m in _bp_meds():
        for d, timing in (m.get("dosing_history") or [])[1:]:
            out.append((pd.Timestamp(d), m.get("brand") or m.get("name", ""), timing))
    return sorted(out)


def _short_timing(timing: str) -> str:
    t = timing.lower()
    return "morning" if "morning" in t else "bedtime" if "bedtime" in t else t


def _panel_chrome(fig, row, label, goal, y_lo, y_hi):
    """Split-panel dressing: y-range clamped to the data (the whole point —
    BP lives in 70-150, an axis from 0 buries the signal), a faint target
    tint below the goal, one dashed goal line with an in-panel label, and a
    muted panel title."""
    fig.update_yaxes(range=[y_lo, y_hi], row=row, col=1)
    fig.add_hrect(y0=y_lo, y1=goal, fillcolor=lib.GOOD, opacity=0.05,
                  line_width=0, row=row, col=1)
    fig.add_hline(y=goal, line_dash="dash", line_color=lib.GOOD, line_width=1.5,
                  row=row, col=1)
    fig.add_annotation(text=f"goal <{goal}", x=1, xref="x domain", xanchor="right",
                       y=goal, yanchor="top", yshift=-2, showarrow=False,
                       font=dict(size=10, color=lib.GOOD), row=row, col=1)
    fig.add_annotation(text=label, x=0, xref="x domain", xanchor="left",
                       y=1, yref="y domain", yanchor="bottom", yshift=2,
                       showarrow=False, font=dict(size=11, color=lib.MUTED),
                       row=row, col=1)


def _yrange(series_list, goal, pad=4):
    """Data-clamped axis range that always keeps the goal line in view."""
    vals = pd.concat([s for s in series_list if not s.empty])
    return min(vals.min(), goal) - pad, max(vals.max(), goal) + pad


def _daypart_theme(fig, height):
    """House theme + the split-panel specifics the demo approved: legend on
    top (two cohort entries) and headroom for the in-panel annotations."""
    lib.apply_theme(fig, height, legend=True)
    fig.update_layout(
        margin=dict(l=8, r=8, t=30, b=8),
        legend=dict(orientation="h", y=1.13, x=0, font=dict(size=11)))
    return fig


_DAYPARTS = (("am", "#facc15", "Morning"), ("pm", "#818cf8", "Afternoon & evening"))


def _bp_goal() -> tuple[int, int]:
    return T.bp_goal(getattr(CD, "CARDIO_GOALS", None))


def _med_markers(fig, med_starts, timing_changes=()):
    """Dotted start marker in both panels per BP med, labeled (bedtime-aware)
    in the top panel, plus a dashed marker wherever the dosing time changed.
    Shape + annotation, never add_vline (date-axis crash)."""
    for when, name, timing in timing_changes:
        for row in (1, 2):
            fig.add_shape(type="line", x0=when, x1=when, y0=0, y1=1,
                          xref="x", yref="y domain",
                          line=dict(color=lib.GOOD, dash="dash", width=1),
                          opacity=0.7, row=row, col=1)
        fig.add_annotation(x=when, y=1, yref="y domain", yanchor="bottom",
                           yshift=14, text=f"{name} → {_short_timing(timing)}", showarrow=False,
                           xanchor="left", xshift=4,
                           font=dict(size=10, color=lib.GOOD), row=1, col=1)
    for start, name, freq in med_starts:
        for row in (1, 2):
            fig.add_shape(type="line", x0=start, x1=start, y0=0, y1=1,
                          xref="x", yref="y domain",
                          line=dict(color=lib.GOOD, dash="dot", width=1.5),
                          opacity=0.9, row=row, col=1)
        label = name + (" (bedtime)" if "bedtime" in freq else "")
        fig.add_annotation(x=start, y=1, yref="y domain", yanchor="bottom",
                           yshift=14, text=label, showarrow=False,
                           xanchor="right", xshift=-4,
                           font=dict(size=10, color=lib.GOOD), row=1, col=1)


def _bp_daily_smooth(bp: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Daily means smoothed with a trailing calendar-day window. Day-first so
    a 5-reading morning weighs the same as a single-reading day, and
    calendar-based so the old count-based window (7 *readings* ≈ anywhere
    from 2 days to 3 weeks of wall time) can't wiggle per cuff press or
    average across a data gap."""
    daily = (bp.set_index("timestamp")
               .resample("D")[["systolic", "diastolic"]]
               .mean().dropna(how="all"))
    return daily.rolling(f"{window_days}D", min_periods=1).mean()


def _bp_history_fig(bp: pd.DataFrame, med_starts, window_days: int = 3,
                    timing_changes=()) -> "go.Figure":
    """The section's headline chart: every reading + a running average, as
    stacked SYSTOLIC/DIASTOLIC panels with data-clamped axes, one goal line
    each, and a stage-2 reference instead of the full band overlay (which
    pinned the old axis to 0). Pure figure builder (no Streamlit)."""
    from plotly.subplots import make_subplots

    d = bp.copy()
    smooth = _bp_daily_smooth(d, window_days)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.16)
    for row, col_, color, stage2 in ((1, "systolic", lib.SERIES["sys"], T.BP_STAGE2[0]),
                                     (2, "diastolic", lib.SERIES["dia"], T.BP_STAGE2[1])):
        fig.add_trace(go.Scatter(
            x=d["timestamp"], y=d[col_], mode="markers", showlegend=False,
            marker=dict(color=color, size=7, opacity=0.55,
                        line=dict(color="rgba(0,0,0,0.5)", width=1)),
            hovertemplate="%{y} mmHg<extra></extra>"), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=smooth.index, y=smooth[col_], mode="lines", showlegend=False,
            line=dict(color=color, width=2.5),
            hovertemplate=f"{window_days}d mean %{{y:.0f}}<extra></extra>"),
            row=row, col=1)
        fig.add_hline(y=stage2, line_dash="dot", line_color=lib.BAD,
                      line_width=1, opacity=0.6, row=row, col=1)
        fig.add_annotation(text=f"stage 2 ≥{stage2}", x=0, xref="x domain",
                           xanchor="left", y=stage2, yanchor="bottom", yshift=2,
                           showarrow=False, font=dict(size=10, color=lib.BAD),
                           opacity=0.8, row=row, col=1)

    gs, gd = _bp_goal()
    slo, shi = _yrange([d["systolic"]], gs)
    dlo, dhi = _yrange([d["diastolic"]], gd)
    _panel_chrome(fig, 1, "SYSTOLIC — mmHg", gs, slo, shi)
    _panel_chrome(fig, 2, "DIASTOLIC — mmHg", gd, dlo, dhi)
    _med_markers(fig, med_starts, timing_changes)
    fig.update_layout(hovermode="x unified")
    fig = _daypart_theme(fig, 430)
    fig.update_layout(showlegend=False)
    return fig


def _daypart_weekly_fig(bp: pd.DataFrame, med_starts, timing_changes=()) -> "go.Figure":
    """Weekly AM/PM means as stacked SYSTOLIC/DIASTOLIC panels, each with its
    own data-clamped axis and goal line, plus BP-med start markers. Pure
    figure builder (no Streamlit) so tests and the mini can run it headless."""
    from plotly.subplots import make_subplots

    wk = bp.copy()
    wk["week"] = M.week_start(wk["timestamp"])
    wk["daypart"] = (wk["timestamp"].dt.hour < 12).map({True: "am", False: "pm"})
    g = (wk.groupby(["week", "daypart"])[["systolic", "diastolic"]]
           .mean().reset_index())
    am, pm = g[g["daypart"] == "am"], g[g["daypart"] == "pm"]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.16)
    for row, col_ in ((1, "systolic"), (2, "diastolic")):
        for sub, (_, color, name) in zip((am, pm), _DAYPARTS):
            if sub.empty:
                continue
            fig.add_trace(go.Scatter(
                x=sub["week"], y=sub[col_], mode="lines+markers", name=name,
                line=dict(color=color, width=2.5), marker=dict(size=6),
                showlegend=(row == 1), legendgroup=name,
                hovertemplate="%{y:.0f}<extra>" + name + "</extra>"),
                row=row, col=1)

    gs, gd = _bp_goal()
    slo, shi = _yrange([am["systolic"], pm["systolic"]], gs)
    dlo, dhi = _yrange([am["diastolic"], pm["diastolic"]], gd)
    _panel_chrome(fig, 1, "SYSTOLIC — weekly mean, mmHg", gs, slo, shi)
    _panel_chrome(fig, 2, "DIASTOLIC — weekly mean, mmHg", gd, dlo, dhi)
    _med_markers(fig, med_starts, timing_changes)
    fig.update_layout(hovermode="x unified")
    return _daypart_theme(fig, 430)


def _daypart_clock_fig(bp: pd.DataFrame, now: pd.Timestamp | None = None) -> "go.Figure":
    """Trailing-90-day readings by clock time as stacked SYSTOLIC/DIASTOLIC
    panels with a 3-hour mean line (the diurnal shape) and a noon divider.
    Pure figure builder (no Streamlit)."""
    from plotly.subplots import make_subplots

    now = now if now is not None else pd.Timestamp.now()
    win = bp[bp["timestamp"] >= now - pd.Timedelta(days=90)].copy()
    if win.empty:
        return go.Figure()
    win["clock"] = win["timestamp"].dt.hour + win["timestamp"].dt.minute / 60
    win["daypart"] = (win["timestamp"].dt.hour < 12).map({True: "am", False: "pm"})
    win["label"] = win["timestamp"].dt.strftime("%b %d · %H:%M")

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.16)
    for row, col_ in ((1, "systolic"), (2, "diastolic")):
        for part, color, name in _DAYPARTS:
            sub = win[win["daypart"] == part]
            if sub.empty:
                continue
            fig.add_trace(go.Scatter(
                x=sub["clock"], y=sub[col_], mode="markers", name=name,
                marker=dict(color=color, size=9, opacity=0.75,
                            line=dict(color="rgba(0,0,0,0.5)", width=1)),
                showlegend=(row == 1), legendgroup=name, customdata=sub["label"],
                hovertemplate="%{customdata} — %{y} mmHg<extra></extra>"),
                row=row, col=1)
        b3 = win.copy()
        b3["bin"] = (b3["clock"] // 3) * 3 + 1.5
        m3 = b3.groupby("bin")[col_].mean().reset_index()
        fig.add_trace(go.Scatter(
            x=m3["bin"], y=m3[col_], mode="lines", name="3-hour mean",
            line=dict(color=lib.INK, width=2), opacity=0.45,
            showlegend=(row == 1), legendgroup="mean",
            hovertemplate="3h mean %{y:.0f}<extra></extra>"), row=row, col=1)

    gs, gd = _bp_goal()
    slo, shi = _yrange([win["systolic"]], gs)
    dlo, dhi = _yrange([win["diastolic"]], gd)
    _panel_chrome(fig, 1, "SYSTOLIC — each reading, trailing 90 days", gs, slo, shi)
    _panel_chrome(fig, 2, "DIASTOLIC — each reading, trailing 90 days", gd, dlo, dhi)
    for row in (1, 2):
        fig.add_shape(type="line", x0=12, x1=12, y0=0, y1=1, xref="x",
                      yref="y domain",
                      line=dict(color=lib.MUTED, dash="dot", width=1),
                      opacity=0.6, row=row, col=1)
    fig.add_annotation(x=12, y=1, yref="y domain", yanchor="bottom", yshift=14,
                       text="noon", showarrow=False,
                       font=dict(size=10, color=lib.MUTED), row=1, col=1)
    fig.update_xaxes(range=[5, 24], tickvals=[6, 9, 12, 15, 18, 21, 24],
                     ticktext=["6am", "9am", "noon", "3pm", "6pm", "9pm", "12am"])
    fig = _daypart_theme(fig, 400)
    fig.update_layout(hovermode="closest")
    return fig


def _mornings_after(bp: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """AM readings attributable to a med. When the first-dose clock time is
    known (start carries a non-midnight time), count strictly from that
    moment — an after-midnight first dose makes the same day's mornings
    post-med. With a date-only start, count from the first morning AFTER the
    start date: a bedtime-dosed med's start-day morning predates its first
    dose, so day-of readings must not count as "since" the med."""
    boundary = (start if start.time() != datetime.time(0)
                else start + pd.Timedelta(days=1))
    return bp[(bp["timestamp"] >= boundary) & (bp["timestamp"].dt.hour < 12)]


def _render_bp_am_pm(bp: pd.DataFrame):
    """Morning-vs-evening split: daypart metrics (trailing 90 days), weekly
    AM/PM mean trends with BP-medication start markers, and a reading-by-
    clock-time scatter. Motivated by the morning-surge pattern (readings
    before noon run ~9/5 mmHg higher) and the BP med's dosing time (bedtime at
    the start, later moved to the morning; see the dosing-time marker)."""
    st.markdown("### Morning vs evening")
    stats = _bp_daypart_stats(bp)
    med_starts = _bp_med_starts()

    def _mmhg(d):
        return f"{d['sys']:.0f}/{d['dia']:.0f}" if d else "—"

    tiles = [
        lib.card("Morning", _mmhg(stats["am"]),
                 f"before noon · trailing 90 days · n={stats['am']['n'] if stats['am'] else 0}"),
        lib.card("Afternoon & evening", _mmhg(stats["pm"]),
                 f"from noon · trailing 90 days · n={stats['pm']['n'] if stats['pm'] else 0}"),
        lib.card("Morning minus evening",
                 f"{stats['gap'][0]:+.0f}/{stats['gap'][1]:+.0f}" if stats["gap"] else "—",
                 "positive = mornings run higher"),
    ]
    if med_starts:
        start, name, _freq = med_starts[-1]
        post = _mornings_after(bp, start)
        when = (f"from the first dose ({start:%Y-%m-%d %H:%M})"
                if start.time() != datetime.time(0)
                else f"from the morning after the {start.date()} start")
        tiles.append(lib.card(f"Mornings since {name}",
                              f"{post['systolic'].mean():.0f}/{post['diastolic'].mean():.0f}"
                              if len(post) else "—",
                              f"n={len(post)} · {when} — the number the med should move"))
    lib.cards(tiles)

    lib.chart(_daypart_weekly_fig(bp, med_starts, _bp_med_timing_changes()), "cardio_bp_daypart_weekly")
    st.caption("Weekly means, split at noon — systolic and diastolic each get "
               "their own axis and goal line. Dotted green marker: BP-medication start; "
               "dashed: a change in when it's taken.")

    fig_clock = _daypart_clock_fig(bp)
    if fig_clock.data:
        lib.chart(fig_clock, "cardio_bp_clock")
        st.caption("Each reading by clock time, trailing 90 days — the gray "
                   "line is the 3-hour average, the shape of your day.")


_EVENT_COLOR = "#f0abfc"


def _lipid_lanes():
    """Regimen lanes for the lipid-lowering medications only (BP meds stay off)."""
    from dashboard.goals_view import _med_lanes
    meds = [m for m in getattr(CD, "MEDICATIONS", [])
            if "ldl" in (m.get("purpose") or "").lower()]
    return _med_lanes(getattr(CD, "STATIN_EVENTS", []), meds, getattr(CD, "STATIN", None))


def _lipid_history_fig(q: pd.DataFrame, lip: pd.DataFrame, end_ts) -> "go.Figure":
    """The therapy × lipids × exercise history as three stacked panels on one
    real-date axis: LDL-C/ApoB draws with the goal lines, the lipid regimen
    as lanes, and quarterly exercise intensity. Clinical events are dotted
    verticals through all three, named on hover (and listed under the chart)
    rather than in labels that pile up."""
    from plotly.subplots import make_subplots

    lanes = _lipid_lanes()
    lane_order = list(dict.fromkeys(seg["lane"] for seg in lanes))
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        row_heights=[0.48, 0.07 + 0.05 * max(len(lane_order), 1), 0.32])
    x0 = min([lip["date"].min(), pd.to_datetime(q["bucket_start"]).min()]
             + [seg["start"] for seg in lanes]) - pd.Timedelta(days=30)
    x1 = pd.Timestamp(end_ts) + pd.Timedelta(days=45)

    # ① lipids
    fig.add_trace(go.Scatter(
        x=lip["date"], y=lip["ldl"], name="LDL-C", mode="lines+markers+text",
        text=[lib.fmt(v, "{:.0f}", "") for v in lip["ldl"]], textposition="top center",
        textfont=dict(size=10, color=lib.SERIES["ldl"]),
        line=dict(color=lib.SERIES["ldl"], width=2), marker=dict(size=8),
        hovertemplate="%{x|%b %d, %Y} · LDL-C %{y:.0f} mg/dL<extra></extra>"), row=1, col=1)
    apo = lip[lip["apob"].notna()]
    if not apo.empty:
        fig.add_trace(go.Scatter(
            x=apo["date"], y=apo["apob"], name="ApoB", mode="lines+markers+text",
            text=[f"{v:.0f}" for v in apo["apob"]], textposition="bottom center",
            textfont=dict(size=9, color=lib.SERIES["apob"]),
            line=dict(color=lib.SERIES["apob"], width=1.5, dash="dot"), marker=dict(size=6),
            hovertemplate="%{x|%b %d, %Y} · ApoB %{y:.0f} mg/dL<extra></extra>"), row=1, col=1)
    from dashboard.goals_view import add_ldl_goal_line, add_panel_tag, add_zero_line, draw_med_lanes
    add_ldl_goal_line(fig, getattr(CD, "CARDIO_GOALS", None))
    add_zero_line(fig)
    top = max(lip["ldl"].max(), apo["apob"].max() if not apo.empty else 0)

    # ② regimen lanes
    draw_med_lanes(fig, lanes, 2, pd.Timestamp(end_ts))

    # ③ quarterly exercise intensity
    qq = q.copy()
    qq["mid"] = pd.to_datetime(qq["bucket_start"]) + pd.Timedelta(days=45)
    for col, name, color, off in (("mod_vigorous_min_per_week", "Moderate-vigorous min/wk (Z3–5)", lib.ZONE_COLORS["Z3"], -17),
                                  ("vigorous_min_per_week", "Vigorous min/wk (Z4–5)", lib.ZONE_COLORS["Z4"], 17)):
        fig.add_trace(go.Bar(
            x=qq["mid"] + pd.Timedelta(days=off), y=qq[col], name=name, marker_color=color, opacity=0.85,
            width=30 * 86400000,
            customdata=qq["bucket"].map(br.quarter_short),
            hovertemplate="%{customdata} · %{y:.0f} min/wk<extra>" + name + "</extra>"), row=3, col=1)
    for y, color, text in ((T.AHA_MODERATE_MIN_WK, lib.ZONE_COLORS["Z3"], f"AHA ≥{T.AHA_MODERATE_MIN_WK}"),
                           (T.AHA_VIGOROUS_MIN_WK, lib.ZONE_COLORS["Z4"], f"AHA ≥{T.AHA_VIGOROUS_MIN_WK} vigorous")):
        fig.add_hline(y=y, line_dash="dash", line_color=color, opacity=0.6, row=3, col=1,
                      annotation_text=text, annotation_position="top left",
                      annotation_font_size=9, annotation_font_color=color)

    # clinical events: dotted verticals through every panel, named on hover
    events = sorted(getattr(CD, "EVENTS", []), key=lambda e: e[0])
    for d, label, _note in events:
        fig.add_shape(type="line", x0=pd.Timestamp(d), x1=pd.Timestamp(d), y0=0, y1=1, xref="x", yref="paper",
                      line=dict(color=_EVENT_COLOR, dash="dot", width=1), opacity=0.45)
    if events:
        fig.add_trace(go.Scatter(
            x=[pd.Timestamp(d) for d, *_ in events], y=[top * 1.12] * len(events), mode="markers",
            name="clinical event", marker=dict(symbol="triangle-down", size=9, color=_EVENT_COLOR),
            customdata=[label for _, label, _ in events],
            hovertemplate="%{x|%b %d, %Y} · %{customdata}<extra></extra>"), row=1, col=1)

    fig.update_xaxes(range=[x0, x1])
    fig.update_yaxes(range=[0, top * 1.2], row=1, col=1)
    add_panel_tag(fig, 1, "CHOLESTEROL — LDL-C & ApoB, mg/dL")
    add_panel_tag(fig, 2, "MEDICATIONS — lipid-lowering")
    add_panel_tag(fig, 3, "EXERCISE — minutes per week, by quarter")
    fig = lib.apply_theme(fig, 660, legend=True)
    fig.update_layout(barmode="overlay", hovermode="closest", legend=dict(y=-0.08),
                      margin=dict(l=8, r=8, t=28, b=8))
    return fig


def _only_ldl_goal(fig) -> "go.Figure":
    """Report figures draw an "LDL target" line and, when one is recorded, an
    "LDL stretch" line; the dashboard tracks one goal (targets.ldl_goal).
    Keep the line at that value, labeled "LDL goal <N", and drop the other."""
    goal = T.ldl_goal(getattr(CD, "CARDIO_GOALS", None))
    ldl = [a for a in fig.layout.annotations if (a.text or "").startswith(("LDL target", "LDL stretch"))]
    drop = {a.y for a in ldl if a.y != goal}
    fig.layout.annotations = [a for a in fig.layout.annotations if not (a in ldl and a.y in drop)]
    fig.layout.shapes = [sh for sh in fig.layout.shapes
                         if not (sh.type == "line" and sh.y0 in drop and sh.y0 == sh.y1)]
    for a in fig.layout.annotations:
        if a in ldl and a.y == goal:
            a.text = f"LDL goal <{goal}"
    return fig


def _render_lipid_history(q, lip, end_ts):
    st.markdown("## Lipid response to therapy × exercise")
    st.caption("Three panels on one timeline: cholesterol (LDL-C and ApoB draws, with the goal "
               "line), the lipid-lowering medications (one color per medication, alternating "
               "shades at each dose change), and exercise intensity by quarter from heart-rate "
               "zones. Pink triangles and dotted lines = clinical events (hover for the name; "
               "listed below).")
    lib.chart(_lipid_history_fig(q, lip, end_ts), "cardio_lipid_history")
    events = sorted(getattr(CD, "EVENTS", []), key=lambda e: e[0])
    if events:
        with st.expander("Clinical events on this chart"):
            st.markdown("".join(f"<div style='font-size:13px;margin:2px 0'><b>{pd.Timestamp(d):%b %-d, %Y}</b> · "
                                f"{label} <span class=meta>— {note}</span></div>" for d, label, note in events),
                        unsafe_allow_html=True)


def _render_bp_section():
    """Blood-pressure history: every reading with a running average, the
    morning-vs-evening split, and the raw readings. Empty-safe."""
    st.markdown("## Blood pressure history")
    bp = M.bp_readings()
    if bp.empty:
        st.caption("No blood-pressure readings yet — they sync from Garmin Connect "
                   "(cuff or manual entries).")
        return

    latest = bp.iloc[-1]
    cat, color = M.bp_category(latest["systolic"], latest["diastolic"])
    days_ago = (pd.Timestamp.now().normalize() - latest["timestamp"].normalize()).days
    lib.cards([
        lib.card("Latest reading", f"{int(latest['systolic'])}/{int(latest['diastolic'])}",
                 f"{cat} · {latest['timestamp']:%b %-d %H:%M} ({days_ago} d ago)", color),
        lib.card("Readings", f"{len(bp)}", f"since {bp['timestamp'].iloc[0]:%b %Y}"),
    ])
    st.caption("The current 14-day mean against the goal is on Goals.")

    lib.chart(_bp_history_fig(bp, _bp_med_starts(), timing_changes=_bp_med_timing_changes()), "cardio_bp")
    st.caption("Dots are readings, the line is a 3-day running average of "
               "daily means. Dashed green = goal; dotted red = AHA stage 2; green verticals = "
               "BP-medication start (dotted) and dosing-time changes (dashed).")

    _render_bp_am_pm(bp)

    with st.expander("All readings"):
        table = bp.copy()
        table["category"] = table.apply(
            lambda r: M.bp_category(r["systolic"], r["diastolic"])[0], axis=1)
        table["timestamp"] = table["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(
            table[["timestamp", "systolic", "diastolic", "pulse", "category"]].iloc[::-1],
            use_container_width=True, hide_index=True)


# Report charts that duplicate another tab (HR-zone minutes -> Training;
# weekly resting HR, sleep, HRV and steps -> Recovery/Training) are left out.
_REPORT_DETAIL_KEEP = ("Weekly detail", "Quarterly — Exercise intensity")


def render_cardiology():
    st.markdown(_CARD_CSS, unsafe_allow_html=True)
    lib.page_header("Cardiology", "The clinical record and long-term history: labs, therapy, "
                    "blood-pressure history, risk markers. Current numbers against goals are on Goals.")

    lip = br.lipids_df()

    # _frames() scans activity_streams/sleep/heart_rate/wellness/activities and
    # raises SystemExit("No data in DB.") — not just Exception — when none of
    # those tables have any rows yet (a freshly-initialized DB, day one before
    # any wearable sync). Catch both so that case degrades to skipping the
    # activity-history sections below rather than blanking the whole page.
    try:
        q, w, meta = _frames()
    except (Exception, SystemExit) as e:
        q = w = meta = None
        frames_error = str(e)
    else:
        frames_error = None

    activity_note = (f"activity data through {meta['data_max']}. " if meta
                     else "no wearable activity history in this DB yet. ")
    st.caption(f"{CD.PATIENT_NAME} · {CD.SEX} · DOB {CD.DOB} · {CD.DESCRIPTOR} · "
               f"{activity_note}Lipid/statin data is transcribed "
               "from LabCorp panels; verify against originals before any clinical decision.")
    st.markdown(f'<div class="cardio summary"><b>Clinical picture.</b> {CD.CLINICAL_SUMMARY}</div>',
                unsafe_allow_html=True)

    st.markdown(br.stat_cards_html(lip), unsafe_allow_html=True)

    if meta is None:
        st.info("No wearable activity history yet — the exercise/lipid overlay, quarterly "
                f"summary, and detail charts need at least one day of synced data ({frames_error}).")
    else:
        end_ts = pd.Timestamp(meta["data_max"])

        # ---- headline overlay ------------------------------------------------
        _render_lipid_history(q, lip, end_ts)

        # ---- quarterly summary table ----------------------------------------
        st.markdown("## Quarterly summary — therapy, labs & lifestyle")
        st.caption("One row per quarter; lab values are the last draw inside the quarter. "
                   "Red = above target. **Bold dose ↑** = changed during that quarter. "
                   "Grayed rows = no wearable data that quarter.")
        st.markdown(br.quarterly_table_html(q, lip), unsafe_allow_html=True)

        # ---- detailed charts -------------------------------------------------
        with st.expander("Detailed charts — lipids × activity, exercise intensity by quarter",
                         expanded=False):
            st.caption(f"Dashed lines on min/week charts = AHA guidelines: "
                       f"{T.AHA_VIGOROUS_MIN_WK} vigorous / {T.AHA_MODERATE_MIN_WK} moderate "
                       f"minimum, {T.AHA_GOAL_MIN_WK} goal. Recent weekly HR-zone minutes are on Training.")
            for i, (label, fig) in enumerate(br.detail_figures(q, w, end_ts)):
                if not label.startswith(_REPORT_DETAIL_KEEP):
                    continue
                st.markdown(f"### {label}")
                lib.chart(_only_ldl_goal(lib.restyle_report_fig(fig, 560 if label.startswith("Weekly") else 300)),
                          f"cardio_detail_{i}")

    # ---- medications -----------------------------------------------------
    st.markdown("## Medication history")
    meds_html = _medication_history_html()
    if meds_html:
        st.markdown(meds_html, unsafe_allow_html=True)
    statin_events = br.statin_events_df()
    if not statin_events.empty:
        with st.expander("Statin dose history"):
            st.dataframe(statin_events, use_container_width=True, hide_index=True)

    _render_bp_section()

    # ---- complete lab panels --------------------------------------------
    st.markdown("## Complete lab panels by draw date")
    st.markdown(_lab_table_html(lip), unsafe_allow_html=True)
    st.caption("Values mg/dL except Lp(a) (nmol/L). Red = above LabCorp reference. "
               "LDL calculated (NIH).")

    # ---- other risk markers ---------------------------------------------
    st.markdown("## Other cardiovascular-risk markers")
    rm = br.risk_markers_df()
    items = "".join(
        f"<li><b>{r['marker']}</b> — {r['value']} "
        f"<span class=meta>({r['date']}: {r['note']})</span></li>"
        for _, r in rm.iterrows())
    st.markdown(f"<ul class=rm>{items}</ul>", unsafe_allow_html=True)

    # ---- life-context timeline ------------------------------------------
    st.markdown("## Life-context timeline")
    st.caption("Psychosocial / occupational events — kept off the lipid chart (no direct "
               "cholesterol link) but useful context for the activity, resting-HR and sleep trends.")
    st.markdown(br.life_events_html(), unsafe_allow_html=True)

    # ---- methods --------------------------------------------------------
    if meta is not None:
        with st.expander("Methods & caveats"):
            st.markdown(
                f"- **Source coverage varies by era.** Apple full export: steps complete 2016→now; "
                f"official resting HR 2021-06+; workouts 2015+ but HR-zone minutes only 2021+; real "
                f"Apple sleep only from mid-2024 (earlier 'sleep' was a bedtime schedule, excluded). "
                f"Garmin API backfill fills 2020+ resting HR / sleep / HRV / VO₂max.\n"
                f"- **Resting HR** uses Garmin's true daily value where present, then Apple's official "
                f"RestingHeartRate, then a {int(br.RESTING_PCTL * 100)}th-percentile overnight-low "
                f"proxy of intraday samples (dense-sampling days only).\n"
                f"- **'Moderate–vigorous' / 'vigorous' minutes** are HR-zone minutes (Z3–Z5 / Z4–Z5), "
                f"%HRmax bands off a max HR of {T.HRMAX} bpm (set on the Garmin), computed only for activities "
                f"with HR streams ({meta['acts_with_streams']} activities). This is NOT raw workout "
                f"duration — long easy hikes/skis are training volume, not intensity.\n"
                f"- **Activity↔lipid alignment.** Lipid draws span 2020→2026 but quantified activity "
                f"begins {meta['data_min']}; earlier quarters have no activity overlay.\n"
                f"- Lipid/statin/risk-marker values are transcribed from LabCorp reports — verify "
                f"against originals before any clinical decision.")
