"""Fill the plan horizon past what Garmin Coach has published.

- Inside Coach's published window: Coach workouts as-is (not handled here).
- After the window while a Coach plan is still running: a weekday template built from the
  Coach workouts seen in recent refreshes (latest version per date), labeled 'projected'.
- After the Coach plan ends (or with no active plan): planner-generated rides (rides.py).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from . import rides


def weekday_template(versions: list[dict], asof: date, lookback_days: int = 21) -> dict[int, dict]:
    """versions: normalized Coach workouts ({date, type, ... or rest}) seen in recent refreshes.
    Returns {weekday (0=Mon): workout} using the most common type per weekday, represented by the
    most recent workout of that type (so its steps are realistic)."""
    lo = (asof - timedelta(days=lookback_days)).isoformat()
    by_wd: dict[int, list[dict]] = {}
    for w in sorted(versions, key=lambda x: x["date"]):
        if w["date"] < lo:
            continue
        by_wd.setdefault(date.fromisoformat(w["date"]).weekday(), []).append(w)
    out = {}
    for wd, ws in by_wd.items():
        kinds = Counter("REST" if w.get("rest") else w["type"] for w in ws)
        kind, _ = kinds.most_common(1)[0]
        rep = [w for w in ws if ("REST" if w.get("rest") else w["type"]) == kind][-1]
        out[wd] = rep
    return out


def project(start: date, end: date, *, coach_window_end: date | None, plan_end: date | None,
            coach_active: bool, versions: list[dict], athlete: dict, generated_since: date | None = None) -> list[dict]:
    """Workouts for start..end that Coach hasn't published. Each carries source 'projected' or 'generated'."""
    out = []
    first = start if coach_window_end is None else max(start, coach_window_end + timedelta(days=1))
    if first > end:
        return out
    template = weekday_template(versions, start) if coach_active else {}
    proj_end = min(end, plan_end) if (coach_active and plan_end) else (first - timedelta(days=1))
    d = first
    while d <= proj_end:
        t = template.get(d.weekday())
        if t is not None:
            w = {k: v for k, v in t.items() if k not in ("date", "uuid", "workout_uuid")}
            out.append({**w, "date": d.isoformat(), "source": "projected",
                        "note": "Projected from Coach's recent weeks; replaced when Coach publishes this day."})
        d += timedelta(days=1)
    gen_start = proj_end + timedelta(days=1)
    if gen_start <= end:
        gen = rides.generate(gen_start, end, athlete["ftp_w"], athlete.get("available_days") or rides.WEEKDAYS[:6],
                             athlete.get("long_days") or ["SATURDAY"], first_monday=generated_since)
        for w in gen:
            w["note"] = ("Planner-generated ride (no active Garmin Coach plan)." if not coach_active
                         else "Planner-generated ride (after the Coach plan ends).")
        out += gen
    return out
