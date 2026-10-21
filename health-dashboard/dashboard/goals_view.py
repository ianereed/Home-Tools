"""Goals dashboard page — the forward-looking view of the two physician-set
cardiovascular targets and the levers being used to reach them.

Ordering is deliberate (actionable first): ① levers row (weight, exercise,
sodium, alcohol, bloodwork cadence, medications), ② LDL trajectory with the
medication regimen as lanes sharing the same time axis, ③ blood-pressure
weekly means with the required pace to goal, ④ a weekly review table putting
BP next to the levers on one time base. This is the only page that compares
a current value with a target; every value comes from dashboard.metrics and
every target from dashboard.targets. History lives on Cardiology.

PHI posture (CARDIO_PLAN.md Standing rule 2): this module is committed and
must contain no PHI literals — no goal values, medication names, doctor
names, or lab values. Everything clinical is read from the gitignored
`cardiology/clinical_data.py` at runtime via getattr with safe fallbacks, and
app.py only wires this page in when that module exists on the host. The only
numeric constants here are public references (trial-published PCSK9i response
range); guideline numbers come from dashboard.targets.
"""
import os
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

_HD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CARDIO_DIR = os.path.join(_HD_ROOT, "cardiology")
if _CARDIO_DIR not in sys.path:
    sys.path.insert(0, _CARDIO_DIR)

import build_report as br      # noqa: E402  (cardiology/build_report.py)
import clinical_data as CD     # noqa: E402  (cardiology/clinical_data.py, gitignored)
from dashboard import lib      # noqa: E402
from dashboard import metrics as M   # noqa: E402
from dashboard import targets as T   # noqa: E402

# --- Public reference constants (no personal values) -------------------------
# PCSK9-inhibitor add-on LDL response, published trial range (FOURIER-class
# outcome trials report ~59-63% mean LDL reduction on top of a statin; band
# widened to 50-65% to absorb regimen nuance). Drawn as a reference range,
# never as a personal prediction.
PCSK9I_LDL_REDUCTION = (0.50, 0.65)
# Standard lipid-panel re-check window after a therapy change (weeks).
DRAW_WINDOW_WEEKS = (8, 12)
# Regimen-lane colors (match build_report's statin cyan; teal/pink from
# lib.COLORWAY for the non-statin lanes).
_LANE_COLORS = ["#38bdf8", "#5eead4", "#f0abfc"]

# =============================================================================
# Pure helpers (unit-tested with synthetic values; no I/O, no CD access)
# =============================================================================

def _med_lanes(statin_events, medications, statin_name):
    """Build regimen-lane segments for the LDL chart's lower panel.

    Returns a list of dicts: {lane, start, end (None = ongoing), label, frac}
    where frac in (0, 1] scales the statin segment's opacity by dose. The
    statin lane comes from the dose-event history (one segment per dose era);
    every other medication with a start date gets one segment from start to
    its stop date (or ongoing).
    """
    lanes = []
    events = sorted(
        [(pd.Timestamp(d), mg) for d, mg, *_ in (statin_events or [])],
        key=lambda t: t[0])
    doses = [mg for _, mg in events if mg] or [1]
    max_dose = max(doses)
    for i, (start, mg) in enumerate(events):
        if not mg:          # a dose of 0 is a stop event, not a segment
            continue
        end = events[i + 1][0] if i + 1 < len(events) else None
        lanes.append({
            "lane": statin_name or "statin", "start": start, "end": end,
            "label": f"{int(mg)} mg", "frac": 0.3 + 0.7 * (mg / max_dose),
        })
    statin_key = (statin_name or "").lower()
    for m in medications or []:
        name = (m.get("name") or "").lower()
        if not m.get("start") or (statin_key and statin_key in name):
            continue
        display = m.get("brand") or m.get("name") or "?"
        lanes.append({
            "lane": display, "start": pd.Timestamp(m["start"]),
            "end": pd.Timestamp(m["stop"]) if m.get("stop") else None,
            "label": f'{display} {m.get("dose", "")}'.strip(), "frac": 1.0,
        })
    return lanes


def _projection(latest_ldl, medications, lo_hi=PCSK9I_LDL_REDUCTION):
    """Expected LDL range (lo, hi) on the current regimen, or None.

    Drawn only when an ACTIVE, started medication is a PCSK9 inhibitor (per
    its purpose text) — the one drug class with a large, well-published
    response range. Values derive from the latest draw at runtime; the only
    constants are the public trial percentages.
    """
    if latest_ldl is None or pd.isna(latest_ldl):
        return None
    for m in medications or []:
        purpose = (m.get("purpose") or "").lower()
        status = (m.get("status") or "").lower()
        if "pcsk9" in purpose and m.get("start") and status.startswith("active"):
            return (latest_ldl * (1 - lo_hi[1]), latest_ldl * (1 - lo_hi[0]))
    return None


def _next_injection(medications, today):
    """(display_name, next_date) for the soonest upcoming dose of any active
    every-N-weeks injectable, or None. Computed from the start date and the
    stated frequency ('every 2 weeks' / 'every 3 weeks' ...)."""
    best = None
    for m in medications or []:
        freq = (m.get("frequency") or "").lower()
        status = (m.get("status") or "").lower()
        if not m.get("start") or not status.startswith("active"):
            continue
        if "week" not in freq or "every" not in freq:
            continue
        n_weeks = 1
        for tok in freq.split():
            if tok.isdigit():
                n_weeks = int(tok)
                break
        period = 7 * n_weeks
        start = pd.Timestamp(m["start"]).normalize()
        elapsed = (today.normalize() - start).days
        if elapsed < 0:
            nxt = start
        else:
            nxt = start + pd.Timedelta(days=period * (elapsed // period + 1))
        if best is None or nxt < best[1]:
            best = (m.get("brand") or m.get("name") or "?", nxt)
    return best


def _therapy_change_date(statin_events, medications):
    """Most recent therapy change: any dose step, med start, or med stop."""
    dates = [pd.Timestamp(d) for d, *_ in (statin_events or [])]
    for m in medications or []:
        for key in ("start", "stop"):
            if m.get(key):
                dates.append(pd.Timestamp(m[key]))
    return max(dates) if dates else None


def _pace_per_week(current, goal, today, deadline):
    """Required change per week (negative = must fall) to reach `goal` from
    `current` by `deadline`. None when there's no deadline, it has passed,
    or the goal is already met."""
    if deadline is None or current is None or pd.isna(current):
        return None
    weeks = (deadline - today).days / 7
    if weeks <= 0 or current <= goal:
        return None
    return (goal - current) / weeks


# =============================================================================
# Levers row (section ① — actionable data first)
# =============================================================================

def _weight_tile(goals, today):
    w = M.weight_now(today)
    if w is None:
        return lib.card("Weight", "—", "no weigh-ins yet", lib.MUTED)
    band = T.weight_band_lb(goals)
    if band:
        lo, hi = band
        if w["lb"] <= hi:
            color, gap = lib.GOOD, "in the goal band"
        else:
            color, gap = lib.WARN, f'<b>{w["lb"] - hi:.0f}–{w["lb"] - lo:.0f} lb to go</b>'
        tgt = f"band {lo:.0f}–{hi:.0f} lb · {gap}"
    else:
        color, tgt = lib.MUTED, "no goal band configured"
    basis = (f'{w["basis"]} · latest {w["latest_lb"]:.1f}, {w["age_days"]} d ago' if not w["stale"]
             else f'last weigh-in {w["age_days"]} d ago')
    spark = M.weight_daily(M.since(30, today))["lb"].tolist()
    return lib.card("Weight", f'{w["lb"]:.1f} <small>lb</small>', f"{tgt}<br>{basis}",
                    color, lib.spark_svg(spark, lib.SERIES["weight"]))


def _exercise_tile(today):
    ex = M.exercise_summary(today)
    if ex is None:
        return lib.card("Exercise", "—", "no heart-rate-stream activities yet", lib.MUTED)
    return lib.card(
        "Exercise", f'{ex["modvig"]:.0f} <small>Z3–5 min/wk</small>',
        f"AHA ≥{T.AHA_MODERATE_MIN_WK} moderate (or ≥{T.AHA_VIGOROUS_MIN_WK} vigorous) · "
        f'4-wk avg · vigorous {ex["vig"]:.0f}',
        lib.GOOD if ex["meets_aha"] else lib.WARN, lib.spark_svg(ex["spark"], lib.ZONE_COLORS["Z3"]))


def _sodium_tile(today):
    na = M.sodium_week(today)
    if na is None:
        return lib.card("Sodium", "—",
                        f"no days logged this week · DASH ≤{T.SODIUM_MG_CEILING:,} mg", lib.MUTED)
    cov = na["days"]
    color = lib.MUTED if cov < 4 else lib.GOOD if na["mean"] <= T.SODIUM_MG_CEILING else lib.BAD
    return lib.card(
        "Sodium", f'{na["mean"]:,.0f} <small>mg/day</small>',
        f"7-day avg, net of ride sweat · DASH ≤{T.SODIUM_MG_CEILING:,} · ideal "
        f"{T.SODIUM_MG_IDEAL:,} · <b>logged {cov}/7 days</b>"
        + ("" if cov >= 4 else " — trend unlocks at 4/7"),
        color, lib.spark_svg(na["series"], lib.SERIES["sodium"]))


def _alcohol_tile(today):
    a = M.alcohol_summary(today)
    if a is None:
        return lib.card("Alcohol", "—",
                        "no drinks logged · tracked via Garmin Lifestyle Logging", lib.MUTED)
    over = a["this_week"] > T.ALCOHOL_DRINKS_WK or a["prior4_avg"] > T.ALCOHOL_DRINKS_WK
    return lib.card(
        "Alcohol", f'{a["this_week"]:.0f} <small>drinks this wk</small>',
        f'≤{T.ALCOHOL_DRINKS_WK}/wk guideline · prior 4-wk avg <b>{a["prior4_avg"]:.0f}</b> · '
        "for BP, lower beats “under guideline”",
        lib.WARN if over else lib.GOOD, lib.spark_svg(a["weekly"].tolist()[-8:], lib.SERIES["alcohol"]))


def _bloodwork_tile(lip, today):
    if lip.empty:
        return lib.card("Bloodwork", "—", "no lab draws recorded", lib.MUTED)
    last_draw = lip["date"].max()
    days_since = (today.normalize() - last_draw.normalize()).days
    change = _therapy_change_date(getattr(CD, "STATIN_EVENTS", []),
                                  getattr(CD, "MEDICATIONS", []))
    tgt, color = f"last draw {last_draw:%Y-%m-%d}", lib.MUTED
    if change is not None:
        w0 = change + pd.Timedelta(weeks=DRAW_WINDOW_WEEKS[0])
        w1 = change + pd.Timedelta(weeks=DRAW_WINDOW_WEEKS[1])
        if last_draw >= change:
            tgt, color = f"post-change draw done ({last_draw:%b %d})", lib.GOOD
        elif today >= w0 - pd.Timedelta(days=14):
            tgt = (f"<b>next draw due {w0:%b %d} – {w1:%b %d}</b> — first panel "
                   "after the regimen change")
            color = lib.WARN
        else:
            tgt = f"next draw due {w0:%b %d} – {w1:%b %d} (post-change re-check)"
            color = lib.GOOD
    return lib.card("Bloodwork", f"{days_since} <small>d since draw</small>", tgt, color)


def _meds_tile(meds, today):
    active = [m for m in meds if (m.get("status") or "").lower().startswith("active")]
    if not active:
        return lib.card("Medications", "—", "no active medications recorded", lib.MUTED)
    lines = "<br>".join(
        f'{m.get("brand") or m.get("name")} {m.get("dose", "")} '
        f'<small>{m.get("frequency", "")}</small>' for m in active)
    change = _therapy_change_date(getattr(CD, "STATIN_EVENTS", []), meds)
    bits = []
    if change is not None:
        bits.append(f"regimen day {max((today.normalize() - change).days, 0) + 1}")
    nxt = _next_injection(meds, today)
    if nxt:
        bits.append(f"next {nxt[0]} injection <b>{nxt[1]:%b %d}</b>")
    return lib.card("Medications", f'<span style="font-size:14px">{lines}</span>',
                    " · ".join(bits) or "current regimen", lib.GOOD)


def _medications_html() -> str:
    """One card per current-regimen medication. Discontinued therapies stay in
    MEDICATIONS for the regimen lanes and projection but get no card here;
    prescribed-not-yet-started meds do. Empty/absent MEDICATIONS renders nothing."""
    meds = [m for m in getattr(CD, "MEDICATIONS", [])
            if not m.get("stop")
            and not (m.get("status") or "").lower().startswith("discontinued")]
    if not meds:
        return ""
    cards = []
    for m in meds:
        started = m.get("start")
        status = m.get("status", "")
        started_line = f"Started {started} ({status})" if started else f"<b>{status}</b>"
        sub = (f'{m.get("form", "")} · {m.get("frequency", "")}<br>{started_line}<br>'
               f'{m.get("prescriber", "")} · {m.get("purpose", "")}')
        label = f'{m.get("name", "")} ({m.get("brand", "")})' if m.get("brand") else m.get("name", "")
        cards.append(lib.card(label, m.get("dose", "—"), sub))
    return f'<div class="cards c3">{"".join(cards)}</div>'


def _render_levers(goals, lip, today):
    st.markdown("## Levers — this week")
    st.caption("The six trackers of the treatment plan, current value vs target. "
               "Gray dot = not enough data yet. History: weight on Nutrition → Body, "
               "exercise on Training, sodium and alcohol on Nutrition → History, labs on Cardiology.")
    meds = getattr(CD, "MEDICATIONS", [])
    lib.cards([
        _weight_tile(goals, today),
        _exercise_tile(today),
        _sodium_tile(today),
        _alcohol_tile(today),
        _bloodwork_tile(lip, today),
        _meds_tile(meds, today),
    ], cols3=True)
    cur = _medications_html()
    if cur:
        with st.expander("Current medications"):
            st.markdown(cur, unsafe_allow_html=True)


# =============================================================================
# Goal 1 — LDL trajectory × regimen (section ②)
# =============================================================================

def _time_marker(fig, x, text, color, dash, opacity=0.5, xanchor="left"):
    """Full-height vertical time marker (today / deadline).

    Not add_vline: with an annotation on a date x-axis, plotly's axis-spanning-
    shape machinery averages the shape's Timestamps (shapeannotation._mean),
    which raises on some plotly/pandas combinations — crashed live on the mini
    while the laptop's versions tolerated it. A plain shape plus a paper-ref
    annotation renders identically and avoids that code path on every version.
    """
    fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1, xref="x", yref="paper",
                  line=dict(color=color, dash=dash, width=1), opacity=opacity)
    fig.add_annotation(x=x, y=1.0, xref="x", yref="paper", yanchor="bottom",
                       text=text, showarrow=False, xanchor=xanchor,
                       font=dict(size=10, color=color))


def _ldl_figure(lip, goals, today):
    deadline = None
    if goals and goals.get("deadline"):
        deadline = pd.Timestamp(goals["deadline"])
    x_end = deadline if deadline is not None else today + pd.Timedelta(days=120)

    lanes = _med_lanes(getattr(CD, "STATIN_EVENTS", []),
                       getattr(CD, "MEDICATIONS", []),
                       getattr(CD, "STATIN", None))
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.74, 0.26], vertical_spacing=0.07)

    # -- draws (top row) --
    fig.add_trace(go.Scatter(
        x=lip["date"], y=lip["ldl"], name="LDL-C",
        mode="lines+markers+text", text=[f"{v:.0f}" if pd.notna(v) else "" for v in lip["ldl"]],
        textposition="top center", textfont=dict(size=10, color=lib.MUTED),
        line=dict(color=lib.SERIES["ldl"], width=2), marker=dict(size=8)), row=1, col=1)
    apo = lip[lip["apob"].notna()]
    if not apo.empty:
        fig.add_trace(go.Scatter(
            x=apo["date"], y=apo["apob"], name="ApoB",
            mode="lines+markers", line=dict(color=lib.SERIES["apob"], width=1.5, dash="dot"),
            marker=dict(size=6)), row=1, col=1)
    br.add_ldl_goal_lines(fig, row=1, col=1)

    # -- today + deadline verticals --
    _time_marker(fig, today, "today", lib.MUTED, "dot", opacity=0.5,
                 xanchor="right")
    if deadline is not None:
        # right-anchored: the deadline sits near the plot's right edge, so the
        # label must extend left of the line or it clips off-plot
        _time_marker(fig, deadline, f"goal · {deadline:%b %Y}", lib.GOOD, "dash",
                     opacity=0.8, xanchor="right")

    # -- expected-response band (public trial range, never a promise) --
    latest_ldl = lip["ldl"].dropna().iloc[-1] if lip["ldl"].notna().any() else None
    proj = _projection(latest_ldl, getattr(CD, "MEDICATIONS", []))
    if proj:
        fig.add_shape(type="rect", x0=today, x1=x_end, y0=proj[0], y1=proj[1],
                      fillcolor=lib.ACCENT, opacity=0.14, line_width=0, row=1, col=1)
        # Short on-chart label; the full published-range explanation lives in
        # the caption below the chart (the band is narrow — deadline minus
        # today — so long text would clip at the plot edge).
        fig.add_annotation(
            x=today, y=proj[1], xanchor="left",
            text="expected range", showarrow=False, yshift=10,
            font=dict(size=10, color=lib.ACCENT), row=1, col=1)

    # -- regimen lanes (bottom row, shared x-axis) --
    lane_order = []
    for seg in lanes:
        if seg["lane"] not in lane_order:
            lane_order.append(seg["lane"])
        solid_end = seg["end"] if seg["end"] is not None else today
        lane_color = _LANE_COLORS[lane_order.index(seg["lane"]) % len(_LANE_COLORS)]
        color = lib.rgba(lane_color, seg["frac"] * 0.85)
        fig.add_trace(go.Scatter(
            x=[seg["start"], solid_end], y=[seg["lane"]] * 2, mode="lines",
            line=dict(width=12, color=color), showlegend=False,
            hovertemplate=f'{seg["label"]}<br>%{{x|%Y-%m-%d}}<extra></extra>'),
            row=2, col=1)
        if seg["end"] is None:      # ongoing → dotted continuation to the deadline
            fig.add_trace(go.Scatter(
                x=[solid_end, x_end], y=[seg["lane"]] * 2, mode="lines",
                line=dict(width=2, color=color, dash="dot"), showlegend=False,
                hoverinfo="skip"), row=2, col=1)
        span_days = ((seg["end"] or x_end) - seg["start"]).days
        if span_days > 90:          # label only segments wide enough to carry text
            fig.add_annotation(
                x=seg["start"] + pd.Timedelta(days=span_days / 2), y=seg["lane"],
                text=seg["label"], showarrow=False,
                # sit labels just above the lane so ongoing (dotted) segments
                # don't get text drawn over the dashes
                yshift=11 if seg["end"] is None else 0,
                font=dict(size=9, color=lib.INK), row=2, col=1)

    x_start = lip["date"].min() if not lip.empty else today - pd.Timedelta(days=365)
    if lanes:
        x_start = min(x_start, min(s["start"] for s in lanes))
    fig.update_xaxes(range=[x_start - pd.Timedelta(days=45),
                            x_end + pd.Timedelta(days=30)])
    fig.update_yaxes(title_text="mg/dL", row=1, col=1)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=10), autorange="reversed",
                     row=2, col=1)
    return lib.apply_theme(fig, 500, legend=True)


def _render_ldl_section(lip, goals, today):
    st.markdown("## Goal 1 — LDL")
    if lip.empty:
        st.caption("No lipid draws recorded yet.")
        return
    latest = lip.iloc[-1]
    goal_val = T.ldl_goal(goals)
    deadline = pd.Timestamp(goals["deadline"]) if goals and goals.get("deadline") else None

    tiles = [lib.card("Latest LDL", f'{latest["ldl"]:.0f} <small>mg/dL</small>',
                      f'drawn {latest["date"]:%Y-%m-%d}')]
    if goal_val is not None and pd.notna(latest["ldl"]):
        tiles.append(lib.card("To goal", f'{latest["ldl"] - goal_val:+.0f} <small>mg/dL</small>',
                              f"goal &lt;{goal_val} mg/dL",
                              lib.GOOD if latest["ldl"] < goal_val else lib.WARN))
    change = _therapy_change_date(getattr(CD, "STATIN_EVENTS", []),
                                  getattr(CD, "MEDICATIONS", []))
    if change is not None:
        w0 = change + pd.Timedelta(weeks=DRAW_WINDOW_WEEKS[0])
        w1 = change + pd.Timedelta(weeks=DRAW_WINDOW_WEEKS[1])
        tiles.append(lib.card("Next draw window", f"{w0:%b %d} – {w1:%b %d}",
                              f"{DRAW_WINDOW_WEEKS[0]}–{DRAW_WINDOW_WEEKS[1]} weeks after the "
                              f"regimen change on {change:%Y-%m-%d}"))
    if deadline is not None:
        tiles.append(lib.card("Deadline", f"{(deadline - today).days} <small>days</small>",
                              f"{deadline:%Y-%m-%d}"))
    lib.cards(tiles)

    lib.chart(_ldl_figure(lip, goals, today), "goals_ldl")
    st.caption("Rose = LDL-C draws · amber = ApoB · lanes = medication regimen on the "
               "same time axis (opacity tracks dose; dotted = ongoing). The blue band "
               "is the published PCSK9-inhibitor response range applied to the latest "
               "draw — a reference, not a prediction; it ignores the concurrent statin "
               "step-down and any bridge therapy between draws. Exercise stays off "
               "this chart deliberately: medication owns this goal, lifestyle owns "
               "Goal 2.")


# =============================================================================
# Goal 2 — blood pressure pace (section ③)
# =============================================================================

def _bp_figure(weekly, goals, mean14, today):
    sys_goal, dia_goal = T.bp_goal(goals)
    deadline = pd.Timestamp(goals["deadline"]) if goals and goals.get("deadline") else None
    x_end = today + pd.Timedelta(days=60)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.5, 0.5], vertical_spacing=0.12,
                        subplot_titles=("Systolic", "Diastolic"))
    series = [("sys", lib.SERIES["sys"], sys_goal, 1), ("dia", lib.SERIES["dia"], dia_goal, 2)]
    for col_name, color, goal_val, row in series:
        fig.add_trace(go.Scatter(
            x=weekly["week"], y=weekly[col_name], name=col_name.upper(),
            mode="lines+markers+text",
            text=[f"{v:.0f}" if pd.notna(v) else "" for v in weekly[col_name]],
            textposition="top center", textfont=dict(size=9, color=lib.MUTED),
            connectgaps=False, line=dict(color=color, width=2),
            marker=dict(size=7), showlegend=False,
            customdata=weekly["n"],
            hovertemplate="wk of %{x|%b %d} · %{y:.0f} (n=%{customdata})<extra></extra>"),
            row=row, col=1)
        if goal_val is not None:
            fig.add_hline(y=goal_val, line_dash="dash", line_color=lib.GOOD,
                          opacity=0.8, row=row, col=1,
                          annotation_text=f"goal <{goal_val}",
                          annotation_position="bottom right",
                          annotation_font_size=10, annotation_font_color=lib.GOOD)
        cur = mean14.get(col_name)
        pace = _pace_per_week(cur, goal_val, today, deadline) if goal_val else None
        if pace is not None:
            days_vis = (x_end - today).days
            fig.add_trace(go.Scatter(
                x=[today, x_end], y=[cur, cur + pace * days_vis / 7],
                mode="lines", line=dict(color=lib.INK, width=1.5, dash="dash"),
                opacity=0.55, showlegend=False,
                hovertemplate=f"pace {pace:+.2f} mmHg/wk<extra></extra>"),
                row=row, col=1)

    if not weekly.empty:
        fig.update_xaxes(range=[weekly["week"].min() - pd.Timedelta(days=3), x_end])
    fig.update_annotations(font_size=11)
    return lib.apply_theme(fig, 380)


def _render_bp_section(goals, today):
    st.markdown("## Goal 2 — Blood pressure")
    bp = M.bp_readings()
    win = M.bp_window(14)
    if win is None:
        st.caption("No blood-pressure readings yet — they sync from Garmin Connect.")
        return
    mean14 = {"sys": win["sys"], "dia": win["dia"]}
    sys_goal, dia_goal = T.bp_goal(goals)
    deadline = pd.Timestamp(goals["deadline"]) if goals and goals.get("deadline") else None

    weekly = M.bp_weekly(bp)
    gap_weeks = int(weekly["n"].isna().sum()) if not weekly.empty else 0

    tiles = [lib.card("Blood pressure", f'{win["sys"]:.0f}/{win["dia"]:.0f}',
                      f'{win["category"]} · {win["basis"]} ({win["n"]} readings)', win["color"]),
             lib.card("To goal", f'{max(win["sys"] - sys_goal, 0):.0f} / {max(win["dia"] - dia_goal, 0):.0f}',
                      f"mmHg above &lt;{sys_goal}/{dia_goal}",
                      lib.GOOD if win["sys"] < sys_goal and win["dia"] < dia_goal else lib.WARN)]
    sp = _pace_per_week(win["sys"], sys_goal, today, deadline)
    dp = _pace_per_week(win["dia"], dia_goal, today, deadline)
    if sp is not None or dp is not None:
        tiles.append(lib.card("Pace needed", f"{sp or 0:+.2f} / {dp or 0:+.2f}",
                              "mmHg per week to reach goal by the deadline — arithmetic, not a forecast"))
    tiles.append(lib.card("Cuff readings", f"{len(bp)}",
                          f"all time · {gap_weeks} week(s) without a reading"))
    lib.cards(tiles)

    lib.chart(_bp_figure(weekly, goals, mean14, today), "goals_bp")
    st.caption("Points = weekly means of cuff readings (hover for n; gaps = weeks "
               "with no readings — a gap breaks the trend, keep the cuff habit). "
               "Dashed white line = required pace to the goal by the deadline. "
               "Every reading and the morning/evening analysis are on the Cardiology page.")


# =============================================================================
# Weekly review (section ④ — levers × BP on one time base)
# =============================================================================

def _weekly_review(today, n_weeks=8):
    """One row per Monday-week: BP mean and readings, weight, Z3–5 minutes,
    drinks and logged sodium, all from the same metric functions as the tiles."""
    monday = M.this_monday(today)
    weeks = [monday - pd.Timedelta(weeks=i) for i in range(n_weeks)]
    start = min(weeks).date().isoformat()

    bp = M.bp_readings()
    bp = bp.assign(week=M.week_start(bp["timestamp"])) if not bp.empty else bp
    wt = M.weight_daily(start)
    wt = wt.assign(week=M.week_start(wt["date"])) if not wt.empty else wt
    zw = M.zone_minutes_weekly(start).set_index("week")
    al = M.alcohol_weekly()
    na = M.nutrition_daily(start)
    na = na.assign(week=M.week_start(na["date"])) if not na.empty else na

    rows = []
    for wk in weeks:
        row = {"Week of": f"{wk:%b %d ’%y}" + (" (current)" if wk == monday else "")}
        g = bp[bp["week"] == wk] if not bp.empty else pd.DataFrame()
        row["BP mean"] = (f'{g["systolic"].mean():.0f} / {g["diastolic"].mean():.0f}'
                          if not g.empty else "—")
        row["Cuff readings"] = f"{len(g)}"
        g = wt[wt["week"] == wk] if not wt.empty else pd.DataFrame()
        row["Weight (lb)"] = f'{g["lb"].mean():.1f}' if not g.empty else "—"
        row["Z3–5 min"] = f'{zw.loc[wk, "modvig"]:.0f}' if wk in zw.index else "—"
        row["Drinks"] = f"{al.get(wk, 0):.0f}" if not al.empty else "—"
        g = na[(na["week"] == wk) & na["sodium_mg"].notna()] if not na.empty else pd.DataFrame()
        row["Sodium (net mg, logged days)"] = (f'{g["sodium_mg"].mean():,.0f} · {len(g)} d'
                                               if not g.empty else "—")
        rows.append(row)
    return pd.DataFrame(rows)


def _render_weekly_review(today):
    st.markdown("## Weekly review — levers × BP")
    st.caption("One row per week, newest first. A “—” is this week's to-do, not "
               "missing history: the row fills in as habits and devices come online. "
               "Z3–5 min is the same HR-zone count as the Exercise tile.")
    st.dataframe(_weekly_review(today), use_container_width=True, hide_index=True)


# =============================================================================
# Page
# =============================================================================

def render_goals():
    today = pd.Timestamp.now().normalize()
    goals = getattr(CD, "CARDIO_GOALS", None)
    lip = br.lipids_df()

    intro_bits = []
    if T.ldl_goal(goals):
        intro_bits.append(f"LDL &lt; {T.ldl_goal(goals)} mg/dL")
    if (goals or {}).get("bp"):
        intro_bits.append("BP &lt; {}/{}".format(*T.bp_goal(goals)))
    if goals and goals.get("deadline"):
        dl = pd.Timestamp(goals["deadline"])
        intro_bits.append(f"by {dl:%b %Y} · <b>{(dl - today).days} days</b>")
    if goals and goals.get("set_by"):
        intro_bits.append(f'set by {goals["set_by"]}')
    lib.page_header("Goals", "Where each target stands now, and the levers that move it. "
                    + (" · ".join(intro_bits) if intro_bits else "No physician goals recorded yet."))

    _render_levers(goals, lip, today)
    _render_ldl_section(lip, goals, today)
    _render_bp_section(goals, today)
    _render_weekly_review(today)

    st.caption("Honesty rules: the LDL band is a published trial range, never a "
               "personal prediction; pace lines are arithmetic (gap ÷ weeks left), "
               "not forecasts; coverage counts stop absent data from reading as "
               "good behavior. History lives on Cardiology, Training and Nutrition.")
