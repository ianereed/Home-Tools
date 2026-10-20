"""Pure display helpers for the Anny Food tab (no Streamlit import)."""
from __future__ import annotations

import html
from datetime import date

from anny_food import track as _tr
from anny_food.targets import Target, Targets

STATUS_COLORS = {
    _tr.UNDER: "#e8833a",
    _tr.ON_PACE: "#4c8bf5",
    _tr.MET: "#2e9d5b",
    _tr.OVER: "#d64545",
    _tr.OK: "#2e9d5b",
    _tr.AHEAD: "#d9a400",
}

STATUS_WORDS = {
    _tr.UNDER: "behind pace",
    _tr.ON_PACE: "on pace",
    _tr.MET: "met",
    _tr.OVER: "over max",
    _tr.OK: "under max",
    _tr.AHEAD: "using max fast",
}

BAR_CSS = """
<style>
.anny-bars { margin: 0.25rem 0 0.75rem 0; }
.anny-bar { margin: 0 0 0.7rem 0; }
.anny-bar.sub { margin-left: 1.1rem; margin-bottom: 0.5rem; }
.anny-head { display: flex; justify-content: space-between; gap: 0.5rem;
             font-size: 1rem; line-height: 1.3; }
.anny-bar.sub .anny-head { font-size: 0.9rem; opacity: 0.9; }
.anny-head .lbl { font-weight: 600; }
.anny-bar.sub .anny-head .lbl { font-weight: 500; }
.anny-head .num { white-space: nowrap; font-variant-numeric: tabular-nums; }
.anny-track { position: relative; height: 0.85rem; border-radius: 0.45rem;
              background: rgba(128,128,128,0.22); overflow: visible; margin-top: 0.15rem; }
.anny-bar.sub .anny-track { height: 0.6rem; }
.anny-fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 0.45rem; }
.anny-pace { position: absolute; top: -0.2rem; bottom: -0.2rem; width: 2px;
             background: currentColor; opacity: 0.75; }
.anny-legend { font-size: 0.8rem; opacity: 0.8; margin-bottom: 0.4rem; }
.anny-legend span.sw { display: inline-block; width: 0.7rem; height: 0.7rem;
                       border-radius: 0.2rem; margin: 0 0.2rem 0 0.6rem; vertical-align: -0.05rem; }
</style>
"""


def fmt_amount(v: float, unit: str) -> str:
    if unit in ("mg", "g", "kcal"):
        return f"{v:,.0f}"
    return f"{v:.1f}"


def bar_html(t: Target, value: float, tri: str, pace_frac: float, *, sub: bool = False) -> str:
    target = t.weekly(tri)
    st_ = _tr.status(t, value, tri, pace_frac)
    color = STATUS_COLORS[st_]
    fill = 0.0 if not target else min(value / target, 1.0) * 100
    num = f"{fmt_amount(value, t.unit)} / {fmt_amount(target, t.unit)} {html.escape(t.unit)}"
    if t.kind == "limit":
        num = f"{fmt_amount(value, t.unit)} of max {fmt_amount(target, t.unit)} {html.escape(t.unit)}"
    elif t.ceiling is not None:
        num += f" (max {fmt_amount(t.ceiling, t.unit)})"
    if st_ == _tr.MET:
        num = "✓ " + num
    elif st_ == _tr.OVER:
        num = "⚠ " + num
    pace_left = min(pace_frac, 1.0) * 100
    return (
        f'<div class="anny-bar{" sub" if sub else ""}" title="{STATUS_WORDS[st_]}">'
        f'<div class="anny-head"><span class="lbl">{html.escape(t.label)}</span>'
        f'<span class="num">{num}</span></div>'
        f'<div class="anny-track"><div class="anny-fill" style="width:{fill:.1f}%;background:{color}"></div>'
        f'<div class="anny-pace" style="left:calc({pace_left:.1f}% - 1px)"></div></div></div>'
    )


def legend_html() -> str:
    parts = [f'<span class="sw" style="background:{STATUS_COLORS[s]}"></span>{STATUS_WORDS[s]}'
             for s in (_tr.UNDER, _tr.ON_PACE, _tr.MET, _tr.OVER)]
    return f'<div class="anny-legend">{"".join(parts)} &nbsp;·&nbsp; │ = where you\'d be today</div>'


def bars_html(targets: Targets, totals: dict[str, float], tri: str, pace_frac: float) -> str:
    """All enabled rows in config order; subgroups indented. The albacore
    sub-cap only shows once some albacore has been eaten."""
    out = [BAR_CSS, legend_html(), '<div class="anny-bars">']
    for t in targets.enabled():
        value = totals.get(t.key, 0.0)
        if t.subset_of and value <= 0:
            continue
        out.append(bar_html(t, value, tri, pace_frac, sub=t.parent is not None))
    out.append("</div>")
    return "".join(out)


# Short names for one-line day summaries.
SUMMARY_KEYS = (
    ("veg_total", "Veg", "c"),
    ("fruit", "Fruit", "c"),
    ("grain_whole", "Whole grain", "oz"),
    ("dairy", "Dairy", "c"),
    ("protein_total", "Protein", "oz"),
    ("calcium", "Ca", "mg"),
    ("choline", "Choline", "mg"),
)


def day_summary(totals: dict[str, float]) -> str:
    parts = []
    for key, name, unit in SUMMARY_KEYS:
        v = totals.get(key, 0.0)
        if v > 0:
            parts.append(f"{name} {fmt_amount(v, unit)} {unit}")
    return " · ".join(parts) if parts else "nothing counted yet"


def day_label(d: date, today: date) -> str:
    if d == today:
        return f"Today · {d:%a %b} {d.day}"
    return f"{d:%a %b} {d.day}"


def week_label(start: date, end: date) -> str:
    if start.month == end.month:
        return f"{start:%b} {start.day}–{end.day}"
    return f"{start:%b} {start.day} – {end:%b} {end.day}"


def entry_label(entry: dict, problem: str | None) -> str:
    servings = float(entry["servings"])
    s = f"{servings:g}× " if servings != 1 else ""
    icon = {"recipe": "🍲", "staple": "🥣", "custom": "✏️", "pending": "⏳"}.get(entry["kind"], "")
    tail = f"  — {problem}" if problem else ""
    return f"{icon} {s}{entry['label']}{tail}"


def food_option_label(food: dict) -> str:
    if food["kind"] == "recipe":
        tail = "" if food["has_breakdown"] else "  (no breakdown yet)"
        return f"🍲 {food['label']}{tail}"
    return f"🥣 {food['label']}"


def trimester_line(tri: str, ga: tuple[int, int] | None, overridden: bool) -> str:
    n = tri[1]
    if ga is None:
        return f"**Trimester {n}** (assumed — set the due date under ⚙️ Settings)"
    weeks, days = ga
    tail = " (set manually)" if overridden else ""
    return f"**Trimester {n}**{tail} · {weeks} weeks {days} days"
