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
guideline numbers come from dashboard.targets.
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
# Standard lipid-panel re-check window after a therapy change (weeks).
DRAW_WINDOW_WEEKS = (8, 12)
# Regimen-lane colors (match build_report's statin cyan; teal/pink from
# lib.COLORWAY for the non-statin lanes).
# One color family per medication lane: (dark, light). Successive dose eras in
# a lane alternate between the two for contrast.
LANE_PALETTES = [("#1d4ed8", "#60a5fa"), ("#0f766e", "#5eead4"), ("#7e22ce", "#d8b4fe"),
                 ("#b45309", "#fcd34d"), ("#be123c", "#fda4af"), ("#4d7c0f", "#bef264")]

# =============================================================================
# Pure helpers (unit-tested with synthetic values; no I/O, no CD access)
# =============================================================================

def _med_lanes(statin_events, medications, statin_name):
    """Build regimen-lane segments for the LDL chart's lower panel.

    Returns a list of dicts: {lane, start, end (None = ongoing), label}. The
    statin lane comes from the dose-event history (one segment per dose era);
    every other medication with a start date gets one segment from start to
    its stop date (or ongoing).
    """
    lanes = []
    events = sorted(
        [(pd.Timestamp(d), mg) for d, mg, *_ in (statin_events or [])],
        key=lambda t: t[0])
    for i, (start, mg) in enumerate(events):
        if not mg:          # a dose of 0 is a stop event, not a segment
            continue
        end = events[i + 1][0] if i + 1 < len(events) else None
        lanes.append({
            "lane": statin_name or "statin", "start": start, "end": end,
            "label": f"{int(mg)} mg",
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
            "label": f'{display} {m.get("dose", "")}'.strip(),
        })
    return lanes


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
    wg = M.weight_goals()
    if wg:
        def to_go(v, g):
            return "reached" if v <= g["lb"] else f"{v - g['lb']:.1f} lb to go"
        # status follows the mean (the trend) against goal 1
        color = lib.GOOD if w["lb"] <= wg[0]["lb"] else lib.WARN
        tgt = "<br>".join(
            f'{g["label"].lower()} ≤{g["lb"]:.1f} ({g["pct"] * 100:.0f}% fat): '
            + (f'<b>mean {to_go(w["lb"], g)}</b> · low {to_go(w["low_lb"], g)}' if i == 0
               else f'mean {to_go(w["lb"], g)}')
            for i, g in enumerate(wg))
    else:
        color, tgt = lib.MUTED, "no DEXA scan on file to set the goal"
    spark = M.weight_daily(M.since(30, today))["lb"].tolist()
    return lib.card("Weight", lib.weight_value(w), f"{tgt}<br>{lib.weight_basis(w)}",
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

def _time_marker(fig, x, text, color, dash, opacity=0.5, xanchor="left", yshift=0):
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
                       text=text, showarrow=False, xanchor=xanchor, yshift=yshift,
                       font=dict(size=10, color=color))


def draw_med_lanes(fig, lanes, row, today, ongoing_to=None):
    """Draw regimen lanes (from _med_lanes) in `row`: one color family per
    medication, alternating dark/light across its dose eras, dose labels inside
    wide-enough segments, lane names on the right. An ongoing segment is solid
    to today, then dotted to `ongoing_to` when given."""
    # a medication keeps its color on every chart: palette by its place in the
    # record (statin first), not by which lanes this chart happens to show
    statin = getattr(CD, "STATIN", None) or "statin"
    order = [statin] + [m.get("brand") or m.get("name") for m in getattr(CD, "MEDICATIONS", [])
                        if statin.lower() not in (m.get("name") or "").lower()]
    order += [seg["lane"] for seg in lanes if seg["lane"] not in order]
    order = list(dict.fromkeys(order))
    era = {}
    for seg in lanes:
        dark, light = LANE_PALETTES[order.index(seg["lane"]) % len(LANE_PALETTES)]
        n = era.get(seg["lane"], 0)
        era[seg["lane"]] = n + 1
        color = dark if n % 2 == 0 else light
        end = seg["end"] if seg["end"] is not None else today
        fig.add_trace(go.Scatter(
            x=[seg["start"], end], y=[seg["lane"]] * 2, mode="lines",
            line=dict(width=13, color=color), showlegend=False,
            hovertemplate=f'{seg["lane"]} · {seg["label"]}<br>%{{x|%Y-%m-%d}}<extra></extra>'),
            row=row, col=1)
        if seg["end"] is None and ongoing_to is not None and ongoing_to > end:
            fig.add_trace(go.Scatter(
                x=[end, ongoing_to], y=[seg["lane"]] * 2, mode="lines",
                line=dict(width=2, color=color, dash="dot"), showlegend=False, hoverinfo="skip"),
                row=row, col=1)
        if (end - seg["start"]).days > 110:
            text = seg["label"].replace(seg["lane"], "").strip() or seg["label"]
            fig.add_annotation(x=seg["start"] + (end - seg["start"]) / 2, y=seg["lane"], text=text,
                               showarrow=False, font=dict(size=9, color="#ffffff" if color == dark else "#0e1117"),
                               row=row, col=1)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=11), autorange="reversed", side="right", automargin=True,
                     row=row, col=1)


def add_panel_tag(fig, row, text):
    """A muted all-caps tag naming a subplot panel (same style as the BP
    charts' "SYSTOLIC — mmHg"), just above the panel's top-left corner."""
    fig.add_annotation(text=text, x=0, xref="x domain", xanchor="left", y=1, yref="y domain",
                       yanchor="bottom", yshift=3, showarrow=False,
                       font=dict(size=11, color=lib.MUTED), row=row, col=1)


def add_ldl_goal_line(fig, goals, row=1):
    """The single LDL goal line (the stretch goal, < 50)."""
    goal = T.ldl_goal(goals)
    if goal is not None:
        fig.add_hline(y=goal, line_dash="dash", line_color=lib.GOOD, opacity=0.85, row=row, col=1,
                      annotation_text=f"LDL goal <{goal}", annotation_position="top left",
                      annotation_font_size=10, annotation_font_color=lib.GOOD)


def add_zero_line(fig, row=1):
    """A solid baseline at 0 mg/dL: the floor of the lipid panel, above the lanes."""
    fig.add_hline(y=0, line_color=lib.INK, line_width=1.5, opacity=0.7, row=row, col=1)


def _ldl_figure(lip, goals, today):
    deadline = None
    if goals and goals.get("deadline"):
        deadline = pd.Timestamp(goals["deadline"])
    x_end = deadline if deadline is not None else today + pd.Timedelta(days=120)

    lanes = _med_lanes(getattr(CD, "STATIN_EVENTS", []),
                       getattr(CD, "MEDICATIONS", []),
                       getattr(CD, "STATIN", None))
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.72, 0.28], vertical_spacing=0.09)

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
    add_ldl_goal_line(fig, goals)
    add_zero_line(fig)

    # -- today + deadline verticals --
    _time_marker(fig, today, "today", lib.MUTED, "dot", opacity=0.5,
                 xanchor="right")
    if deadline is not None:
        # right-anchored: the deadline sits near the plot's right edge, so the
        # label must extend left of the line or it clips off-plot
        _time_marker(fig, deadline, f"goal · {deadline:%b %Y}", lib.GOOD, "dash",
                     opacity=0.8, xanchor="right", yshift=13)

    # -- regimen lanes (bottom row, shared x-axis) --
    draw_med_lanes(fig, lanes, 2, today, ongoing_to=x_end)

    x_start = lip["date"].min() if not lip.empty else today - pd.Timedelta(days=365)
    if lanes:
        x_start = min(x_start, min(s["start"] for s in lanes))
    fig.update_xaxes(range=[x_start - pd.Timedelta(days=45),
                            x_end + pd.Timedelta(days=30)])
    top = max(lip["ldl"].max() if not lip.empty else 100,
              lip["apob"].max() if lip["apob"].notna().any() else 0)
    fig.update_yaxes(range=[0, top * 1.15], row=1, col=1)
    add_panel_tag(fig, 1, "CHOLESTEROL — LDL-C & ApoB, mg/dL")
    add_panel_tag(fig, 2, "MEDICATIONS")
    fig = lib.apply_theme(fig, 520, legend=True)
    # room for the today/deadline labels above and the lane names on the right
    fig.update_layout(margin=dict(l=8, r=8, t=42, b=8))
    return fig


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
    st.caption("Rose = LDL-C draws · amber = ApoB · dashed green = the LDL goal (<50) · solid "
               "line = 0 mg/dL. Lanes = medication regimen on the same time axis: one color per "
               "medication, alternating shades at each dose change; dotted = ongoing to the "
               "deadline. Exercise stays off this chart deliberately: medication owns this goal, "
               "lifestyle owns Goal 2.")


# =============================================================================
# Weekly tracker charts (BP, weight and the levers share one time axis)
# =============================================================================

# Every weekly tracker starts here so the charts line up when read top to
# bottom (the LDL chart keeps its own multi-year axis: draws are rare).
TRACKER_START = pd.Timestamp("2026-06-01")


def _tracker_range(today):
    return [TRACKER_START - pd.Timedelta(days=3), today + pd.Timedelta(days=60)]


def _tracker_figure(week, y, *, name, color, fmt, today, unit="", n=None, n_label="",
                    goal=None, goal_text=None, goal_color=None, ref=None, ref_text=None,
                    second=None, second_name=None, second_color=None, second_goal=None,
                    second_goal_text=None, lines=True, height=240):
    """A BP-style weekly tracker: labeled weekly points (gaps break the line,
    or points only with lines=False), a dashed goal line (green unless
    goal_color is given), an optional muted reference line, and an optional
    companion series in its own color with its own goal line."""
    fig = go.Figure()
    hover = f"wk of %{{x|%b %d}} · %{{y:{fmt[2:-1]}}}{unit}"
    if n is not None:
        hover += f" (%{{customdata}} {n_label})"
    fig.add_trace(go.Scatter(
        x=week, y=y, name=name, mode=("lines+" if lines else "") + "markers+text",
        text=[fmt.format(v) if pd.notna(v) else "" for v in y],
        textposition="top center", textfont=dict(size=9, color=lib.MUTED),
        connectgaps=False, line=dict(color=color, width=2), marker=dict(size=7),
        customdata=n, hovertemplate=hover + "<extra></extra>"))
    if second is not None:
        c2 = second_color or color
        fig.add_trace(go.Scatter(
            x=week, y=second, name=second_name, mode="lines+markers", connectgaps=False,
            line=dict(color=c2, width=1.5, dash=None if second_color else "dot"), marker=dict(size=5),
            hovertemplate=f"wk of %{{x|%b %d}} · %{{y:{fmt[2:-1]}}}{unit}<extra></extra>"))
        if second_goal is not None:
            fig.add_hline(y=second_goal, line_dash="dash", line_color=c2, opacity=0.6,
                          annotation_text=second_goal_text, annotation_position="top right",
                          annotation_font_size=10, annotation_font_color=c2)
    if goal is not None:
        gc = goal_color or lib.GOOD
        fig.add_hline(y=goal, line_dash="dash", line_color=gc, opacity=0.6 if goal_color else 0.8,
                      annotation_text=goal_text, annotation_position="bottom right",
                      annotation_font_size=10, annotation_font_color=gc)
    if ref is not None:
        fig.add_hline(y=ref, line_dash="dot", line_color=lib.MUTED, opacity=0.7,
                      annotation_text=ref_text, annotation_position="top right",
                      annotation_font_size=10, annotation_font_color=lib.MUTED)
    fig.update_xaxes(range=_tracker_range(today))
    return lib.apply_theme(fig, height, legend=second is not None)


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

    fig.update_xaxes(range=_tracker_range(today))
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
    weekly = weekly[weekly["week"] >= TRACKER_START - pd.Timedelta(days=7)]
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
# Goal 3 — weight pace (section ③b, same shape as the BP chart)
# =============================================================================

def _weight_figure(weekly, wgoals, today):
    """Each week's 7-day average (labeled) against the body-fat goal weights."""
    color = lib.SERIES["weight"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=weekly["week"], y=weekly["lb"], name="7-day average",
        mode="lines+markers+text",
        text=[f"{v:.1f}" if pd.notna(v) else "" for v in weekly["lb"]],
        textposition="top center", textfont=dict(size=9, color=lib.MUTED),
        connectgaps=False, line=dict(color=color, width=2), marker=dict(size=7),
        customdata=weekly["n"],
        hovertemplate="wk of %{x|%b %d} · %{y:.1f} lb (%{customdata} weigh-in days)<extra></extra>"))
    for g, dash in zip(wgoals, ("dash", "dot")):
        fig.add_hline(y=g["lb"], line_dash=dash, line_color=lib.GOOD, opacity=0.8,
                      annotation_text=f'{g["label"].lower()} · {g["pct"] * 100:.0f}% fat · {g["lb"]:.1f}',
                      annotation_position="bottom right",
                      annotation_font_size=10, annotation_font_color=lib.GOOD)
    fig.update_xaxes(range=_tracker_range(today))
    if weekly["lb"].notna().any():
        lo = min([weekly["lb"].min()] + [g["lb"] for g in wgoals])
        fig.update_yaxes(range=[lo - 3, weekly["lb"].max() + 3])
    return lib.apply_theme(fig, 280)


def _render_weight_section(goals, today):
    st.markdown("## Goal 3 — Weight")
    weekly = M.weight_weekly(TRACKER_START.date().isoformat())
    now_w = M.weight_now(today)
    if now_w is None or weekly.empty:
        st.caption("No weigh-ins yet — they sync from the Garmin scale.")
        return
    wgoals = M.weight_goals()

    tiles = []
    for g in wgoals:
        tiles.append(lib.card(
            f'To {g["label"].lower()} · {g["pct"] * 100:.0f}% fat',
            f'{max(now_w["lb"] - g["lb"], 0):.1f} / {max(now_w["low_lb"] - g["lb"], 0):.1f} <small>lb</small>',
            f'mean / low above {g["lb"]:.1f} lb ({g["fat_lb"]:.1f} lb of fat)',
            lib.GOOD if now_w["lb"] <= g["lb"] else lib.WARN))
    recent = weekly[weekly["week"] >= M.this_monday(today) - pd.Timedelta(weeks=3)]
    tiles.append(lib.card("Weigh-in days", f'{int(recent["n"].fillna(0).sum())}',
                          "last 4 weeks · a gap breaks the trend"))
    lib.cards(tiles)

    lib.chart(_weight_figure(weekly, wgoals, today), "goals_weight")
    scan = (f'the {wgoals[0]["scan_date"]:%b %-d, %Y} DEXA scan' if wgoals else "a DEXA scan")
    st.caption("Each point is that week's 7-day average weight (hover for weigh-in days). Goal lines = "
               f"the weight at 24% and 20% body fat if {scan}'s lean and bone mass stay the same; "
               "they update with each new scan. Full history and body composition are on Nutrition → Body.")


# =============================================================================
# Goal 4 — body fat (DEXA measured; weight-implied estimate between scans)
# =============================================================================

def _body_fat_figure(scans, est, today):
    fig = go.Figure()
    if not est.empty:
        fig.add_trace(go.Scatter(
            x=est["week"], y=est["pct"] * 100, name="estimate from weight",
            mode="lines+markers", line=dict(color=lib.SERIES["bodyfat"], width=1.5, dash="dot"),
            marker=dict(size=5),
            hovertemplate="wk of %{x|%b %d} · ~%{y:.1f}% (from 7-day avg weight)<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=scans["date"], y=scans["body_fat_pct"], name="DEXA (measured)", mode="markers+text",
        text=[f"{v:.1f}%" for v in scans["body_fat_pct"]], textposition="top center",
        textfont=dict(size=10, color=lib.INK),
        marker=dict(symbol="diamond", size=12, color=lib.SERIES["bodyfat"]),
        hovertemplate="DEXA %{x|%b %d, %Y} · %{y:.1f}%<extra></extra>"))
    for (label, pct), dash in zip(T.BODY_FAT_GOALS, ("dash", "dot")):
        fig.add_hline(y=pct * 100, line_dash=dash, line_color=lib.GOOD, opacity=0.8,
                      annotation_text=f"{label.lower()} · {pct * 100:.0f}%",
                      annotation_position="bottom right",
                      annotation_font_size=10, annotation_font_color=lib.GOOD)
    fig.update_xaxes(range=_tracker_range(today))
    vals = list(scans["body_fat_pct"]) + list(est["pct"] * 100) + [p * 100 for _, p in T.BODY_FAT_GOALS]
    fig.update_yaxes(range=[min(vals) - 2, max(vals) + 3], ticksuffix="%")
    return lib.apply_theme(fig, 260, legend=True)


def _render_body_fat_section(today):
    st.markdown("## Goal 4 — Body fat")
    scans = M.dexa_scans()
    scans = scans[scans["body_fat_pct"].notna()] if not scans.empty else scans
    if scans.empty:
        st.caption("No DEXA scan on file yet — body fat goals need one.")
        return
    last = scans.iloc[-1]
    est = M.body_fat_estimate_weekly(TRACKER_START.date().isoformat())
    w = M.weight_now(today)
    now = None
    if w and not w["stale"] and pd.notna(last["lean"]):
        now = T.body_fat_at_weight(w["lb"], last["lean"], last["bone"] if pd.notna(last["bone"]) else 0.0) * 100

    tiles = [lib.card("DEXA (measured)", f"{last['body_fat_pct']:.1f}%", f"scan of {last['date']:%b %-d, %Y}")]
    if now is not None:
        tiles.append(lib.card("Estimate now", f"~{now:.1f}%",
                              "from the 7-day mean weight, lean and bone held at the last scan"))
    cur = now if now is not None else last["body_fat_pct"]
    for label, pct in T.BODY_FAT_GOALS:
        tiles.append(lib.card(f"To {label.lower()} · {pct * 100:.0f}%",
                              f"{max(cur - pct * 100, 0):.1f} <small>pts</small>",
                              "from the estimate" if now is not None else "from the last scan",
                              lib.GOOD if cur <= pct * 100 else lib.WARN))
    lib.cards(tiles)

    lib.chart(_body_fat_figure(scans, est, today), "goals_body_fat")
    st.caption("Diamonds = DEXA scans, the measurement of record. Dotted line = an estimate between "
               "scans from the scale's weight (each week's 7-day average) with lean and bone held at "
               "the last scan — the same assumption as the weight goals, so the two goals move "
               "together. It reads too low if you lose muscle; the next scan corrects it. The scale's "
               "own body-fat % (bioimpedance) stays off this chart: it read 22.8–24% the week DEXA "
               "measured 30.3%. It's on Nutrition → Body.")


# =============================================================================
# Lever trackers — exercise, sodium, alcohol, on the same weekly axis
# =============================================================================

def _render_lever_trackers(today):
    st.markdown("## Lever trackers")
    st.caption("The levers' weekly history on the same time axis as BP and weight, from June 2026. "
               "The current week is still filling in.")
    start = TRACKER_START.date().isoformat()

    st.markdown("### Exercise — moderate-vigorous minutes per week")
    zw = M.zone_minutes_weekly(start)
    if zw.empty:
        st.caption("No activities with heart-rate streams yet.")
    else:
        mv_c, vg_c = lib.ZONE_COLORS["Z3"], lib.ZONE_COLORS["Z4"]
        lib.chart(_tracker_figure(
            zw["week"], zw["modvig"], name="Z3–5 moderate-vigorous", color=mv_c, fmt="{:.0f}",
            today=today, unit=" min",
            goal=T.AHA_MODERATE_MIN_WK, goal_text=f"AHA ≥{T.AHA_MODERATE_MIN_WK} moderate-vigorous",
            goal_color=mv_c,
            second=zw["vig"], second_name="Z4–5 vigorous", second_color=vg_c,
            second_goal=T.AHA_VIGOROUS_MIN_WK, second_goal_text=f"AHA ≥{T.AHA_VIGOROUS_MIN_WK} vigorous"),
            "goals_tr_exercise")
        st.caption(f"Heart-rate-zone minutes (max HR {T.HRMAX}). Each line has its guideline in the "
                   "matching color; either one clearing its line meets the AHA minimum. Detail by "
                   "zone is on Training.")

    st.markdown("### Sodium — daily average per week")
    na = M.nutrition_daily(start)
    na = na[na["sodium_mg"].notna()] if not na.empty else na
    if na.empty:
        st.caption("No days logged yet.")
    else:
        wk = (na.assign(week=M.week_start(na["date"])).groupby("week")["sodium_mg"]
                .agg(["mean", "count"]))
        wk = wk.reindex(pd.date_range(wk.index.min(), M.this_monday(today), freq="W-MON"))
        lib.chart(_tracker_figure(
            wk.index, wk["mean"], name="mg/day", color=lib.SERIES["sodium"], fmt="{:,.0f}", today=today,
            unit=" mg", n=wk["count"], n_label="days logged",
            goal=T.SODIUM_MG_CEILING, goal_text=f"DASH ≤{T.SODIUM_MG_CEILING:,}",
            ref=T.SODIUM_MG_IDEAL, ref_text=f"ideal {T.SODIUM_MG_IDEAL:,}", lines=False), "goals_tr_sodium")
        st.caption("Each point is the mean of the days logged that week, net of ride sweat sodium "
                   "(hover for how many days). Weeks with nothing logged have no point.")

    st.markdown("### Alcohol — drinks per week")
    weekly = M.alcohol_weekly()
    if weekly.empty:
        st.caption("No drinks logged yet — tracked via Garmin Lifestyle Logging.")
    else:
        first = max(weekly.index.min(), TRACKER_START)
        weekly = weekly.reindex(pd.date_range(M.week_start(pd.Series([first]))[0], M.this_monday(today),
                                              freq="W-MON"), fill_value=0)
        lib.chart(_tracker_figure(
            weekly.index, weekly.values, name="drinks", color=lib.SERIES["alcohol"], fmt="{:.0f}",
            today=today, goal=T.ALCOHOL_DRINKS_WK, goal_text=f"guideline ≤{T.ALCOHOL_DRINKS_WK}"),
            "goals_tr_alcohol")
        st.caption("Garmin serving counts; a week with nothing logged counts as zero. For BP, lower "
                   "beats “under the guideline”.")


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
    wt = M.weight_weekly(start).set_index("week")
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
        row["Weight mean / low (lb)"] = (f'{wt.loc[wk, "lb"]:.1f} / {wt.loc[wk, "low"]:.1f}'
                                         if wk in wt.index and pd.notna(wt.loc[wk, "lb"]) else "—")
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
    wg = M.weight_goals()
    if wg:
        intro_bits.append("weight ≤" + " then ≤".join(
            f'{g["lb"]:.1f} lb ({g["pct"] * 100:.0f}% fat)' for g in wg))
        intro_bits.append("body fat ≤" + " then ≤".join(f"{p * 100:.0f}%" for _, p in T.BODY_FAT_GOALS))
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
    _render_weight_section(goals, today)
    _render_body_fat_section(today)
    _render_lever_trackers(today)
    _render_weekly_review(today)

    st.caption("Honesty rules: pace lines are arithmetic (gap ÷ weeks left), "
               "not forecasts; coverage counts stop absent data from reading as "
               "good behavior. History lives on Cardiology, Training and Nutrition.")
