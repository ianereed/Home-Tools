"""Printable goals report for the cardiologist and primary-care provider.

The Goals page as a static, print-ready HTML document — the same numbers
(dashboard.metrics), targets (dashboard.targets) and charts (goals_view's
builders, re-themed for paper) — followed by an appendix of the underlying
tables. Print it to PDF from a browser, or run with --pdf.

    .venv/bin/python -m dashboard.report [--out PATH] [--pdf]

The output carries PHI (name, DOB, labs, medications): it defaults to the
gitignored cardiology/out/ directory. This module holds no personal values;
everything clinical is read from cardiology/clinical_data.py at run time.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dashboard import goals_view as G          # noqa: E402  (also loads clinical_data, build_report)
from dashboard import lib                      # noqa: E402
from dashboard import metrics as M             # noqa: E402
from dashboard import targets as T             # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cardiology", "out")
CHART_WIDTH = 720       # px: a US Letter page with ~0.5 in margins
CHART_LEFT = 44         # px: y tick labels

# Dashboard colors that vanish or wash out on white paper -> print equivalents.
_PRINT_COLORS = {
    lib.INK: "#111827", lib.MUTED: "#4b5563", lib.GOOD: "#15803d", lib.GRID: "#e5e7eb",
    lib.ZONE_COLORS["Z3"]: "#a16207", lib.ZONE_COLORS["Z4"]: "#c2410c",
    lib.SERIES["ldl"]: "#be123c", lib.SERIES["apob"]: "#b45309", lib.SERIES["sys"]: "#0369a1",
    lib.SERIES["dia"]: "#a21caf", lib.SERIES["weight"]: "#92400e", lib.SERIES["bodyfat"]: "#db2777",
    lib.SERIES["sodium"]: "#334155", lib.SERIES["alcohol"]: "#7e22ce", "#e5e7eb": "#e5e7eb",
}
_PRINT_COLORS = {k.lower(): v for k, v in _PRINT_COLORS.items()}


def _recolor(obj):
    if isinstance(obj, dict):
        return {k: _recolor(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_recolor(v) for v in obj]
    if isinstance(obj, str):
        hit = _PRINT_COLORS.get(obj.lower())
        if hit:
            return hit
        m = re.fullmatch(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", obj.replace(" ", ""))
        if m:   # rgba() built from a palette hex (e.g. a shaded band)
            hx = "#{:02x}{:02x}{:02x}".format(*map(int, m.groups()[:3]))
            if hx in _PRINT_COLORS:
                p = _PRINT_COLORS[hx].lstrip("#")
                return f"rgba({int(p[0:2], 16)},{int(p[2:4], 16)},{int(p[4:6], 16)},{m.group(4)})"
    return obj


def for_print(fig: go.Figure, height: int, x_end=None) -> go.Figure:
    """A dashboard figure re-themed for white paper; x_end trims the empty
    future the dashboard keeps for its pace/ongoing lines."""
    fig = go.Figure(_recolor(fig.to_plotly_json()))
    if x_end is not None:
        for k in list(fig.layout):
            ax = fig.layout[k] if k.startswith("xaxis") else None
            if ax is not None and ax.range:
                ax.range = [ax.range[0], x_end]
    fig.update_layout(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                      font=dict(color="#111827", size=11), width=CHART_WIDTH, height=height,
                      legend=dict(font=dict(color="#111827")))
    fig.update_xaxes(gridcolor="#e5e7eb", linecolor="#9ca3af")
    fig.update_yaxes(gridcolor="#e5e7eb", linecolor="#9ca3af", automargin=False)
    # fixed side margins so every chart's plot area spans the same columns;
    # goal labels and medication lane names sit in the right one
    fig.update_layout(margin=dict(l=CHART_LEFT, r=G._GUIDE_MARGIN))
    return fig


def _chart(fig, height, x_end=None) -> str:
    if fig is None:
        return '<p class="muted">No data yet.</p>'
    return for_print(fig, height, x_end).to_html(full_html=False, include_plotlyjs=False,
                                          config={"staticPlot": True, "displayModeBar": False})


# --- small formatting helpers --------------------------------------------------

def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _num(v, fmt="{:.0f}", dash="—") -> str:
    return dash if v is None or pd.isna(v) else fmt.format(v)


def _left(gap, fmt="{:.1f}", unit="") -> str:
    if gap is None or pd.isna(gap) or gap <= 0:
        return "Complete"
    return f"{fmt.format(gap)} {unit} to go".replace("  ", " ")


def _table(head: list[str], rows: list[list], cls: str = "") -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def _status(text: str) -> str:
    cls = "ok" if text in ("Complete", "Meets AHA", "Under limit", "Under guideline") else "todo"
    return f'<span class="st {cls}">{esc(text)}</span>'


# --- sections -------------------------------------------------------------------

def _summary(goals, lip, today) -> str:
    """One row per goal: target, who set it, current value, status, basis."""
    who = esc((goals or {}).get("set_by") or "Physician").split(" (")[0]
    rows = []
    goal_ldl = T.ldl_goal(goals)
    if not lip.empty and goal_ldl:
        last = lip.iloc[-1]
        rows.append(["LDL-C", f"&lt; {goal_ldl} mg/dL", who, f"{last['ldl']:.0f} mg/dL",
                     _status("Complete" if last["ldl"] < goal_ldl else _left(last["ldl"] - goal_ldl + 1, "{:.0f}", "mg/dL")),
                     f"Lab {last['date']:%b %-d, %Y}"])
    win = M.bp_window(14)
    sg, dg = T.bp_goal(goals)
    if win:
        left = [f"{n} {max(round(c) - g + 1, 0)}" for n, c, g in (("sys", win["sys"], sg), ("dia", win["dia"], dg))
                if round(c) >= g]
        rows.append(["Blood pressure", f"&lt; {sg}/{dg} mmHg", who, f"{win['sys']:.0f}/{win['dia']:.0f} mmHg",
                     _status("Complete" if not left else " · ".join(left) + " mmHg to go"),
                     f"14-day mean, {win['n']} home readings"])
    w = M.weight_now(today)
    wg = M.weight_goals()
    scans = M.dexa_scans()
    if w and wg and not scans.empty:
        g, s_ = wg[0], scans.iloc[-1]
        lb_left, pts_left = w["lb"] - g["lb"], s_["body_fat_pct"] - g["pct"] * 100
        wdoc = (goals or {}).get("weight") or {}
        setby = "Patient" + (f"<br><small>{who}: lose {wdoc['lose_lb']:g} lb ({pd.Timestamp(wdoc['set_on']):%b %Y})</small>"
                             if wdoc.get("lose_lb") and wdoc.get("set_on") else "")
        rows.append(["Weight", f"≤ {g['lb']:.1f} lb", setby,
                     f"{w['lb']:.1f} lb",
                     _status("Complete" if lb_left <= 0 else _left(lb_left, "{:.1f}", "lb")), "7-day mean"])
        rows.append(["Body fat", f"≤ {g['pct'] * 100:.0f}%", "Patient", f"{s_['body_fat_pct']:.1f}%",
                     _status("Complete" if pts_left <= 0 else _left(pts_left, "{:.1f}", "pts")),
                     f"DEXA {s_['date']:%b %-d, %Y}"])
    ex = M.exercise_summary(today)
    if ex:
        rows.append(["Exercise", f"≥ {T.AHA_MODERATE_MIN_WK} min/wk moderate or ≥ {T.AHA_VIGOROUS_MIN_WK} vigorous (AHA)",
                     "Patient", f"{ex['modvig']:.0f} / {ex['vig']:.0f} min/wk",
                     _status("Meets AHA" if ex["meets_aha"] else "Below AHA"), "4-week average, HR zones"])
    na = M.sodium_week(today)
    if na:
        rows.append(["Sodium", f"≤ {T.SODIUM_MG_CEILING:,} mg/day (DASH)", "Patient", f"{na['mean']:,.0f} mg/day",
                     _status("Under limit" if na["mean"] <= T.SODIUM_MG_CEILING else "Over limit"),
                     f"7-day average, {na['days']} days logged"])
    al = M.alcohol_summary(today)
    if al:
        rows.append(["Alcohol", f"≤ {T.ALCOHOL_DRINKS_WK} drinks/wk", "Patient",
                     f"{al['this_week']:.0f} this week · {al['prior4_avg']:.0f}/wk prior 4 wk",
                     _status("Under guideline" if max(al["this_week"], al["prior4_avg"]) <= T.ALCOHOL_DRINKS_WK
                             else "Over guideline"), "Calendar weeks"])
    return _table(["Goal", "Target", "Set by", "Current", "Status", "Basis"], rows, "summary")


def _timing(m: dict) -> str:
    hist = m.get("dosing_history") or []
    if len(hist) >= 2:
        return esc(m.get("frequency", "")) + "<br><small>" + " → ".join(
            f"{esc(t)} from {esc(d)}" for d, t in hist) + "</small>"
    return esc(m.get("frequency", ""))


def _medications() -> str:
    meds = [m for m in getattr(G.CD, "MEDICATIONS", [])
            if not m.get("stop") and not (m.get("status") or "").lower().startswith("discontinued")]
    rows = [[f"<b>{esc(m.get('brand') or m.get('name'))}</b> <small>{esc(m.get('name'))}</small>",
             esc(m.get("dose")), _timing(m), esc(m.get("start") or "—")]
            for m in meds]
    return _table(["Medication", "Dose", "Schedule", "Started"], rows)


# --- appendix tables ----------------------------------------------------------------

def _appendix_lipids(lip) -> str:
    rows = [[f"{r['date']:%Y-%m-%d}", f"{int(r['statin_dose_mg'])} mg" if r["statin_dose_mg"] else "—",
             _num(r["total_chol"]), _num(r["trig"]), _num(r["hdl"]), f"<b>{_num(r['ldl'])}</b>",
             _num(r["apob"]), _num(r["lpa_nmol_l"], "{:g}"), esc(r["note"] if pd.notna(r["note"]) else "")]
            for _, r in lip.iloc[::-1].iterrows()]
    return _table(["Date", "Statin", "TC", "TG", "HDL", "LDL-C", "ApoB", "Lp(a)", "Context"], rows, "dense")


def _appendix_bp(today) -> tuple[str, str]:
    bp = M.bp_readings()
    if bp.empty:
        return "", ""
    b = bp[bp["timestamp"] >= G.TRACKER_START].copy()
    b["week"] = M.week_start(b["timestamp"])
    b["am"] = b["timestamp"].dt.hour < 12
    rows = []
    for wk, g in sorted(b.groupby("week"), key=lambda t: t[0], reverse=True):
        am, pm = g[g["am"]], g[~g["am"]]
        mm = (lambda d: f"{d['systolic'].mean():.0f}/{d['diastolic'].mean():.0f} ({len(d)})" if len(d) else "—")
        rows.append([f"{wk:%b %-d, %Y}", f"<b>{g['systolic'].mean():.0f}/{g['diastolic'].mean():.0f}</b>",
                     len(g), mm(am), mm(pm)])
    weekly = _table(["Week of", "Mean", "Readings", "Morning (n)", "Afternoon/evening (n)"], rows, "dense")
    recent = bp[bp["timestamp"] >= pd.Timestamp(datetime.now()) - pd.Timedelta(days=14)].iloc[::-1]
    rows = [[f"{r['timestamp']:%a %b %-d, %H:%M}", f"{int(r['systolic'])}/{int(r['diastolic'])}",
             _num(r["pulse"]), M.bp_category(r["systolic"], r["diastolic"])[0]]
            for _, r in recent.iterrows()]
    readings = _table(["Time", "BP (mmHg)", "Pulse", "AHA category"], rows, "dense")
    return weekly, readings


def _appendix_weight(today) -> str:
    wk = M.weight_weekly(G.TRACKER_START.date().isoformat(), as_of_today=True, today=today)
    rows = [[f"{r['week']:%b %-d, %Y}", f"<b>{r['lb']:.1f}</b>", int(r["n"])]
            for _, r in wk.iloc[::-1].iterrows() if pd.notna(r["lb"])]
    return _table(["Week of", "7-day mean (lb)", "Weigh-in days"], rows, "dense")


def _appendix_dexa() -> str:
    """Each DEXA scan: whole-body composition, then regional fat % and visceral fat."""
    scans = M.dexa_scans()
    details = {d["date"]: d for d in getattr(G.CD, "DEXA_DETAILS", [])}
    out = []
    for _, s in scans.iloc[::-1].iterrows():
        d = details.get(f"{s['date']:%Y-%m-%d}", {})
        out.append(f"<h3>{s['date']:%b %-d, %Y}" + (f" · {esc(d.get('provider'))}" if d.get("provider") else "")
                   + "</h3>")
        whole = [["Total mass", f"{_num(s['weight'], '{:.1f}')} lb"],
                 ["Body fat (tissue)", f"<b>{_num(s['body_fat_pct'], '{:.1f}')}%</b>"],
                 ["Body fat (region)", f"{_num(d.get('region_fat_pct'), '{:.1f}')}%" if d.get("region_fat_pct") else "—"],
                 ["Fat mass", f"{_num(s['fat'], '{:.1f}')} lb"],
                 ["Lean mass", f"{_num(s['lean'], '{:.1f}')} lb"],
                 ["Bone mineral content", f"{_num(s['bone'], '{:.1f}')} lb"]]
        vat = d.get("vat_mass_kg")
        whole.append(["Visceral fat (VAT)",
                      (f"{vat * T.KG_TO_LB:.1f} lb" + (f" · {d['vat_volume_cm3']:,.0f} cm³" if d.get("vat_volume_cm3") else ""))
                      if vat else (f"{_num(s['visceral'], '{:.1f}')} lb" if pd.notna(s["visceral"]) else "—")])
        regions = [(k, d.get(f"{k.lower()}_fat_pct")) for k in ("Android", "Gynoid", "Trunk", "Arms", "Legs")]
        reg_rows = [[k, f"{v:.1f}%"] for k, v in regions if v is not None]
        if d.get("android_fat_pct") and d.get("gynoid_fat_pct"):
            reg_rows.insert(2, ["Android/gynoid ratio", f"{d['android_fat_pct'] / d['gynoid_fat_pct']:.2f}"])
        out.append('<div class="two">' + _table(["Whole body", ""], whole, "dense")
                   + (_table(["Regional fat", ""], reg_rows, "dense") if reg_rows else "") + "</div>")
    return "".join(out) or '<p class="muted">No DEXA scans.</p>'


def _appendix_levers(today) -> str:
    zw = M.zone_minutes_weekly(G.TRACKER_START.date().isoformat()).set_index("week")
    na = G.sodium_weekly(today)
    al = G.alcohol_weekly_filled(today)
    weeks = sorted(set(zw.index) | set(na.index) | set(al.index), reverse=True)
    rows = []
    for wk in weeks:
        rows.append([f"{wk:%b %-d, %Y}",
                     _num(zw["modvig"].get(wk)) if wk in zw.index else "—",
                     _num(zw["vig"].get(wk)) if wk in zw.index else "—",
                     (f"{na.loc[wk, 'mean']:,.0f} ({int(na.loc[wk, 'count'])} d)"
                      if wk in na.index and pd.notna(na.loc[wk, "mean"]) else "—"),
                     _num(al.get(wk)) if wk in al.index else "—"])
    return _table(["Week of", "Z3–5 min", "Z4–5 min", "Sodium mg/day (days logged)", "Drinks"], rows, "dense")


def _appendix_med_history() -> str:
    meds = sorted(getattr(G.CD, "MEDICATIONS", []), key=lambda m: m.get("start") or "9999")
    rows = [[f"{esc(m.get('brand') or m.get('name'))} <small>({esc(m.get('name'))})</small>", esc(m.get("dose")),
             _timing(m), esc(m.get("start") or "—"), esc(m.get("stop") or "")]
            for m in meds]
    statin = [[esc(d), f"{mg} mg"] for d, mg, _ in getattr(G.CD, "STATIN_EVENTS", [])]
    return (_table(["Medication", "Dose", "Schedule", "Start", "Stop"], rows, "dense")
            + '<div class="keep">'
            + f"<h4>{esc(getattr(G.CD, 'STATIN', 'Statin')).capitalize()} dose history</h4>"
            + _table(["Date", "Dose"], statin, "dense compact") + "</div>")


def _appendix_events() -> str:
    ev = sorted(getattr(G.CD, "EVENTS", []), key=lambda e: e[0], reverse=True)
    return _table(["Date", "Event"], [[f"{pd.Timestamp(d):%b %-d, %Y}", esc(lbl)] for d, lbl, _ in ev], "dense compact")


_CSS = """
@page { size: Letter; margin: 13mm 12mm 14mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #111827; margin: 0;
  font-size: 10.5pt; line-height: 1.4; background: #fff; }
.page { max-width: 760px; margin: 0 auto; padding: 18px 12px; }
header { border-bottom: 2px solid #111827; padding-bottom: 8px; margin-bottom: 12px; }
h1 { font-size: 17pt; margin: 0 0 2px; }
.who { font-size: 10pt; color: #374151; }
.who b { color: #111827; }
h2 { font-size: 12.5pt; margin: 18px 0 6px; padding-bottom: 2px; border-bottom: 1px solid #d1d5db;
  break-after: avoid; }
h3 { font-size: 10.5pt; margin: 12px 0 2px; break-after: avoid; }
h4 { font-size: 9.5pt; margin: 10px 0 2px; break-after: avoid; }
.keep { break-inside: avoid; }
.note, .muted { font-size: 8.5pt; color: #6b7280; margin: 2px 0 8px; }
table { border-collapse: collapse; width: 100%; margin: 4px 0 8px; font-size: 9pt; }
th, td { border-bottom: 1px solid #e5e7eb; padding: 3px 6px; text-align: left; vertical-align: top; }
th { background: #f3f4f6; font-weight: 600; border-bottom: 1px solid #d1d5db; white-space: nowrap; }
table.dense td:first-child { white-space: nowrap; }
table.dense { font-size: 8.2pt; } table.dense td, table.dense th { padding: 2px 5px; }
table.summary td:nth-child(5) { font-size: 8pt; color: #6b7280; }
table.compact { width: auto; }
small { color: #6b7280; }
.st { font-weight: 600; white-space: nowrap; }
.st.ok { color: #15803d; } .st.todo { color: #b45309; }
.chart { break-inside: avoid; margin: 2px 0 6px; }
.appendix { break-before: page; }
.tag { font-size: 8.5pt; font-weight: 400; color: #6b7280; margin-left: 6px; }
.two { display: flex; gap: 14px; align-items: flex-start; break-inside: avoid; } .two table { width: auto; min-width: 230px; }
tr { break-inside: avoid; }
"""


def build_html(today: date | None = None) -> str:
    today = pd.Timestamp(today or date.today()).normalize()
    CD = G.CD
    goals = getattr(CD, "CARDIO_GOALS", None)
    lip = G.br.lipids_df()
    last_bp = M.bp_readings()
    last_w = M.weight_daily()
    through = []
    if not lip.empty:
        through.append(f"labs {lip['date'].max():%b %-d, %Y}")
    if not last_bp.empty:
        through.append(f"BP {last_bp['timestamp'].max():%b %-d}")
    if not last_w.empty:
        through.append(f"weight {last_w['date'].max():%b %-d}")

    x_end = today + pd.Timedelta(days=21)   # room for end-of-line labels
    bp = M.bp_readings()
    bp_weekly = M.bp_weekly(bp)
    bp_weekly = bp_weekly[bp_weekly["week"] >= G.TRACKER_START - pd.Timedelta(days=7)] if not bp_weekly.empty else bp_weekly
    w_weekly = M.weight_weekly(G.TRACKER_START.date().isoformat(), as_of_today=True, today=today)
    scans = M.dexa_scans()
    scans = scans[scans["body_fat_pct"].notna()] if not scans.empty else scans
    no_est = pd.DataFrame(columns=["week", "pct", "scan_date"])
    bp_weekly_tbl, bp_recent_tbl = _appendix_bp(today)
    parts = [f"""<!doctype html><html><head><meta charset="utf-8">
<title>Cardiovascular goals report — {today:%Y-%m-%d}</title>
<style>{_CSS}</style><script>{get_plotlyjs()}</script></head><body><div class="page">
<header>
  <h1>Cardiovascular goals — progress report</h1>
  <div class="who"><b>{esc(CD.PATIENT_NAME)}</b> · {esc(CD.SEX)} · DOB {esc(CD.DOB)} · {esc(CD.DESCRIPTOR)}</div>
  <div class="who">Prepared {today:%b %-d, %Y} · data through {", ".join(through)}</div>
</header>

<h2>Goals</h2>
{_summary(goals, lip, today)}

<h2>Current medications</h2>
{_medications()}

<h2>Goal 1 — LDL-C<span class="tag">LabCorp draws</span></h2>
<div class="chart">{_chart(G._ldl_figure(lip, goals, today, retired_last=True), 440, x_end)}</div>

<h2>Goal 2 — Blood pressure<span class="tag">weekly mean of home readings · Garmin Index BPM</span></h2>
<div class="chart">{_chart(G._bp_figure(bp_weekly, goals, today) if not bp_weekly.empty else None, 360, x_end)}</div>

<h2>Goal 3 — Weight and body fat</h2>
<h3>Weight<span class="tag">weekly 7-day mean · Garmin Index scale</span></h3>
<div class="chart">{_chart(G._weight_figure(w_weekly, M.weight_goals()[:1], today) if not w_weekly.empty else None, 260, x_end)}</div>
<h3>Body fat<span class="tag">DEXA · fat / (fat + lean)</span></h3>
<div class="chart">{_chart(G._body_fat_figure(scans, no_est, today, T.BODY_FAT_GOALS[:1]) if not scans.empty else None, 260, x_end)}</div>

<h2>Levers</h2>
<h3>Exercise, minutes per week<span class="tag">heart-rate zones 3–5 / 4–5 · max HR {T.HRMAX}</span></h3>
<div class="chart">{_chart(G.exercise_figure(today), 240, x_end)}</div>
<h3>Sodium, daily average per week<span class="tag">logged days · net of ride sweat sodium · MyFitnessPal</span></h3>
<div class="chart">{_chart(G.sodium_figure(today), 220, x_end)}</div>
<h3>Alcohol, drinks per week<span class="tag">Garmin lifestyle log</span></h3>
<div class="chart">{_chart(G.alcohol_figure(today), 220, x_end)}</div>

<div class="appendix">
<h2>Appendix A — Lipid panels (mg/dL; Lp(a) nmol/L)</h2>
{_appendix_lipids(lip)}
<h2>Appendix B — Blood pressure by week</h2>
{bp_weekly_tbl}
<h2>Appendix C — Blood pressure readings, last 14 days</h2>
{bp_recent_tbl}
<h2>Appendix D — Weight by week</h2>
{_appendix_weight(today)}
<h2>Appendix E — DEXA scans</h2>
{_appendix_dexa()}
<h2>Appendix F — Exercise, sodium and alcohol by week</h2>
{_appendix_levers(today)}
<h2>Appendix G — Medication history</h2>
{_appendix_med_history()}
<h2>Appendix H — Clinical events</h2>
{_appendix_events()}
</div>
</div></body></html>"""]
    return "".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="output .html path (default: cardiology/out/goals_report_<date>.html)")
    ap.add_argument("--pdf", action="store_true", help="also print a PDF next to it with headless Chrome")
    a = ap.parse_args(argv)
    out = a.out or os.path.join(OUT_DIR, f"goals_report_{date.today():%Y-%m-%d}.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as fh:
        fh.write(build_html())
    print(out)
    if a.pdf:
        pdf = os.path.splitext(out)[0] + ".pdf"
        chrome = next((p for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                                   "/Applications/Chromium.app/Contents/MacOS/Chromium") if os.path.exists(p)), None)
        if chrome is None:
            print("no Chrome/Chromium found: open the HTML and print to PDF", file=sys.stderr)
            return 1
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--virtual-time-budget=10000", f"--print-to-pdf={pdf}", "file://" + os.path.abspath(out)],
                       check=True, capture_output=True)
        print(pdf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
