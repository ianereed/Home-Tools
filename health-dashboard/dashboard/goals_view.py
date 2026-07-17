"""Goals dashboard page — the forward-looking view of the two physician-set
cardiovascular targets and the levers being used to reach them.

Ordering is deliberate (actionable first): ① levers row (weight, exercise,
sodium, alcohol, bloodwork cadence, medications), ② LDL trajectory with the
medication regimen as lanes sharing the same time axis, ③ blood-pressure
weekly means with the required pace to goal, ④ a weekly review table putting
BP next to the levers on one time base. The historical/retrospective views
stay on the Cardiology page untouched.

PHI posture (CARDIO_PLAN.md Standing rule 2): this module is committed and
must contain no PHI literals — no goal values, medication names, doctor
names, or lab values. Everything clinical is read from the gitignored
`cardiology/clinical_data.py` at runtime via getattr with safe fallbacks, and
app.py only wires this page in when that module exists on the host. The only
numeric constants here are public references (trial-published PCSK9i response
range, AHA activity minutes, US dietary-guideline drink ceiling, DASH sodium
targets via diet_content).
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
from dashboard.diet_content import DASH_TARGETS  # noqa: E402  (public constants)

# --- Public reference constants (no personal values) -------------------------
# PCSK9-inhibitor add-on LDL response, published trial range (FOURIER-class
# outcome trials report ~59-63% mean LDL reduction on top of a statin; band
# widened to 50-65% to absorb regimen nuance). Drawn as a reference range,
# never as a personal prediction.
PCSK9I_LDL_REDUCTION = (0.50, 0.65)
# Standard lipid-panel re-check window after a therapy change (weeks).
DRAW_WINDOW_WEEKS = (8, 12)
# AHA weekly aerobic-activity guideline (see br.AHA_GUIDES for the chart lines).
AHA_MODERATE_MIN_WK = 150
AHA_VIGOROUS_MIN_WK = 75
# US Dietary Guidelines: <=2 drinks/day for men -> 14/week (public reference).
ALCOHOL_WEEKLY_GUIDELINE = 14
# Regimen-lane colors (match build_report's statin cyan; teal/pink from
# lib.COLORWAY for the non-statin lanes).
_LANE_COLORS = ["#38bdf8", "#5eead4", "#f0abfc"]

_LEVERS_CSS = """
<style>
.glevers{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:8px 0 4px;}
@media (max-width:900px){.glevers{grid-template-columns:repeat(2,1fr);}}
.glever{background:#161b26;border:1px solid #2a2f3a;border-radius:8px;padding:10px 12px 8px;}
.glever .lab{font-size:10px;letter-spacing:1px;text-transform:uppercase;color:#8b93a7;
  display:flex;justify-content:space-between;align-items:center;}
.glever .val{font-size:20px;font-weight:650;margin:2px 0 0;color:#e6e9ef;
  font-variant-numeric:tabular-nums;}
.glever .val small{font-size:12px;color:#8b93a7;font-weight:500;}
.glever .tgt{font-size:11px;color:#8b93a7;margin:0 0 4px;line-height:1.45;}
.glever .tgt b{color:#e6e9ef;}
.gdot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none;}
</style>
"""


def _safe_load(query, params=()):
    """lib.load_df, degrading to an empty frame on a pre-migration DB (missing
    table -> pandas DatabaseError). Mirrors staleness_check's posture: a host
    whose health.db predates the cardio schema renders empty states, never a
    stack trace."""
    try:
        return lib.load_df(query, params)
    except Exception:
        return pd.DataFrame()


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


def _spark_svg(values, color, width=140, height=26):
    """Tiny inline-SVG trend line for a lever tile. Empty string when there
    aren't at least 2 points."""
    vals = [v for v in (values or []) if v is not None and not pd.isna(v)]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = width / (len(vals) - 1)
    pts = " ".join(
        f"{i * step:.1f},{height - 3 - (v - lo) / span * (height - 6):.1f}"
        for i, v in enumerate(vals))
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'style="display:block;margin-top:4px">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linejoin="round"/></svg>')


def _week_start(ts_series):
    """Monday of each timestamp's week (matches the Cardiology alcohol view)."""
    return ts_series.dt.to_period("W-SUN").dt.start_time


# =============================================================================
# Levers row (section ① — actionable data first)
# =============================================================================

def _tile(label, value, target_html, color, spark=""):
    return (f'<div class="glever"><div class="lab">{label}'
            f'<span class="gdot" style="background:{color}"></span></div>'
            f'<div class="val">{value}</div>'
            f'<div class="tgt">{target_html}</div>{spark}</div>')


def _weight_tile(goals, today):
    w = _safe_load(
        "SELECT timestamp, weight_kg FROM body_weight "
        "WHERE timestamp >= ? ORDER BY timestamp",
        ((today - pd.Timedelta(days=365)).isoformat(),))
    wg = (goals or {}).get("weight", {})
    baseline_kg = wg.get("baseline_kg")
    band = None
    if baseline_kg and wg.get("lose_lb_min") is not None and wg.get("lose_lb_max") is not None:
        baseline_lb = baseline_kg * lib.KG_TO_LB
        band = (baseline_lb - wg["lose_lb_max"], baseline_lb - wg["lose_lb_min"])
    if w.empty:
        return _tile("Weight", "—", "no weigh-ins yet · Garmin scale pending", lib.MUTED)
    w["timestamp"] = pd.to_datetime(w["timestamp"])
    w["lb"] = w["weight_kg"] * lib.KG_TO_LB
    latest = w.iloc[-1]
    age_d = (today.normalize() - latest["timestamp"].normalize()).days
    if band:
        lo, hi = band
        if latest["lb"] <= hi:
            color, gap = lib.GOOD, "in the goal band"
        else:
            color = lib.WARN
            gap = f'<b>{latest["lb"] - hi:.0f}–{latest["lb"] - lo:.0f} lb to go</b>'
        tgt = f"band {lo:.0f}–{hi:.0f} lb · {gap}"
    else:
        color, tgt = lib.MUTED, "no goal band configured"
    stale = f" · last {age_d} d ago (scale pending)" if age_d > 10 else ""
    return _tile("Weight", f'{latest["lb"]:.1f} <small>lb</small>', tgt + stale,
                 color, _spark_svg(w["lb"].tolist()[-14:], lib.WARN))


def _exercise_tile(today):
    """Moderate-vigorous HR-zone minutes/week vs the AHA guideline, from the
    Cardiology page's cached weekly frame; falls back to raw activity duration
    when the zone pipeline has nothing (e.g. empty DB)."""
    try:
        from dashboard.cardiology_view import _frames
        _, w, _ = _frames()
        w = w.copy()
        w["bucket_start"] = pd.to_datetime(w["bucket_start"])
        this_monday = (today - pd.Timedelta(days=today.weekday())).normalize()
        done = w[w["bucket_start"] < this_monday].tail(4)
        modvig = done["mod_vigorous_min_per_week"].dropna()
        vig = done["vigorous_min_per_week"].dropna()
        if not modvig.empty:
            mv, vg = modvig.mean(), (vig.mean() if not vig.empty else 0)
            color = lib.GOOD if (mv >= AHA_MODERATE_MIN_WK or vg >= AHA_VIGOROUS_MIN_WK) else lib.WARN
            spark_vals = w["mod_vigorous_min_per_week"].dropna().tolist()[-12:]
            return _tile(
                "Exercise", f"{mv:.0f} <small>Z3–5 min/wk</small>",
                f"AHA ≥{AHA_MODERATE_MIN_WK} moderate (or ≥{AHA_VIGOROUS_MIN_WK} vigorous) · "
                f"4-wk avg · vigorous {vg:.0f}",
                color, _spark_svg(spark_vals, lib.ACCENT))
    except (Exception, SystemExit):
        pass
    acts = _safe_load(
        "SELECT date, duration_minutes FROM activities "
        "WHERE date >= ? AND dup_of IS NULL ORDER BY date",
        ((today - pd.Timedelta(days=28)).date().isoformat(),))
    if acts.empty:
        return _tile("Exercise", "—", "no activity data yet", lib.MUTED)
    per_wk = acts["duration_minutes"].sum() / 4
    return _tile("Exercise", f"{per_wk:.0f} <small>min/wk</small>",
                 "raw duration (HR-zone minutes unavailable) · "
                 f"AHA ≥{AHA_MODERATE_MIN_WK} moderate", lib.MUTED)


def _sodium_tile(today):
    n = _safe_load(
        "SELECT date, sodium_mg FROM nutrition_daily WHERE date >= ? ORDER BY date",
        ((today - pd.Timedelta(days=7)).date().isoformat(),))
    logged = n[n["sodium_mg"].notna()] if not n.empty else n
    if n.empty or logged.empty:
        return _tile("Sodium", "—",
                     f'no days logged this week · DASH ≤{DASH_TARGETS["sodium_mg_ceiling"]:,} mg',
                     lib.MUTED)
    mean = logged["sodium_mg"].mean()
    cov = len(logged)
    if cov < 4:
        color = lib.MUTED
    else:
        color = lib.GOOD if mean <= DASH_TARGETS["sodium_mg_ceiling"] else lib.BAD
    spark = _safe_load(
        "SELECT sodium_mg FROM nutrition_daily WHERE date >= ? ORDER BY date",
        ((today - pd.Timedelta(days=14)).date().isoformat(),))
    return _tile(
        "Sodium", f"{mean:,.0f} <small>mg/day</small>",
        f'DASH ≤{DASH_TARGETS["sodium_mg_ceiling"]:,} · ideal '
        f'{DASH_TARGETS["sodium_mg_ideal"]:,} · <b>coverage {cov}/7 days</b>'
        + ("" if cov >= 4 else " — trend unlocks at 4/7"),
        color, _spark_svg(spark["sodium_mg"].tolist(), lib.ACCENT))


def _alcohol_tile(today):
    df = _safe_load(
        "SELECT date, amount FROM lifestyle_log "
        "WHERE name = 'Alcohol' AND source = 'garmin' ORDER BY date")
    if df.empty:
        return _tile("Alcohol", "—",
                     "no drinks logged · tracked via Garmin Lifestyle Logging", lib.MUTED)
    df["date"] = pd.to_datetime(df["date"])
    df["week"] = _week_start(df["date"])
    weekly = df.groupby("week")["amount"].sum()
    this_week = (today - pd.Timedelta(days=today.weekday())).normalize()
    current = weekly.get(this_week, 0)
    prior4 = weekly[weekly.index < this_week].tail(4)
    avg4 = prior4.mean() if not prior4.empty else None
    over = (current > ALCOHOL_WEEKLY_GUIDELINE
            or (avg4 is not None and avg4 > ALCOHOL_WEEKLY_GUIDELINE))
    color = lib.WARN if over else lib.GOOD
    avg_txt = f"{avg4:.0f}" if avg4 is not None else "—"
    return _tile(
        "Alcohol", f"{current:.0f} <small>drinks this wk</small>",
        f"≤{ALCOHOL_WEEKLY_GUIDELINE}/wk guideline · prior 4-wk avg <b>{avg_txt}</b> · "
        "for BP, lower beats “under guideline”",
        color, _spark_svg(weekly.tolist()[-8:], lib.ACCENT))


def _bloodwork_tile(lip, today):
    if lip.empty:
        return _tile("Bloodwork", "—", "no lab draws recorded", lib.MUTED)
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
    return _tile("Bloodwork", f"{days_since} <small>d since draw</small>", tgt, color)


def _meds_tile(meds, today):
    active = [m for m in meds if (m.get("status") or "").lower().startswith("active")]
    if not active:
        return _tile("Medications", "—", "no active medications recorded", lib.MUTED)
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
    return _tile("Medications", f'<span style="font-size:14px">{lines}</span>',
                 " · ".join(bits) or "current regimen", lib.GOOD)


def _render_levers(goals, lip, today):
    st.markdown("## Levers — this week")
    st.caption("The six trackers of the treatment plan, current value vs target. "
               "Gray dot = not enough data yet; details live in the sections below "
               "and on the Cardiology/Diet pages.")
    meds = getattr(CD, "MEDICATIONS", [])
    tiles = "".join([
        _weight_tile(goals, today),
        _exercise_tile(today),
        _sodium_tile(today),
        _alcohol_tile(today),
        _bloodwork_tile(lip, today),
        _meds_tile(meds, today),
    ])
    st.markdown(f'<div class="glevers">{tiles}</div>', unsafe_allow_html=True)


# =============================================================================
# Goal 1 — LDL trajectory × regimen (section ②)
# =============================================================================

def _rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.2f})"


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
        line=dict(color=lib.BAD, width=2), marker=dict(size=8)), row=1, col=1)
    apo = lip[lip["apob"].notna()]
    if not apo.empty:
        fig.add_trace(go.Scatter(
            x=apo["date"], y=apo["apob"], name="ApoB",
            mode="lines+markers", line=dict(color=lib.WARN, width=1.5, dash="dot"),
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
        color = _rgba(lane_color, seg["frac"] * 0.85)
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
    ldl_goal = (goals or {}).get("ldl", {})
    goal_val = ldl_goal.get("stretch") or ldl_goal.get("target")
    deadline = pd.Timestamp(goals["deadline"]) if goals and goals.get("deadline") else None

    c = st.columns(4)
    c[0].metric("Latest LDL", f'{latest["ldl"]:.0f} mg/dL',
                help=f'drawn {latest["date"]:%Y-%m-%d}')
    if goal_val is not None and pd.notna(latest["ldl"]):
        c[1].metric("To goal", f'{latest["ldl"] - goal_val:+.0f}',
                    help=f"goal <{goal_val} mg/dL")
    change = _therapy_change_date(getattr(CD, "STATIN_EVENTS", []),
                                  getattr(CD, "MEDICATIONS", []))
    if change is not None:
        w0 = change + pd.Timedelta(weeks=DRAW_WINDOW_WEEKS[0])
        w1 = change + pd.Timedelta(weeks=DRAW_WINDOW_WEEKS[1])
        c[2].metric("Next draw window", f"{w0:%b %d} – {w1:%b %d}",
                    help=f"{DRAW_WINDOW_WEEKS[0]}–{DRAW_WINDOW_WEEKS[1]} weeks after "
                         f"the regimen change on {change:%Y-%m-%d}")
    if deadline is not None:
        c[3].metric("Deadline", f"{(deadline - today).days} days",
                    help=f"{deadline:%Y-%m-%d}")

    st.plotly_chart(_ldl_figure(lip, goals, today), use_container_width=True,
                    key="goals_ldl")
    st.caption("Red = LDL-C draws · amber = ApoB · lanes = medication regimen on the "
               "same time axis (opacity tracks dose; dotted = ongoing). The blue band "
               "is the published PCSK9-inhibitor response range applied to the latest "
               "draw — a reference, not a prediction; it ignores the concurrent statin "
               "step-down and any bridge therapy between draws. Exercise stays off "
               "this chart deliberately: medication owns this goal, lifestyle owns "
               "Goal 2.")


# =============================================================================
# Goal 2 — blood pressure pace (section ③)
# =============================================================================

def _weekly_bp(bp):
    bp = bp.copy()
    bp["timestamp"] = pd.to_datetime(bp["timestamp"])
    bp["week"] = _week_start(bp["timestamp"])
    weekly = bp.groupby("week").agg(
        sys=("systolic", "mean"), dia=("diastolic", "mean"),
        n=("systolic", "count")).reset_index()
    if weekly.empty:
        return weekly
    full = pd.DataFrame({"week": pd.date_range(
        weekly["week"].min(), weekly["week"].max(), freq="W-MON")})
    return full.merge(weekly, on="week", how="left")


def _bp_figure(weekly, goals, mean14, today):
    bp_goal = (goals or {}).get("bp", {})
    sys_goal, dia_goal = bp_goal.get("systolic"), bp_goal.get("diastolic")
    deadline = pd.Timestamp(goals["deadline"]) if goals and goals.get("deadline") else None
    x_end = today + pd.Timedelta(days=60)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.5, 0.5], vertical_spacing=0.12,
                        subplot_titles=("Systolic", "Diastolic"))
    series = [("sys", lib.ACCENT, sys_goal, 1), ("dia", lib.HRV, dia_goal, 2)]
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
    bp = _safe_load(
        "SELECT timestamp, systolic, diastolic FROM blood_pressure ORDER BY timestamp")
    if bp.empty:
        st.caption("No blood-pressure readings yet — they sync from Garmin Connect.")
        return
    bp["timestamp"] = pd.to_datetime(bp["timestamp"])
    last14 = bp[bp["timestamp"] >= today - pd.Timedelta(days=14)]
    src = last14 if not last14.empty else bp
    mean14 = {"sys": src["systolic"].mean(), "dia": src["diastolic"].mean()}
    cat, color = lib.bp_category(mean14["sys"], mean14["dia"])
    bp_goal = (goals or {}).get("bp", {})
    sys_goal, dia_goal = bp_goal.get("systolic"), bp_goal.get("diastolic")
    deadline = pd.Timestamp(goals["deadline"]) if goals and goals.get("deadline") else None

    weekly = _weekly_bp(bp)
    gap_weeks = int(weekly["n"].isna().sum()) if not weekly.empty else 0

    c = st.columns(4)
    c[0].metric("14-day mean", f'{mean14["sys"]:.0f}/{mean14["dia"]:.0f}')
    c[0].markdown(f'<span style="color:{color};font-size:12px;">{cat}</span>',
                  unsafe_allow_html=True)
    if sys_goal and dia_goal:
        c[1].metric("To goal",
                    f'{max(mean14["sys"] - sys_goal, 0):.0f} sys · '
                    f'{max(mean14["dia"] - dia_goal, 0):.0f} dia',
                    help=f"vs <{sys_goal}/{dia_goal}")
        sp = _pace_per_week(mean14["sys"], sys_goal, today, deadline)
        dp = _pace_per_week(mean14["dia"], dia_goal, today, deadline)
        if sp is not None or dp is not None:
            c[2].metric("Pace needed",
                        f"{sp:+.2f} / {dp:+.2f}" if sp and dp else
                        f"{(sp or dp):+.2f}",
                        help="mmHg per week, to reach goal by the deadline — "
                             "arithmetic, not a forecast")
    c[3].metric("Readings", f"{len(bp)}",
                help=f"{gap_weeks} week(s) in range with no readings")

    st.plotly_chart(_bp_figure(weekly, goals, mean14, today),
                    use_container_width=True, key="goals_bp")
    st.caption("Points = weekly means of cuff readings (hover for n; gaps = weeks "
               "with no readings — a gap breaks the trend, keep the cuff habit). "
               "Dashed white line = required pace to the goal by the deadline. "
               "Daily readings and AHA category bands stay on the Cardiology page.")


# =============================================================================
# Weekly review (section ④ — levers × BP on one time base)
# =============================================================================

def _weekly_review(today, n_weeks=8):
    this_monday = (today - pd.Timedelta(days=today.weekday())).normalize()
    weeks = [this_monday - pd.Timedelta(weeks=i) for i in range(n_weeks)]
    start_iso = min(weeks).isoformat()

    def by_week(df, ts_col):
        if df.empty:
            return df
        df = df.copy()
        df[ts_col] = pd.to_datetime(df[ts_col])
        df["week"] = _week_start(df[ts_col])
        return df

    bp = by_week(_safe_load(
        "SELECT timestamp, systolic, diastolic FROM blood_pressure "
        "WHERE timestamp >= ?", (start_iso,)), "timestamp")
    wt = by_week(_safe_load(
        "SELECT timestamp, weight_kg FROM body_weight WHERE timestamp >= ?",
        (start_iso,)), "timestamp")
    ac = by_week(_safe_load(
        "SELECT date, duration_minutes FROM activities "
        "WHERE date >= ? AND dup_of IS NULL", (start_iso[:10],)), "date")
    al = by_week(_safe_load(
        "SELECT date, amount FROM lifestyle_log "
        "WHERE name = 'Alcohol' AND source = 'garmin' AND date >= ?",
        (start_iso[:10],)), "date")
    na = by_week(_safe_load(
        "SELECT date, sodium_mg FROM nutrition_daily WHERE date >= ?",
        (start_iso[:10],)), "date")

    rows = []
    for wk in weeks:
        row = {"Week of": f"{wk:%b %d ’%y}" + (" (current)" if wk == this_monday else "")}
        g = bp[bp["week"] == wk] if not bp.empty else pd.DataFrame()
        row["BP mean"] = (f'{g["systolic"].mean():.0f} / {g["diastolic"].mean():.0f}'
                          if not g.empty else "—")
        row["Cuff readings"] = f"{len(g)}" if not g.empty else "0"
        g = wt[wt["week"] == wk] if not wt.empty else pd.DataFrame()
        row["Weight (lb)"] = (f'{g["weight_kg"].mean() * lib.KG_TO_LB:.1f}'
                              if not g.empty else "—")
        g = ac[ac["week"] == wk] if not ac.empty else pd.DataFrame()
        row["Active min"] = f'{g["duration_minutes"].sum():.0f}' if not g.empty else "—"
        g = al[al["week"] == wk] if not al.empty else pd.DataFrame()
        row["Drinks"] = f'{g["amount"].sum():.0f}' if not g.empty else "—"
        g = na[na["week"] == wk] if not na.empty else pd.DataFrame()
        row["Sodium (mg, logged days)"] = (
            f'{g["sodium_mg"].mean():,.0f} · {g["sodium_mg"].notna().sum()} d'
            if not g.empty and g["sodium_mg"].notna().any() else "—")
        rows.append(row)
    return pd.DataFrame(rows)


def _render_weekly_review(today):
    st.markdown("## Weekly review — levers × BP")
    st.caption("One row per week, newest first. A “—” is this week's to-do, not "
               "missing history: the row fills in as habits and devices come online. "
               "Active min is raw duration (all activities); the AHA comparison uses "
               "HR-zone minutes in the Exercise tile above.")
    st.dataframe(_weekly_review(today), use_container_width=True, hide_index=True)


# =============================================================================
# Page
# =============================================================================

def render_goals():
    st.markdown(_LEVERS_CSS, unsafe_allow_html=True)
    st.markdown("# Goals")

    today = pd.Timestamp.now().normalize()
    goals = getattr(CD, "CARDIO_GOALS", None)
    lip = br.lipids_df()

    intro_bits = []
    ldl_goal = (goals or {}).get("ldl", {})
    if ldl_goal.get("stretch") or ldl_goal.get("target"):
        intro_bits.append(
            f'LDL &lt; {ldl_goal.get("stretch") or ldl_goal.get("target")} mg/dL')
    bp_goal = (goals or {}).get("bp", {})
    if bp_goal.get("systolic") and bp_goal.get("diastolic"):
        intro_bits.append(f'BP &lt; {bp_goal["systolic"]}/{bp_goal["diastolic"]}')
    if goals and goals.get("deadline"):
        dl = pd.Timestamp(goals["deadline"])
        intro_bits.append(f"by {dl:%b %Y} · <b>{(dl - today).days} days</b>")
    if intro_bits:
        st.markdown(
            f'<div style="color:#8b93a7;font-size:13px;margin:-6px 0 10px;">'
            f'{" &nbsp;·&nbsp; ".join(intro_bits)}'
            f'{" &nbsp;·&nbsp; set by " + goals.get("set_by", "") if goals and goals.get("set_by") else ""}'
            f'</div>', unsafe_allow_html=True)

    _render_levers(goals, lip, today)
    _render_ldl_section(lip, goals, today)
    _render_bp_section(goals, today)
    _render_weekly_review(today)

    st.caption("Honesty rules: the LDL band is a published trial range, never a "
               "personal prediction; pace lines are arithmetic (gap ÷ weeks left), "
               "not forecasts; coverage counts stop absent data from reading as "
               "good behavior. Historical detail lives on the Cardiology and Diet "
               "pages.")
