"""Overview: what changed since you last looked, and is the data fresh.

Information only. Every tile is a 7-day average from the same metric function
its page uses (dashboard.metrics), compared with the 7 days ending on your
previous visit, so a tile and its page can't disagree.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from dashboard import lib
from dashboard import metrics as M

# (key, label, unit, format, higher_is_better, series color, page it lives on, basis)
# basis "avg7" = 7-day average; "latest" for fitness, which is already a 42-day average.
TILES = [
    ("sleep", "Sleep", "h", "{:.1f}", True, "sleep", "Recovery", "avg7"),
    ("hrv", "HRV", "ms", "{:.0f}", True, "hrv", "Recovery", "avg7"),
    ("rhr", "Resting HR", "bpm", "{:.0f}", False, "rhr", "Recovery", "avg7"),
    ("fitness", "Fitness (CTL)", "", "{:.0f}", True, "volume", "Training", "latest"),
    ("steps", "Steps", "", "{:,.0f}", True, "steps", "Training", "avg7"),
    ("weight", "Weight", "lb", "{:.1f}", False, "weight", "Nutrition", "avg7"),
]
_FLAT = 0.03   # changes under 3 % read as "steady"


def _series() -> dict[str, pd.DataFrame]:
    """date/value frames for every tile, last 120 days."""
    start = M.since(120)
    sl = M.sleep_nightly(start)
    load = M.training_load_cached(120)
    return {
        "sleep": sl.rename(columns={"hours": "v"})[["date", "v"]] if not sl.empty else sl,
        "hrv": M.hrv_daily(start).rename(columns={"hrv": "v"}),
        "rhr": M.resting_hr_daily(start).rename(columns={"rhr": "v"}),
        "fitness": load.rename(columns={"ctl": "v"})[["date", "v"]] if not load.empty else load,
        "steps": M.steps_daily(start).rename(columns={"steps": "v"}),
        "weight": M.weight_daily(start).rename(columns={"lb": "v"}),
    }


def _as_of(df: pd.DataFrame, when: date) -> float | None:
    if df.empty:
        return None
    s = df[df["date"] <= pd.Timestamp(when)]["v"].dropna()
    return s.iloc[-1] if not s.empty else None


def changes(series: dict, compare_from: date, today: date) -> list[dict]:
    """Per tile: the current value (7-day average, or latest for fitness) vs the
    same measure as of `compare_from`."""
    out = []
    for key, label, unit, fmt, hib, color, page, basis in TILES:
        df = series.get(key, pd.DataFrame())
        if basis == "latest":
            cur, prior = _as_of(df, today), _as_of(df, compare_from)
        else:
            cur, prior = M.avg_window(df, "v", today), M.avg_window(df, "v", compare_from)
        pct = (cur - prior) / abs(prior) if cur is not None and prior else None
        direction = None if pct is None else "flat" if abs(pct) < _FLAT else "up" if pct > 0 else "down"
        good = None if direction in (None, "flat") else (direction == "up") == hib
        out.append({"key": key, "label": label, "unit": unit, "fmt": fmt, "color": color,
                    "page": page, "basis": basis, "cur": cur, "prior": prior, "pct": pct,
                    "direction": direction, "good": good,
                    "spark": df["v"].tolist()[-60:] if not df.empty else []})
    return out


def _freshness_badge(label: str, last: str | None, today: date) -> str:
    """Daily metrics are dated the night/day they describe, so yesterday is fresh."""
    if not last:
        return (f'<span style="display:inline-block;background:#33384a;color:{lib.MUTED};font-size:10px;'
                f'padding:2px 7px;border-radius:10px;margin:2px 4px 2px 0;">{label}: no data</span>')
    d = date.fromisoformat(str(last)[:10])
    behind = (today - d).days
    color = lib.GOOD if behind <= 1 else lib.WARN if behind <= 2 else lib.BAD
    return (f'<span style="display:inline-block;background:{color}22;border:1px solid {color}55;'
            f'color:{color};font-size:10px;padding:2px 7px;border-radius:10px;margin:2px 4px 2px 0;">'
            f'{label}: {d:%b %-d}</span>')


def _last_dates() -> dict[str, str | None]:
    def last(df):
        return None if df.empty else df["date"].max().date().isoformat()
    start = M.since(60)
    acts = M.activities(start)
    return {"Sleep": last(M.sleep_nightly(start)), "HRV": last(M.hrv_daily(start)),
            "Resting HR": last(M.resting_hr_daily(start)), "Steps": last(M.steps_daily(start)),
            "Activities": last(acts), "Weight": last(M.weight_daily(start)),
            "Food log": last(M.nutrition_daily(start))}


def render_overview(since_iso: str | None):
    today = date.today()
    first_visit = not since_iso or date.fromisoformat(since_iso) >= today
    compare_from = today - timedelta(days=30) if first_visit else date.fromisoformat(since_iso)
    lib.page_header("Overview", f"What changed since {'30 days ago' if first_visit else f'your last visit ({compare_from:%b %-d})'}: "
                    "each tile is a 7-day average (fitness: today's value), compared with the same measure then.")

    rows = changes(_series(), compare_from, today)
    tiles = []
    for r in rows:
        unit = f" <small>{r['unit']}</small>" if r["unit"] else ""
        value = lib.fmt(r["cur"], r["fmt"]) + (unit if r["cur"] is not None else "")
        delta = None
        if r["direction"] == "flat":
            delta = ("→ steady", lib.MUTED)
        elif r["direction"]:
            arrow = "↑" if r["direction"] == "up" else "↓"
            delta = (f"{arrow} {abs(r['pct']) * 100:.0f}% from {r['fmt'].format(r['prior'])}",
                     lib.GOOD if r["good"] else lib.BAD)
        elif r["cur"] is not None:
            delta = ("no reading back then", lib.MUTED)
        basis = "today" if r["basis"] == "latest" else "7-day avg"
        tiles.append(lib.card(r["label"], value, f"{basis} · on {r['page']}",
                              spark=lib.spark_svg(r["spark"], lib.SERIES[r["color"]]), delta=delta))
    lib.cards(tiles)

    moved = [r for r in rows if r["direction"] in ("up", "down")]
    if moved:
        st.markdown("## What moved")
        for r in moved:
            dot = lib.GOOD if r["good"] else lib.BAD
            st.markdown(
                f'<div style="margin:1px 0;font-size:0.9rem;"><span style="color:{dot};">●</span> '
                f'<b>{r["label"]}</b> {r["direction"]} from {r["fmt"].format(r["prior"])} to '
                f'{r["fmt"].format(r["cur"])}{(" " + r["unit"]) if r["unit"] else ""}</div>',
                unsafe_allow_html=True)
    else:
        st.caption("Nothing moved more than 3 %.")

    st.markdown("## Data freshness")
    st.markdown("".join(_freshness_badge(k, v, today) for k, v in _last_dates().items()),
                unsafe_allow_html=True)

    st.markdown("## Highlights")
    start30 = M.since(30)
    sl = M.sleep_nightly(start30)
    vol = M.weekly_volume(M.activities(M.since(90)))
    steps = M.steps_daily(start30)
    hl = []
    if not sl.empty:
        best, worst = sl.loc[sl["hours"].idxmax()], sl.loc[sl["hours"].idxmin()]
        hl.append(lib.card("Longest night · 30 d", f"{best['hours']:.1f} <small>h</small>", f"{best['date']:%b %-d}"))
        hl.append(lib.card("Shortest night · 30 d", f"{worst['hours']:.1f} <small>h</small>", f"{worst['date']:%b %-d}"))
    if not vol.empty:
        top = vol.loc[vol["hours"].idxmax()]
        hl.append(lib.card("Biggest training week · 90 d", f"{top['hours']:.1f} <small>h</small>",
                           f"week of {top['week']:%b %-d}"))
    if not steps.empty:
        top = steps.loc[steps["steps"].idxmax()]
        hl.append(lib.card("Most steps · 30 d", f"{top['steps']:,.0f}", f"{top['date']:%b %-d}"))
    if hl:
        lib.cards(hl)
