"""Track-mode math: pregnancy dating, weeks, weekly totals, pace, status.

Pure functions — no database, no Streamlit — so the rules are easy to test.
"""
from __future__ import annotations

from datetime import date, timedelta

from anny_food.targets import Target, Targets

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


# ---------------------------------------------------------------------------
# Dating
# ---------------------------------------------------------------------------

def gestational_age(due: date, today: date) -> tuple[int, int]:
    """(completed weeks, days) from a due date at 40w0d."""
    days = 280 - (due - today).days
    days = max(days, 0)
    return days // 7, days % 7


def trimester(due: date | None, today: date, override: str | None = None) -> str:
    """t1 through 13w6d, t2 14w0d–27w6d, t3 from 28w0d. An override wins;
    with neither, assume t1."""
    if override in ("t1", "t2", "t3"):
        return override
    if due is None:
        return "t1"
    weeks, _ = gestational_age(due, today)
    if weeks < 14:
        return "t1"
    if weeks < 28:
        return "t2"
    return "t3"


# ---------------------------------------------------------------------------
# Weeks
# ---------------------------------------------------------------------------

def week_bounds(d: date, week_start: str = "monday") -> tuple[date, date]:
    """First and last (inclusive) day of the week containing d."""
    start_idx = _WEEKDAYS.index(week_start.lower())
    offset = (d.weekday() - start_idx) % 7
    start = d - timedelta(days=offset)
    return start, start + timedelta(days=6)


def pace(today: date, week_start: date) -> float:
    """Fraction of the week elapsed, counting today as a full day.
    Day 3 of 7 → 3/7. Past weeks → 1.0, future weeks → 0.0."""
    n = (today - week_start).days + 1
    return min(max(n, 0), 7) / 7


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------

def sum_values(entries: list[dict[str, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for vals in entries:
        for k, v in vals.items():
            out[k] = out.get(k, 0.0) + v
    return out


def weekly_totals(raw: dict[str, float], targets: Targets, tri: str) -> dict[str, float]:
    """Add derived rows and split legumes between vegetables and protein.

    Legumes count toward veg_legumes until its weekly target is met; the
    rest becomes protein at meta.legume_protein_oz_eq_per_cup_eq. The
    overflow is reported as "legume_protein" so the UI can say so.
    """
    totals = {k: raw.get(k, 0.0) for k in targets.value_keys()}
    legumes = totals.get("veg_legumes", 0.0)
    try:
        legume_target = targets.get("veg_legumes").weekly(tri)
    except KeyError:
        legume_target = legumes
    as_veg = min(legumes, legume_target)
    rate = float(targets.meta.get("legume_protein_oz_eq_per_cup_eq", 4.0))
    totals["veg_legumes"] = as_veg
    totals["legume_protein"] = (legumes - as_veg) * rate
    for t in targets.enabled():
        if t.derived:
            totals[t.key] = sum(totals.get(k, 0.0) for k in t.derived_from)
            if t.key == "protein_total":
                totals[t.key] += totals["legume_protein"]
    return totals


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

UNDER, ON_PACE, MET, OVER, OK, AHEAD = "under", "on_pace", "met", "over", "ok", "ahead"

# Within this fraction of pace still counts as on pace — a single missed
# snack early in the week shouldn't turn a bar orange.
PACE_SLACK = 0.85


def status(t: Target, value: float, tri: str, pace_frac: float) -> str:
    target = t.weekly(tri)
    if t.kind == "limit":
        if value > target + 1e-9:
            return OVER
        if value > target * pace_frac + 1e-9:
            return AHEAD          # under the weekly max, but using it up early
        return OK
    if t.ceiling is not None and value > t.ceiling + 1e-9:
        return OVER
    if value >= target - 1e-9:
        return MET
    if value >= target * pace_frac * PACE_SLACK:
        return ON_PACE
    return UNDER


def percent(t: Target, value: float, tri: str) -> float:
    target = t.weekly(tri)
    return 100.0 * value / target if target else 0.0
