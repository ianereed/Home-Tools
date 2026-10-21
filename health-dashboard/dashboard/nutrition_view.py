"""Nutrition page: what to eat, what you ate, and body composition.

The fuel planner (daily targets + meals from Garmin Coach, power-meter rides, MyFitnessPal
intake and the calendar; the latest run written by nutrition.refresh), plus the weight and
body-composition history and the diet research as reference.

Tabs: Today · Next 2 weeks · Body · History · Groceries · Reference. Read-only except the
"Refresh now" button, which only sets a flag the watcher job picks up.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import collectors.db as db
from dashboard import diet_content
from dashboard import lib
from dashboard import metrics as M
from dashboard import targets as T
from nutrition import fragments as H

TABS = ["Today", "Next 2 weeks", "Body", "History", "Groceries", "Reference"]
KG_LB = T.KG_TO_LB


# ------------------------------------------------------------------ data
def _connect():
    if not os.path.exists(db.DB_PATH):
        return None
    return sqlite3.connect(db.DB_PATH)


def latest_run_meta() -> dict | None:
    conn = _connect()
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT id, created_at, trigger, reason FROM fuel_plan_runs WHERE status='ok' "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        err = conn.execute("SELECT created_at, error FROM fuel_plan_runs WHERE status='error' ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not row:
        return None
    return {"id": row[0], "created_at": row[1], "trigger": row[2], "reason": row[3],
            "last_error": err if err and err[0] > row[1] else None}


@st.cache_data(show_spinner=False, max_entries=4)
def load_payload(run_id: int) -> dict:
    conn = _connect()
    try:
        return json.loads(conn.execute("SELECT payload_json FROM fuel_plan_runs WHERE id = ?", (run_id,)).fetchone()[0])
    finally:
        conn.close()


def _cardio_goals() -> dict | None:
    """Physician goals when the out-of-git clinical module is on this host; None elsewhere."""
    cardio = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cardiology")
    if os.path.exists(os.path.join(cardio, "clinical_data.py")) and cardio not in sys.path:
        sys.path.insert(0, cardio)
    try:
        import clinical_data as CD
    except ImportError:
        return None
    return getattr(CD, "CARDIO_GOALS", None)


def request_refresh() -> None:
    conn = _connect()
    if conn is None:
        return
    try:
        conn.execute("INSERT OR REPLACE INTO fuel_state (key, value, updated_at) VALUES ('refresh_requested', '1', ?)",
                     (datetime.now().isoformat(timespec="seconds"),))
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------ helpers
def _plan(p: dict) -> dict:
    return p["plans"].get("plan") or next(iter(p["plans"].values()))


def _rows(p: dict):
    """[(day, energy, meals_or_None, totals)] for the plan pace. Payload meals are [date, meals] pairs."""
    pl = _plan(p)
    n = len(p["days"])
    meals = [m[1] for m in pl["meals"]] if pl.get("meals") else [None] * n
    return list(zip(p["days"], pl["energy"], meals, pl.get("totals") or [{}] * n))


def _hm(m) -> str:
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def _sources_line(p: dict, meta: dict) -> str:
    bad = {k: v for k, v in p.get("sources", {}).items() if v not in ("ok", "cached", "off")}
    when = datetime.fromisoformat(meta["created_at"])
    age_h = (datetime.now() - when).total_seconds() / 3600
    parts = [f"Plan refreshed {when.strftime('%a %-I:%M %p')} ({meta['reason']})"]
    if age_h > 6:
        parts.append(f"⚠️ {age_h:.0f} h old")
    if bad:
        parts.append("⚠️ " + ", ".join(f"{k}: {v}" for k, v in bad.items()))
    return " · ".join(parts)


def _metrics(items: list[tuple[str, str, str]]):
    """A row of value cards: (label, value, sub-line)."""
    lib.cards([lib.card(label, value, note) for label, value, note in items])


def _day_card(day, e, meals, tot, open_: bool = False):
    label = (f"{H.fdate(day['date'])} · {e['category']} · {H.g(e['kcal'])} kcal · "
             f"C {e['carb_g']:.0f} / P {e['protein_g']:.0f} / F {e['fat_g']:.0f} g")
    if day["source"] in ("projected", "generated"):
        label += " · projected" if day["source"] == "projected" else " · planner ride"
    with st.expander(label, expanded=open_):
        chips = "".join(H.session_chip(s) for s in day["sessions"]) or '<span class="fuel-chip">Rest day</span>'
        events = "".join(f"<li>{H.esc(ev.get('start', 'all day') if not ev.get('all_day') else 'all day')} · "
                         f"{H.esc(ev['title'])}</li>" for ev in day.get("events", []))
        st.html(f"{chips}" + (f"<ul class='fuel-notes'>{events}</ul>" if events else "")
                + H.notes_html(day, e, meals)
                + f"<div class='fuel-tgt'>burn {H.g(e['tdee'])} (base + {H.g(e['ex_active'])} active − "
                  f"{H.g(e['displaced'])} displaced) · deficit {H.g(e['deficit'])}</div>")
        if meals:
            st.html(H.meals_html(meals))
            if tot:
                st.caption(f"As planned: {H.g(tot.get('kcal', 0))} kcal · fiber {tot.get('fiber', 0):.0f} g · "
                           f"sodium {H.g(tot.get('na', 0))} / {H.g(e.get('sodium_max_mg', 2300))} mg · "
                           f"cholesterol {H.g(tot.get('chol', 0))}"
                           f"{' / ' + H.g(e['chol_max_mg']) if e.get('chol_max_mg') else ''} mg · "
                           f"sat fat {tot.get('sat', 0):.0f} / {e.get('satfat_max_g', 0):.0f} g")


# ------------------------------------------------------------------ tabs
def _today(p: dict):
    today = date.today().isoformat()
    rows = {d["date"]: (d, e, m, t) for d, e, m, t in _rows(p)}
    if today not in rows:
        st.info("This plan run doesn't cover today yet; it will after the next refresh.")
        return
    day, e, meals, tot = rows[today]
    intake = (p.get("intake") or {}).get(today) or {}
    logged = intake.get("calories_kcal")
    _metrics([("Eat today", f"{H.g(e['kcal'])} kcal", f"burn {H.g(e['tdee'])}"),
              ("Logged so far", f"{H.g(logged)} kcal" if logged is not None else "—",
               f"via {intake.get('source')}" if intake else "nothing logged yet"),
              ("Left to eat", f"{H.g(e['kcal'] - (logged or 0))} kcal", ""),
              ("Day type", e["category"].title(), f"deficit {H.g(e['deficit'])}")])
    lg = e.get("logged_goal")
    if lg and lg["sessions_pending"]:
        done = (f"{H.g(lg['ex_net'])} kcal of completed exercise" if lg["sessions_done"] else "no exercise")
        st.caption(f"MyFitnessPal goal: {H.g(lg['kcal'])} kcal ({done} counted). It rises toward "
                   f"{H.g(e['kcal'])} as planned sessions sync from Garmin.")
    bars = [H.bar_row("Calories", logged, e["kcal"], "kcal"),
            H.bar_row("Carbs", intake.get("carbs_g"), e["carb_g"], "g"),
            H.bar_row("Protein", intake.get("protein_g"), e["protein_g"], "g"),
            H.bar_row("Fat", intake.get("fat_g"), e["fat_g"], "g"),
            H.bar_row("Sodium (limit)", intake.get("sodium_mg"), e.get("sodium_max_mg", 2300), "mg", ceiling=True),
            H.bar_row("Cholesterol" + (" (limit)" if e.get("chol_max_mg") else ""), intake.get("cholesterol_mg"),
                      e.get("chol_max_mg"), "mg", ceiling=True),
            H.bar_row("Sat fat (limit)", intake.get("saturated_fat_g"), e.get("satfat_max_g", 17), "g", ceiling=True),
            H.bar_row("Fiber (min)", intake.get("fiber_g"), e.get("fiber_target", 25), "g")]
    st.html('<div class="fuel-card">' + "".join(bars) + "</div>")
    if intake.get("updated_at"):
        st.caption(f"Intake synced {datetime.fromisoformat(intake['updated_at']).strftime('%-I:%M %p')} from MyFitnessPal. "
                   "Potassium isn't tracked as a target (shown in MFP only).")
    st.markdown("## Training")
    chips = "".join(H.session_chip(s) for s in day["sessions"]) or '<span class="fuel-chip">Rest day</span>'
    st.html(chips + H.notes_html(day, e, meals))
    for s in day["sessions"]:
        if s.get("completed"):
            src = s.get("energy_source", "")
            extra = f" · NP {s['np_w']:.0f} W (IF {s['if_np']:.2f})" if s.get("np_w") and s.get("if_np") else ""
            st.caption(f"{s['name']}: {H.g(s['active_kcal'])} active kcal from {src}{extra}. Targets above already use it.")
    lift = next((x for x in (p.get("strength") or {}).get("lifts", []) if x["date"] == today), None)
    if lift:
        _lift_block(lift, p)
    st.markdown("## Meals")
    if meals:
        st.html(H.meals_html(meals))


def _sync_status(lift: dict, p: dict) -> str:
    sync = (p.get("strength") or {}).get("sync") or {}
    mode = sync.get("mode", "off")
    op = next((o for o in sync.get("ops", []) if o["plan_key"] == lift["plan_key"]), None)
    if mode == "off":
        return "Garmin sync is off."
    if mode == "dry_run":
        return f"Dry run: would {op['op']} on Garmin." if op else "Dry run: already in sync."
    return f"On your Garmin calendar ({op['op']} this refresh)." if op else "On your Garmin calendar."


def _lift_block(lift: dict, p: dict):
    st.markdown("## Strength")
    rows = "".join(f"<li><span>{H.esc(x['label'])}</span><span class='q'>{x['sets']} × "
                   f"{(str(x['reps']) + ' reps') if x.get('reps') else (str(x['seconds']) + ' s')}"
                   f"{(' · ' + str(x['rir']) + ' RIR') if x.get('rir') else ''} · rest {x['rest_s']} s</span></li>"
                   for x in lift["exercises"])
    reasons = "".join(f"<li>{H.esc(r)}</li>" for r in lift.get("reasons", []))
    st.html(f"<div class='fuel-card'><div class='fuel-name'>{H.esc(lift['name'])} · {H._hm(lift['start'])}"
            f"</div><ul class='fuel-ing'>{rows}</ul><ul class='fuel-notes'>{reasons}</ul>"
            f"<div class='fuel-tgt'>{H.esc(_sync_status(lift, p))}</div></div>")


def _next(p: dict):
    rows = _rows(p)
    hist = p.get("history") or {}
    retro = (hist.get("retro") or {}).get("plan") or []
    fig = go.Figure()
    xs_r = [r["date"] for r in retro]
    fig.add_bar(x=xs_r, y=[r["tdee"] for r in retro], name="burn (actual rides)", marker_color=lib.SERIES["kcal"])
    nxt = rows[:14]
    fig.add_bar(x=[e["date"] for _, e, _, _ in nxt], y=[e["tdee"] for _, e, _, _ in nxt], name="burn (planned)",
                marker_color=lib.rgba(lib.SERIES["kcal"], 0.35))
    fig.add_scatter(x=[e["date"] for _, e, _, _ in nxt], y=[e["kcal"] for _, e, _, _ in nxt], name="eat target",
                    mode="lines+markers", line=dict(color=lib.INK, width=2))
    lib.chart(lib.apply_theme(fig, 260, legend=True), "fuel_energy")
    for i, (day, e, meals, tot) in enumerate(nxt):
        _day_card(day, e, meals, tot, open_=(i == 0))
    with st.expander("Through the end of the plan"):
        df = pd.DataFrame([{"date": e["date"], "type": e["category"], "source": d["source"],
                            "sessions": ", ".join(s["name"] for s in d["sessions"]) or "rest",
                            "eat kcal": round(e["kcal"]), "C g": round(e["carb_g"]), "P g": round(e["protein_g"]),
                            "F g": round(e["fat_g"]), "lb/wk": e.get("pace")} for d, e, _, _ in rows])
        st.dataframe(df, hide_index=True, use_container_width=True)


def _body(p: dict):
    dexa = p.get("dexa") or {}
    ftp = (p.get("athlete") or {}).get("ftp_w")
    w = M.weight_now()
    kg = w["lb"] / KG_LB if w else dexa.get("total_mass_kg")
    _metrics([("Weight", lib.weight_value(w) if w else "—", lib.weight_basis(w) if w else "no weigh-ins"),
              ("Body fat", f"{dexa.get('tissue_fat_pct')}%" if dexa.get("tissue_fat_pct") else "—",
               f"DEXA {dexa.get('date', '')}"),
              ("Lean mass", f"{dexa.get('lean_mass_kg', 0) * KG_LB:.0f} lb" if dexa.get("lean_mass_kg") else "—",
               f"DEXA {dexa.get('date', '')}"),
              ("FTP", f"{ftp:.0f} W" if ftp else "—", f"{ftp / kg:.2f} W/kg" if ftp and kg else "")])
    _weight_section()
    _body_composition_section()
    st.markdown("## Body-fat targets")
    sched = p.get("pace_schedule") or []
    st.caption("Pace schedule: " + " → ".join(f"{v} lb/wk from {H.fdate(d, '%b %-d')}" for d, v in sched))
    t_rows = []
    for t in p.get("targets", []):
        t_rows.append({"target": t["label"], "scale weight (lb)": round(t["total_lb"]),
                       "fat to lose (lb)": round(t["fat_loss_kg"] * KG_LB),
                       "W/kg at FTP": round(ftp / t["total_kg"], 2) if ftp else None,
                       "~weeks at 1 lb/wk": round(t["fat_loss_kg"] * KG_LB)})
    if t_rows:
        st.dataframe(pd.DataFrame(t_rows), hide_index=True, use_container_width=True)
    st.markdown("## Pace comparison (next 14 days)")
    cmp_rows = []
    for pace, plan in p["plans"].items():
        en = plan["energy"][:14]
        if not en:
            continue
        want = sum(e.get("pace", 0) for e in en) * 500
        got = sum(e["deficit"] for e in en)
        key = [e["carb_g_per_kg"] for e in en if e["category"] == "key"]
        cmp_rows.append({"pace": "plan schedule" if pace == "plan" else f"{pace} lb/wk",
                         "avg eat kcal": round(sum(e["kcal"] for e in en) / len(en)),
                         "deficit delivered": f"{100 * got / want:.0f}%" if want else "—",
                         "≈ lb/wk": round(got / 3500 / len(en) * 7, 2), "key-day carbs g/kg": round(min(key), 1) if key else None})
    st.dataframe(pd.DataFrame(cmp_rows), hide_index=True, use_container_width=True)
    st.markdown("## Calibration")
    wt = M.read("SELECT date(timestamp) AS d, AVG(weight_kg) AS kg FROM body_weight WHERE timestamp >= date('now','-28 days') "
                "GROUP BY d ORDER BY d")
    logged = {d: v for d, v in (p.get("intake") or {}).items() if v.get("calories_kcal")}
    if len(wt) >= 10 and len(logged) >= 10:
        span = (pd.to_datetime(wt["d"].iloc[-1]) - pd.to_datetime(wt["d"].iloc[0])).days or 1
        lb_wk = (wt["kg"].iloc[0] - wt["kg"].iloc[-1]) * KG_LB / span * 7
        mean_in = sum(v["calories_kcal"] for v in logged.values()) / len(logged)
        _metrics([("Measured maintenance", f"{H.g(mean_in + lb_wk * 500)} kcal/day",
                   f"{len(wt)} weigh-ins, {len(logged)} logged days")])
    else:
        st.caption(f"Needs ≥10 weigh-ins and ≥10 logged days in the last 4 weeks (now {len(wt)} and {len(logged)}). "
                   "Until then maintenance is the formula estimate (±200–300 kcal/day).")


def _weight_section():
    """Daily-mean weight with its 7-day average, DEXA scans, and the physician
    goal band when one is on file."""
    st.markdown("## Weight")
    raw = M.read("SELECT timestamp, weight_kg, source FROM body_weight ORDER BY timestamp")
    w = M.weight_daily()
    if w.empty:
        st.caption("No weigh-ins yet.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=w["date"], y=w["lb"], mode="markers", name="Daily mean",
                             marker=dict(color=lib.SERIES["weight"], size=5, opacity=0.5)))
    fig.add_trace(go.Scatter(x=w["date"], y=M.rolling_avg(w, "lb"), mode="lines", connectgaps=False,
                             name="7-day mean", line=dict(color=lib.SERIES["weight"], width=2)))
    fig.add_trace(go.Scatter(x=w["date"], y=M.rolling_low(w, "low"), mode="lines", connectgaps=False,
                             name="7-day low", line=dict(color=lib.SERIES["weight"], width=1.5, dash="dot")))
    dexa = raw[raw["source"] == "dexa"] if not raw.empty else raw
    if not dexa.empty:
        fig.add_trace(go.Scatter(x=pd.to_datetime(dexa["timestamp"]), y=dexa["weight_kg"] * KG_LB,
                                 mode="markers", name="DEXA",
                                 marker=dict(symbol="diamond", size=10, color=lib.INK)))
    goals = _cardio_goals()
    band, base = T.weight_band_lb(goals), T.weight_baseline_lb(goals)
    if band:
        fig.add_hrect(y0=band[0], y1=band[1], fillcolor=lib.GOOD, opacity=0.07, line_width=0,
                      annotation_text="goal", annotation_position="top left")
    if base:
        fig.add_hline(y=base, line_dash="dot", line_color=lib.MUTED, line_width=1,
                      annotation_text="baseline", annotation_position="bottom right")
    # open on recent months; older (sparse, pre-scale) readings are a zoom-out away
    end = pd.Timestamp(date.today())
    fig.update_xaxes(range=[end - pd.Timedelta(days=120), end + pd.Timedelta(days=3)])
    recent = w[w["date"] >= end - pd.Timedelta(days=120)]
    if not recent.empty:
        lo_y = min(recent["low"].min(), band[0] if band else recent["low"].min())
        hi_y = max(recent["lb"].max(), base or recent["lb"].max())
        fig.update_yaxes(range=[lo_y - 2, hi_y + 2])
    lib.chart(lib.apply_theme(fig, 260, legend=True), "nu_weight")
    st.caption("Solid = 7-day mean of daily averages; dotted = lowest single reading in the trailing "
               "7 days, the closest to true weight (least water, food and clothing). A dehydrated "
               "post-ride weigh-in can pull the low down.")


def _body_composition_section():
    """BIA (Garmin scale) and DEXA as separate traces: different measurement
    physics, never merged into one series. Rendered only when there are rows."""
    bc = M.read("SELECT timestamp, body_fat_pct, lean_mass_kg, visceral_fat_rating, "
                "visceral_fat_mass_kg, source FROM body_composition ORDER BY timestamp")
    if bc.empty:
        return
    st.markdown("## Body composition")
    bc["timestamp"] = pd.to_datetime(bc["timestamp"])
    st.caption("BIA (Garmin scale) and DEXA measure body fat with different physics and "
               "are not directly comparable — shown as separate traces, never merged.")
    cc = st.columns(2)
    with cc[0]:
        st.markdown("### Body fat %")
        fig = go.Figure()
        for src, name, mode, marker in (("garmin", "BIA (Garmin scale)", "lines+markers", dict(size=5)),
                                        ("dexa", "DEXA", "markers", dict(size=11, symbol="diamond"))):
            d = bc[(bc["source"] == src) & bc["body_fat_pct"].notna()]
            if not d.empty:
                fig.add_trace(go.Scatter(x=d["timestamp"], y=d["body_fat_pct"], mode=mode, name=name,
                                         line=dict(color=lib.SERIES["bodyfat"]),
                                         marker=dict(color=lib.SERIES["bodyfat"], **marker)))
        lib.chart(lib.apply_theme(fig, 200, legend=True), "nu_bodyfat")
    with cc[1]:
        st.markdown("### Lean mass (lb)")
        fig = go.Figure()
        for src, name, mode, marker in (("garmin", "BIA (Garmin scale)", "lines+markers", dict(size=5)),
                                        ("dexa", "DEXA", "markers", dict(size=11, symbol="diamond"))):
            d = bc[(bc["source"] == src) & bc["lean_mass_kg"].notna()]
            if not d.empty:
                fig.add_trace(go.Scatter(x=d["timestamp"], y=d["lean_mass_kg"] * KG_LB, mode=mode, name=name,
                                         line=dict(color=lib.SERIES["lean"]),
                                         marker=dict(color=lib.SERIES["lean"], **marker)))
        lib.chart(lib.apply_theme(fig, 200, legend=True), "nu_lean")
    dexa = bc[bc["source"] == "dexa"]
    if not dexa.empty:
        with st.expander("DEXA history"):
            t = dexa.copy()
            t["timestamp"] = t["timestamp"].dt.strftime("%Y-%m-%d")
            st.dataframe(t[["timestamp", "body_fat_pct", "lean_mass_kg", "visceral_fat_mass_kg"]],
                         use_container_width=True, hide_index=True)


def baseline_summary(intake: dict, retro: list[dict], today: str, kg: float | None) -> dict | None:
    """Averages over fully past logged days: what you actually eat vs the plan's targets/limits."""
    tgt = {r["date"]: r for r in retro}
    sodium_lim = T.SODIUM_MG_CEILING
    days = sorted(d for d, v in intake.items() if d < today and v.get("calories_kcal"))
    if not days:
        return None

    def avg(key, src=intake):
        vals = [src[d].get(key) for d in days if src.get(d, {}).get(key) is not None]
        return sum(vals) / len(vals) if vals else None
    kcal = avg("calories_kcal")
    p, c, f = avg("protein_g"), avg("carbs_g"), avg("fat_g")
    tdays = [d for d in days if d in tgt]
    t = {k: (sum(tgt[d][k] for d in tdays) / len(tdays) if tdays else None)
         for k in ("kcal", "tdee", "protein_g", "carb_g", "fat_g")}
    fib = [tgt[d].get("fiber_target") for d in tdays if tgt[d].get("fiber_target")]
    t["fiber"] = sum(fib) / len(fib) if fib else None
    chol_lim = next((tgt[d]["chol_max_mg"] for d in tdays if tgt[d].get("chol_max_mg")), None)  # None = display-only
    split = None
    if p is not None and c is not None and f is not None and kcal:
        e = 4 * p + 4 * c + 9 * f
        split = (round(100 * 4 * c / e), round(100 * 4 * p / e), round(100 * 9 * f / e))
    return {"n": len(days), "first": days[0], "last": days[-1], "kcal": kcal, "protein": p, "carbs": c, "fat": f,
            "sat": avg("saturated_fat_g"), "fiber": avg("fiber_g"), "sodium": avg("sodium_mg"),
            "chol": avg("cholesterol_mg"), "protein_per_kg": p / kg if p and kg else None, "split_cpf": split,
            "target": t, "days_over_sodium": sum(1 for d in days if (intake[d].get("sodium_mg") or 0) > sodium_lim),
            "chol_limit": chol_lim,
            "days_over_chol": (sum(1 for d in days if (intake[d].get("cholesterol_mg") or 0) > chol_lim)
                               if chol_lim else None)}


def _baseline(p: dict):
    st.markdown("## Baseline: what you eat vs the plan")
    retro = ((p.get("history") or {}).get("retro") or {}).get("plan") or []
    kg = p.get("weight_kg") or (p.get("dexa") or {}).get("total_mass_kg")
    b = baseline_summary(p.get("intake") or {}, retro, date.today().isoformat(), kg)
    if not b:
        st.caption("No complete logged days yet. Log everything you eat in MyFitnessPal; averages appear here from tomorrow.")
        return
    t = b["target"]

    def fmt(v, unit="", nd=0):
        return "—" if v is None else f"{v:,.{nd}f}{unit}"
    rows = [("Calories", fmt(b["kcal"]), fmt(t["kcal"]), f"burn ≈ {fmt(t['tdee'])}"),
            ("Protein", fmt(b["protein"], " g"), fmt(t["protein_g"], " g"), f"{fmt(b['protein_per_kg'], ' g/kg', 1)}"),
            ("Carbs", fmt(b["carbs"], " g"), fmt(t["carb_g"], " g"), ""),
            ("Fat", fmt(b["fat"], " g"), fmt(t["fat_g"], " g"), f"sat {fmt(b['sat'], ' g')}"),
            ("Fiber", fmt(b["fiber"], " g"), f"≥ {fmt(t.get('fiber'), ' g')}", "14 g per 1,000 kcal"),
            ("Sodium (net of ride sweat)", fmt(b["sodium"], " mg"), f"≤ {T.SODIUM_MG_CEILING:,} mg",
             f"{b['days_over_sodium']} of {b['n']} days over"),
            ("Cholesterol", fmt(b["chol"], " mg"), f"≤ {fmt(b['chol_limit'], ' mg')}" if b["chol_limit"] else "no limit",
             f"{b['days_over_chol']} of {b['n']} days over" if b["chol_limit"] else "for reference")]
    st.dataframe(pd.DataFrame(rows, columns=["daily average", "you", "plan", "note"]), hide_index=True,
                 use_container_width=True)
    split = b["split_cpf"]
    st.caption(f"{b['n']} logged day(s), {H.fdate(b['first'], '%b %-d')}–{H.fdate(b['last'], '%b %-d')}"
               + (f" · carbs/protein/fat split {split[0]}/{split[1]}/{split[2]} % of calories" if split else "")
               + ". Today is excluded until it's over. Days with nothing logged don't count.")


def _history(p: dict):
    hist = p.get("history") or {}
    rides = hist.get("rides") or []
    if rides:
        st.markdown("## Rides: power meter vs Garmin")
        st.dataframe(pd.DataFrame([{
            "date": r["date"], "activity": r["name"], "min": round(r["session"]["minutes"]),
            "avg / NP W": f"{r['avg_power_w']:.0f} / {r['np_w']:.0f}" if r.get("avg_power_w") else "no power",
            "active kcal (plan uses)": round(r["session"]["active_kcal"]), "Garmin active": round(r["session"]["garmin_active_kcal"]),
            "source": r["session"]["energy_source"]} for r in rides]), hide_index=True, use_container_width=True)
    retro = (hist.get("retro") or {}).get("plan") or []
    intake = p.get("intake") or {}
    if retro:
        st.markdown("## Eaten vs target, last 2 weeks")
        st.dataframe(_numeric(pd.DataFrame([{
            "date": r["date"], "burn": round(r["tdee"]), "target": round(r["kcal"]),
            "eaten": round(intake[r["date"]]["calories_kcal"]) if intake.get(r["date"], {}).get("calories_kcal") else None,
            f"sodium mg (≤{T.SODIUM_MG_CEILING:,})": intake.get(r["date"], {}).get("sodium_mg"),
            "fiber g": intake.get(r["date"], {}).get("fiber_g"),
            "fiber target": round(r["fiber_target"]) if r.get("fiber_target") else None,
            "sat fat g": intake.get(r["date"], {}).get("saturated_fat_g"),
            "sat fat max": round(r["satfat_max_g"]) if r.get("satfat_max_g") else None,
            "chol mg": intake.get(r["date"], {}).get("cholesterol_mg"),
            "logged in": intake.get(r["date"], {}).get("source")} for r in retro])),
            hide_index=True, use_container_width=True)
    _baseline(p)
    _alcohol_section()
    diffs = M.read("SELECT detected_at, date, change, before_json, after_json FROM coach_diffs ORDER BY id DESC LIMIT 30")
    st.markdown("## Garmin Coach changes")
    if diffs.empty:
        st.caption("No Coach changes detected yet. Every refresh compares Coach's window with the previous one; "
                   "this is where the lift-adaptation experiment shows up.")
    else:
        def _name(j):
            if not j:
                return "—"
            w = json.loads(j)
            return "rest" if w.get("rest") else f"{w.get('name')} {round((w.get('duration_s') or 0) / 60)} min"
        st.dataframe(pd.DataFrame([{"seen": r.detected_at[:16], "day": r.date, "change": r.change,
                                    "before": _name(r.before_json), "after": _name(r.after_json)} for r in diffs.itertuples()]),
                     hide_index=True, use_container_width=True)
    stg = p.get("strength") or {}
    if stg:
        st.markdown("## Strength")
        ratio = stg.get("acwr")
        st.caption(f"7-day : 28-day load ratio {ratio:.2f}" if ratio else "Load ratio needs 4 weeks of rides.")
        st.caption("Completed lifts this and last week: " + (", ".join(c["date"] for c in stg.get("completed", [])) or "none")
                   + " · progress " + ", ".join(f"{k}: {v}" for k, v in (stg.get("progress") or {}).items()))
        ops = M.read("SELECT ts, mode, op, date, ok, detail FROM fuel_garmin_ops ORDER BY id DESC LIMIT 20")
        if not ops.empty:
            st.dataframe(ops, hide_index=True, use_container_width=True)
    st.markdown("## Data issues")
    issues = [f"{r['date']} {r['name']}: no power recorded (Garmin estimate used)" for r in hist.get("no_power", [])]
    src = p.get("sources", {})
    if src.get("mfp") == "auth_expired":
        issues.append("MyFitnessPal session expired: re-run the mfp-mcp auth step on the Mac mini.")
    if not M.read("SELECT 1 FROM body_weight WHERE timestamp >= date('now','-10 days') LIMIT 1").shape[0]:
        issues.append("No weigh-ins in the last 10 days (targets use the last body-composition scan).")
    st.html("<ul class='fuel-notes'>" + "".join(f"<li>{H.esc(i)}</li>" for i in issues) + "</ul>" if issues
            else "<p class='fuel-tgt'>None.</p>")


def _numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Numeric columns as numbers so missing values show blank, not "None"."""
    for c in df.columns:
        if c not in ("date", "logged in"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _alcohol_section():
    """Weekly drinks from Garmin Lifestyle Logging against the guideline. The
    current-week number vs target is on Goals."""
    st.markdown("## Alcohol")
    df = M.alcohol_log()
    if df.empty:
        st.caption("No alcohol logged yet — tracked via Garmin Connect Lifestyle Logging.")
        return
    weekly = M.alcohol_weekly()
    fig = go.Figure(go.Bar(x=weekly.index, y=weekly.values, marker_color=lib.SERIES["alcohol"], opacity=0.8))
    fig.add_hline(y=T.ALCOHOL_DRINKS_WK, line_dash="dot", line_color=lib.MUTED,
                  annotation_text=f"guideline ≤{T.ALCOHOL_DRINKS_WK}/wk", annotation_position="top left",
                  annotation_font_color=lib.MUTED)
    lib.chart(lib.apply_theme(fig, 200), "nu_alcohol")
    st.caption("Garmin logs a serving count per category (beer/wine/spirit), not ABV-weighted units. "
               f"Reference line = US Dietary Guidelines ≤{T.ALCOHOL_DRINKS_WK} drinks/week (men).")
    with st.expander("Weekly drinks by type"):
        pivot = (df.assign(week=M.week_start(df["date"]))
                   .pivot_table(index="week", columns="subtype", values="amount", aggfunc="sum", fill_value=0)
                   .sort_index(ascending=False))
        pivot["Total"] = pivot.sum(axis=1)
        pivot.index = pivot.index.strftime("Week of %Y-%m-%d")
        pivot.columns = [str(c).title() for c in pivot.columns]
        st.dataframe(pivot, use_container_width=True)


def _reference(p: dict):
    st.markdown(diet_content.STARTING_APPROACH)
    st.caption(diet_content.ACTIVITY_GUIDELINES)
    st.markdown("## Diet research")
    for i, sec in enumerate(diet_content.SECTIONS):
        with st.expander(sec["title"], expanded=(i == 0)):
            st.markdown(sec["body_md"])
            for point in sec["key_points"]:
                st.markdown(f"- {point}")
            st.caption("Sources")
            st.markdown("\n".join(f"- [{label}]({url})" for label, url in sec["sources"]))


def _groceries(p: dict):
    weeks = _plan(p).get("grocery_by_week") or {}
    if not weeks:
        st.caption("No grocery list in this run.")
        return
    keys = sorted(weeks)
    labels = {k: f"Week of {H.fdate(k, '%b %-d')}" for k in keys}
    pick = st.selectbox("Week", keys, format_func=lambda k: labels[k], key="fuel_groc_week")
    st.caption("Home-cooked meals only (eating out and trip days excluded). Cooked weights converted to raw/dry purchase amounts.")
    st.html(H.grocery_html(weeks[pick]))


# ------------------------------------------------------------------ page
def render_nutrition():
    st.html(H.CSS)
    lib.page_header("Nutrition", "What to eat, what you ate, and body composition. "
                    "Intake from MyFitnessPal, plan from Garmin Coach and the power meter.")
    meta = latest_run_meta()
    if meta is None:
        st.info("No fuel plan yet. It appears after the first `python -m nutrition.refresh` run on the Mac mini.")
        tabs = st.tabs(["Body", "Reference"])
        with tabs[0]:
            _weight_section()
            _body_composition_section()
        with tabs[1]:
            _reference({})
        return
    p = load_payload(meta["id"])
    top = st.columns([5, 1])
    top[0].caption(_sources_line(p, meta))
    if top[1].button("Refresh now", key="fuel_refresh", use_container_width=True):
        request_refresh()
        st.toast("Refresh requested; the watcher picks it up within 15 minutes.")
    if meta.get("last_error"):
        st.warning(f"Last refresh failed at {meta['last_error'][0][:16]}: {meta['last_error'][1][:160]}. Showing the previous plan.")
    tabs = st.tabs(TABS)
    for tab, fn in zip(tabs, (_today, _next, _body, _history, _groceries, _reference)):
        with tab:
            fn(p)
