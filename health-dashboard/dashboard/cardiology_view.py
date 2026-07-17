"""Cardiology dashboard page.

Renders the cardiology dataset (LabCorp lipid/statin history overlaid on the
wearable activity record) as a native dashboard page. It reuses the figure and
table builders in `cardiology/build_report.py` so this page and the standalone
HTML report never drift: Plotly figures are shown with st.plotly_chart, and the
report's HTML stat-cards / tables are rendered via st.markdown with the report's
own CSS injected once.

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
from collectors.db import DB_PATH  # noqa: E402
from dashboard import lib      # noqa: E402


# The report's presentation CSS (cards + clinical tables), scoped so it only
# styles the cardiology page's injected HTML and inherits the dark dashboard bg.
_CARD_CSS = """
<style>
.cardio.summary{background:#10261a;border-left:3px solid #4ade80;padding:12px 16px;
  border-radius:6px;font-size:14px;line-height:1.55;margin:6px 0 14px;}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0;}
.card{background:#161b26;border:1px solid #2a2f3a;border-radius:8px;padding:10px 14px;}
.cardlabel{font-size:11px;color:#8b93a7;text-transform:uppercase;letter-spacing:1px;}
.cardval{font-size:24px;font-weight:650;margin:2px 0;} .cardval.hi{color:#f87171;}
.cardsub{font-size:11px;color:#8b93a7;line-height:1.4;}
.delta{font-size:13px;font-weight:600;} .delta.up{color:#f87171;} .delta.dn{color:#4ade80;}
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
    con = sqlite3.connect(DB_PATH)
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


def _goal_card_html(label: str, value: str, sub: str, color: str) -> str:
    return (f'<div class=card><div class=cardlabel>{label}</div>'
            f'<div class=cardval style="color:{color}">{value}</div>'
            f'<div class=cardsub>{sub}</div></div>')


def _goals_strip_html(lip: pd.DataFrame) -> str:
    """Three-card goals strip: LDL vs target, BP category, weight vs goal band.

    Gated on CARDIO_GOALS as a whole — with an un-updated PHI file (no
    CARDIO_GOALS attribute) this returns "" and the page renders exactly as
    before Phase 1.
    """
    goals = getattr(CD, "CARDIO_GOALS", None)
    if not goals:
        return ""

    # --- LDL ---
    ldl_goal = goals.get("ldl", {})
    target, stretch, unit = ldl_goal.get("target"), ldl_goal.get("stretch"), ldl_goal.get("unit", "mg/dL")
    latest_ldl = lip.iloc[-1]["ldl"] if not lip.empty else None
    if latest_ldl is not None and pd.notna(latest_ldl) and target is not None:
        if latest_ldl < target:
            color = lib.GOOD
        elif latest_ldl < 100:
            color = lib.WARN
        else:
            color = lib.BAD
        ldl_card = _goal_card_html(
            "LDL-C", f"{int(latest_ldl)} mg/dL",
            f"target &lt;{target} · stretch &lt;{stretch} {unit}", color)
    else:
        ldl_card = _goal_card_html("LDL-C", "—", "no lipid draw yet", lib.MUTED)

    # --- Blood pressure ---
    since_14d = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()
    bp14 = lib.load_df(
        "SELECT systolic, diastolic FROM blood_pressure WHERE timestamp >= ? ORDER BY timestamp",
        (since_14d,))
    if not bp14.empty:
        sys_m, dia_m = bp14["systolic"].mean(), bp14["diastolic"].mean()
        cat, color = lib.bp_category(sys_m, dia_m)
        bp_card = _goal_card_html(
            "Blood pressure", f"{sys_m:.0f}/{dia_m:.0f}",
            f"{cat} · 14-day mean ({len(bp14)} readings)", color)
    else:
        latest_bp = lib.load_df(
            "SELECT systolic, diastolic, timestamp FROM blood_pressure "
            "ORDER BY timestamp DESC LIMIT 1")
        if not latest_bp.empty:
            r = latest_bp.iloc[0]
            cat, color = lib.bp_category(r["systolic"], r["diastolic"])
            bp_card = _goal_card_html(
                "Blood pressure", f"{int(r['systolic'])}/{int(r['diastolic'])}",
                f"{cat} · latest ({str(r['timestamp'])[:10]})", color)
        else:
            bp_card = _goal_card_html("Blood pressure", "—", "no readings yet", lib.MUTED)

    # --- Weight ---
    weight_goal = goals.get("weight", {})
    baseline_kg = weight_goal.get("baseline_kg")
    lose_min, lose_max = weight_goal.get("lose_lb_min"), weight_goal.get("lose_lb_max")
    since_7d = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    w7 = lib.load_df(
        "SELECT weight_kg FROM body_weight WHERE timestamp >= ? ORDER BY timestamp",
        (since_7d,))
    if not w7.empty:
        mean_lb = w7["weight_kg"].mean() * lib.KG_TO_LB
        if baseline_kg and lose_min is not None and lose_max is not None:
            baseline_lb = baseline_kg * lib.KG_TO_LB
            goal_min_lb, goal_max_lb = baseline_lb - lose_max, baseline_lb - lose_min
            delta_lb = mean_lb - baseline_lb
            color = lib.GOOD if mean_lb <= goal_max_lb else lib.WARN
            sub = f"goal {goal_min_lb:.0f}–{goal_max_lb:.0f} lb · Δ{delta_lb:+.1f} lb vs baseline"
        else:
            color, sub = lib.MUTED, "7-day mean"
        weight_card = _goal_card_html("Weight", f"{mean_lb:.1f} lb", sub, color)
    else:
        weight_card = _goal_card_html("Weight", "—", "awaiting scale", lib.MUTED)

    return f'<div class=cards>{ldl_card}{bp_card}{weight_card}</div>'


def _medications_html() -> str:
    """One card per MEDICATIONS entry. Gated on getattr(CD, "MEDICATIONS", [])
    — empty/absent renders nothing (backward compatible with an un-updated
    PHI file)."""
    meds = getattr(CD, "MEDICATIONS", [])
    if not meds:
        return ""
    cards = []
    for m in meds:
        started = m.get("start")
        status = m.get("status", "")
        started_line = f"Started {started} ({status})" if started else f"<b>{status}</b>"
        sub = (f'{m.get("form", "")} · {m.get("frequency", "")}<br>'
               f'{started_line}<br>'
               f'{m.get("prescriber", "")} · {m.get("purpose", "")}')
        label = f'{m.get("name", "")} ({m.get("brand", "")})' if m.get("brand") else m.get("name", "")
        cards.append(_goal_card_html(label, m.get("dose", "—"), sub, lib.INK))
    return f'<div class=cards>{"".join(cards)}</div>'


def _render_bp_section():
    """Blood pressure: metrics row, systolic/diastolic scatter + 7-day rolling
    means with AHA bands, recent-readings table. Empty-safe."""
    st.markdown("## Blood pressure")
    bp = lib.load_df(
        "SELECT timestamp, systolic, diastolic, pulse FROM blood_pressure ORDER BY timestamp")
    if bp.empty:
        st.caption("No blood-pressure readings yet — they sync from Garmin Connect "
                   "(cuff or manual entries).")
        return

    bp["timestamp"] = pd.to_datetime(bp["timestamp"])
    latest = bp.iloc[-1]
    cat, color = lib.bp_category(latest["systolic"], latest["diastolic"])
    since_14d = pd.Timestamp.now() - pd.Timedelta(days=14)
    last_14 = bp[bp["timestamp"] >= since_14d]
    days_ago = (pd.Timestamp.now().normalize() - latest["timestamp"].normalize()).days

    c = st.columns(4)
    c[0].metric("Latest", f"{int(latest['systolic'])}/{int(latest['diastolic'])}")
    c[0].markdown(f'<span style="color:{color};font-size:12px;">{cat}</span>',
                  unsafe_allow_html=True)
    c[1].metric("14-day mean",
                f"{last_14['systolic'].mean():.0f}/{last_14['diastolic'].mean():.0f}"
                if not last_14.empty else "—")
    c[2].metric("Readings in range", f"{len(bp)}")
    c[3].metric("Last reading", f"{days_ago} day{'s' if days_ago != 1 else ''} ago")

    bp["sys_roll"] = bp["systolic"].rolling(7, min_periods=1).mean()
    bp["dia_roll"] = bp["diastolic"].rolling(7, min_periods=1).mean()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=bp["timestamp"], y=bp["systolic"], mode="markers",
                              name="Systolic", marker=dict(color=lib.ACCENT, size=6)))
    fig.add_trace(go.Scatter(x=bp["timestamp"], y=bp["diastolic"], mode="markers",
                              name="Diastolic", marker=dict(color=lib.HRV, size=6)))
    fig.add_trace(go.Scatter(x=bp["timestamp"], y=bp["sys_roll"], mode="lines",
                              name="Systolic 7d mean", line=dict(color=lib.ACCENT, width=2)))
    fig.add_trace(go.Scatter(x=bp["timestamp"], y=bp["dia_roll"], mode="lines",
                              name="Diastolic 7d mean", line=dict(color=lib.HRV, width=2)))
    lib.add_bp_bands(fig)
    st.plotly_chart(lib.apply_theme(fig, 260, legend=True), use_container_width=True,
                    key="cardio_bp")

    with st.expander("Recent readings"):
        table = bp.copy()
        table["category"] = table.apply(
            lambda r: lib.bp_category(r["systolic"], r["diastolic"])[0], axis=1)
        table["timestamp"] = table["timestamp"].dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(
            table[["timestamp", "systolic", "diastolic", "pulse", "category"]].iloc[::-1],
            use_container_width=True, hide_index=True)


def _render_weight_section():
    """Weight: metrics row in lb (kg in help text), daily-mean + 7-day rolling
    chart in lb, goal band + baseline overlay only when CARDIO_GOALS is
    present, DEXA (diamond) and Apple (circle) points overlaid. Empty-safe."""
    st.markdown("## Weight")
    w = lib.load_df("SELECT timestamp, weight_kg, source FROM body_weight ORDER BY timestamp")
    if w.empty:
        st.caption("No weigh-ins yet — data will flow from the Garmin scale once "
                   "it's set up.")
        return

    w["timestamp"] = pd.to_datetime(w["timestamp"])
    w["weight_lb"] = w["weight_kg"] * lib.KG_TO_LB
    latest = w.iloc[-1]
    since_7d = pd.Timestamp.now() - pd.Timedelta(days=7)
    last_7 = w[w["timestamp"] >= since_7d]

    goals = getattr(CD, "CARDIO_GOALS", None)
    weight_goal = goals.get("weight", {}) if goals else {}
    baseline_kg = weight_goal.get("baseline_kg")
    has_baseline = baseline_kg is not None

    cols = st.columns(4 if has_baseline else 3)
    cols[0].metric("Latest", f"{latest['weight_lb']:.1f} lb",
                   help=f"{latest['weight_kg']:.1f} kg")
    cols[1].metric("7-day mean",
                   f"{last_7['weight_lb'].mean():.1f} lb" if not last_7.empty else "—")
    cols[2].metric("Readings", f"{len(w)}")
    if has_baseline:
        baseline_lb = baseline_kg * lib.KG_TO_LB
        delta_lb = latest["weight_lb"] - baseline_lb
        cols[3].metric("vs baseline", f"{delta_lb:+.1f} lb")

    daily = w.copy()
    daily["date"] = daily["timestamp"].dt.date
    daily_mean = daily.groupby("date", as_index=False)["weight_lb"].mean()
    daily_mean["date"] = pd.to_datetime(daily_mean["date"])
    daily_mean["roll"] = daily_mean["weight_lb"].rolling(7, min_periods=1).mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily_mean["date"], y=daily_mean["weight_lb"], mode="markers",
                              name="Daily mean", marker=dict(color=lib.ACCENT, size=5, opacity=0.5)))
    fig.add_trace(go.Scatter(x=daily_mean["date"], y=daily_mean["roll"], mode="lines",
                              name="7-day rolling", line=dict(color=lib.ACCENT, width=2)))

    dexa = w[w["source"] == "dexa"]
    if not dexa.empty:
        fig.add_trace(go.Scatter(x=dexa["timestamp"], y=dexa["weight_lb"], mode="markers",
                                  name="DEXA", marker=dict(symbol="diamond", size=10, color=lib.WARN)))
    apple = w[w["source"] == "apple"]
    if not apple.empty:
        fig.add_trace(go.Scatter(x=apple["timestamp"], y=apple["weight_lb"], mode="markers",
                                  name="Apple", marker=dict(symbol="circle", size=8, color=lib.SLEEP)))

    if (has_baseline and weight_goal.get("lose_lb_min") is not None
            and weight_goal.get("lose_lb_max") is not None):
        goal_min_lb = baseline_lb - weight_goal["lose_lb_max"]
        goal_max_lb = baseline_lb - weight_goal["lose_lb_min"]
        fig.add_hrect(y0=goal_min_lb, y1=goal_max_lb, fillcolor=lib.GOOD, opacity=0.07,
                      line_width=0, annotation_text="goal", annotation_position="top left")
        fig.add_hline(y=baseline_lb, line_dash="dot", line_color=lib.MUTED, line_width=1,
                      annotation_text="baseline", annotation_position="bottom right")

    st.plotly_chart(lib.apply_theme(fig, 260, legend=True), use_container_width=True,
                    key="cardio_weight")


def _render_body_composition_section():
    """Body composition: rendered only when body_composition has rows. BIA
    (Garmin) and DEXA are always separate traces — different measurement
    physics, never merged into one series."""
    bc = lib.load_df(
        "SELECT timestamp, body_fat_pct, lean_mass_kg, visceral_fat_rating, "
        "visceral_fat_mass_kg, source FROM body_composition ORDER BY timestamp")
    if bc.empty:
        return

    st.markdown("## Body composition")
    bc["timestamp"] = pd.to_datetime(bc["timestamp"])
    bia = bc[bc["source"] == "garmin"]
    dexa = bc[bc["source"] == "dexa"]

    st.markdown("### Body fat %")
    st.caption("BIA (Garmin scale) and DEXA measure body fat with different physics "
               "and are not directly comparable — shown as separate traces, never merged.")
    fat_fig = go.Figure()
    if not bia.empty:
        fat_fig.add_trace(go.Scatter(x=bia["timestamp"], y=bia["body_fat_pct"],
                                      mode="lines+markers", name="BIA (Garmin scale)",
                                      line=dict(color=lib.ACCENT, width=2)))
    if not dexa.empty:
        fat_fig.add_trace(go.Scatter(x=dexa["timestamp"], y=dexa["body_fat_pct"],
                                      mode="markers", name="DEXA",
                                      marker=dict(size=12, color=lib.WARN, symbol="diamond")))
    st.plotly_chart(lib.apply_theme(fat_fig, 240, legend=True), use_container_width=True,
                    key="cardio_bodyfat")

    c1, c2 = st.columns(2)
    with c1:
        lean = bc[bc["lean_mass_kg"].notna()]
        if not lean.empty:
            st.markdown("### Lean mass")
            fig = go.Figure()
            for src, name, color in (("garmin", "Garmin", lib.ACCENT), ("dexa", "DEXA", lib.WARN)):
                d = lean[lean["source"] == src]
                if not d.empty:
                    fig.add_trace(go.Scatter(x=d["timestamp"], y=d["lean_mass_kg"],
                                              mode="lines+markers", name=name,
                                              line=dict(color=color)))
            st.plotly_chart(lib.apply_theme(fig, 180, legend=True), use_container_width=True,
                            key="cardio_lean")
    with c2:
        visc = bc[bc["visceral_fat_rating"].notna() | bc["visceral_fat_mass_kg"].notna()]
        if not visc.empty:
            st.markdown("### Visceral fat")
            fig = go.Figure()
            garmin_v = visc[(visc["source"] == "garmin") & visc["visceral_fat_rating"].notna()]
            if not garmin_v.empty:
                fig.add_trace(go.Scatter(x=garmin_v["timestamp"], y=garmin_v["visceral_fat_rating"],
                                          mode="lines+markers", name="Garmin rating",
                                          line=dict(color=lib.ACCENT)))
            dexa_v = visc[(visc["source"] == "dexa") & visc["visceral_fat_mass_kg"].notna()]
            if not dexa_v.empty:
                fig.add_trace(go.Scatter(x=dexa_v["timestamp"], y=dexa_v["visceral_fat_mass_kg"],
                                          mode="markers", name="DEXA mass (kg)",
                                          marker=dict(size=10, color=lib.WARN, symbol="diamond")))
            st.plotly_chart(lib.apply_theme(fig, 180, legend=True), use_container_width=True,
                            key="cardio_visceral")

    if not dexa.empty:
        with st.expander("DEXA history"):
            table = dexa.copy()
            table["timestamp"] = table["timestamp"].dt.strftime("%Y-%m-%d")
            st.dataframe(
                table[["timestamp", "body_fat_pct", "lean_mass_kg", "visceral_fat_mass_kg"]],
                use_container_width=True, hide_index=True)


# US Dietary Guidelines: ≤2 drinks/day for men → 14/week. A public reference
# number (like the DASH sodium targets), not a personal/physician-set value.
_ALCOHOL_WEEKLY_GUIDELINE = 14


def _render_lifestyle_section():
    """Alcohol from Garmin Lifestyle Logging: current-week + 4-week-avg metrics,
    a weekly units table (beer/wine/spirit/total), and a weekly-total bar chart
    against the public guideline line. Empty-safe.

    'Units' here = Garmin's summed serving counts (it logs drinks per category,
    not ABV-weighted units) — surfaced in the caption so the number isn't
    over-read as standardized UK units.
    """
    st.markdown("## Lifestyle — alcohol")
    df = lib.load_df(
        "SELECT date, subtype, amount FROM lifestyle_log "
        "WHERE name = 'Alcohol' AND source = 'garmin' ORDER BY date")
    if df.empty:
        st.caption("No alcohol logged yet — tracked via Garmin Connect Lifestyle "
                   "Logging (log drinks on the watch or in Garmin Connect).")
        return

    df["date"] = pd.to_datetime(df["date"])
    # Weeks start Monday; label each row by its Monday date.
    df["week"] = df["date"].dt.to_period("W-SUN").dt.start_time
    weekly_total = df.groupby("week")["amount"].sum()

    this_week = df["date"].max().to_period("W-SUN").start_time
    current = weekly_total.get(this_week, 0)
    prior4 = weekly_total[weekly_total.index < this_week].tail(4)
    avg4 = prior4.mean() if not prior4.empty else None

    c = st.columns(3)
    c[0].metric("This week (drinks)", f"{current:.0f}")
    c[1].metric("Prior 4-wk avg", f"{avg4:.1f}" if avg4 is not None else "—")
    c[2].metric("Guideline", f"≤{_ALCOHOL_WEEKLY_GUIDELINE}/wk")
    st.caption("Garmin logs a serving count per category (beer/wine/spirit); "
               "\"units\" here is the weekly sum of those counts, not ABV-weighted. "
               f"Reference line = US Dietary Guidelines ≤{_ALCOHOL_WEEKLY_GUIDELINE} "
               "drinks/week (men).")

    fig = go.Figure()
    fig.add_trace(go.Bar(x=weekly_total.index, y=weekly_total.values,
                         marker_color=lib.ACCENT, opacity=0.75))
    fig.add_hline(y=_ALCOHOL_WEEKLY_GUIDELINE, line_dash="dot", line_color=lib.WARN,
                  annotation_text=f"guideline ≤{_ALCOHOL_WEEKLY_GUIDELINE}/wk",
                  annotation_position="top left")
    st.plotly_chart(lib.apply_theme(fig, 220), use_container_width=True, key="cardio_alcohol")

    with st.expander("Weekly units table", expanded=True):
        pivot = (df.pivot_table(index="week", columns="subtype", values="amount",
                                aggfunc="sum", fill_value=0)
                 .sort_index(ascending=False))
        pivot["Total"] = pivot.sum(axis=1)
        pivot.index = pivot.index.strftime("Week of %Y-%m-%d")
        pivot.columns = [str(col).title() for col in pivot.columns]
        st.dataframe(pivot, use_container_width=True)


def render_cardiology():
    st.markdown(_CARD_CSS, unsafe_allow_html=True)
    st.markdown("# Cardiology")

    lip = br.lipids_df()

    # _frames() scans activity_streams/sleep/heart_rate/wellness/activities and
    # raises SystemExit("No data in DB.") — not just Exception — when none of
    # those tables have any rows yet (a freshly-initialized DB, day one before
    # any wearable sync). Catch both so that case degrades to skipping the
    # activity-history sections below rather than blanking the whole page:
    # goals/meds/BP/weight/body-comp only need CD + the new cardio tables.
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

    # ---- Cardiology goals -------------------------------------------------
    goals_html = _goals_strip_html(lip)
    if goals_html:
        st.markdown("## Goals")
        st.markdown(goals_html, unsafe_allow_html=True)

    # ---- Medications -------------------------------------------------------
    meds_html = _medications_html()
    if meds_html:
        st.markdown("## Medications")
        st.markdown(meds_html, unsafe_allow_html=True)
        statin_events = br.statin_events_df()
        if not statin_events.empty:
            with st.expander("Statin dose history"):
                st.dataframe(statin_events, use_container_width=True, hide_index=True)

    # ---- BP / weight / body-comp (Phase 3) --------------------------------
    _render_bp_section()
    _render_weight_section()
    _render_body_composition_section()
    _render_lifestyle_section()

    st.markdown(br.stat_cards_html(lip), unsafe_allow_html=True)

    if meta is None:
        st.info("No wearable activity history yet — the exercise/lipid overlay, quarterly "
                 f"summary, and detail charts need at least one day of synced data ({frames_error}).")
    else:
        end_ts = pd.Timestamp(meta["data_max"])

        # ---- headline overlay ------------------------------------------------
        st.markdown("## Lipid response to therapy × exercise")
        st.caption(f"Quarterly exercise intensity (bars, left axis), labeled LDL-C / ApoB draws "
                   f"(left axis, mg/dL), {CD.STATIN} dose step (right axis). Pink dotted "
                   "verticals = clinical events.")
        st.plotly_chart(br.exec_overlay(q, lip, end_ts), use_container_width=True, key="cardio_exec")

        # ---- quarterly summary table ----------------------------------------
        st.markdown("## Quarterly summary — therapy, labs & lifestyle")
        st.caption("One row per quarter; lab values are the last draw inside the quarter. "
                   "Red = above target. **Bold dose ↑** = changed during that quarter. "
                   "Grayed rows = no wearable data that quarter.")
        st.markdown(br.quarterly_table_html(q, lip), unsafe_allow_html=True)

        # ---- detailed charts -------------------------------------------------
        with st.expander("Detailed charts — weekly & quarterly trends", expanded=False):
            st.caption("Dashed lines on min/week charts = AHA guidelines: 75 vigorous / "
                       "150 moderate minimum, 300 goal.")
            for i, (label, fig) in enumerate(br.detail_figures(q, w, end_ts)):
                st.markdown(f"### {label}")
                st.plotly_chart(fig, use_container_width=True, key=f"cardio_detail_{i}")

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
                f"%HRmax bands off an empirical peak of {br.HRMAX} bpm, computed only for activities "
                f"with HR streams ({meta['acts_with_streams']} activities). This is NOT raw workout "
                f"duration — long easy hikes/skis are training volume, not intensity.\n"
                f"- **Activity↔lipid alignment.** Lipid draws span 2020→2026 but quantified activity "
                f"begins {meta['data_min']}; earlier quarters have no activity overlay.\n"
                f"- Lipid/statin/risk-marker values are transcribed from LabCorp reports — verify "
                f"against originals before any clinical decision.")
