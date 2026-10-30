"""Cycling workouts on the Garmin calendar that neither Coach nor the planner owns (yours, or ones Claude
uploads) → engine workouts in coach.normalize's shape, so the fuel plan can plan around them.

Steps without a power target (HR, open, lap-button) count as easy Z2 so energy is not zero. The workout
type (which sets fueling and key-day handling) is inferred from the hardest step.
"""
from __future__ import annotations

import time
from datetime import date

from . import coach

FP_PREFIX = "FP · "
DETAIL_TTL_S = 6 * 3600
EASY = (0.55, 0.65)   # fraction of FTP assumed for steps with no power target


def calendar_rides(items: list[dict], today: date, end: date) -> list[dict]:
    """Scheduled cycling workouts in today..end that are not Coach's or planner-owned (FP · …)."""
    t0, t1 = today.isoformat(), end.isoformat()
    out = []
    for it in items:
        d = (it.get("date") or "")[:10]
        if it.get("itemType") != "workout" or it.get("sportTypeKey") != "cycling" or not (t0 <= d <= t1):
            continue
        if it.get("workoutId") is None or (it.get("title") or "").startswith(FP_PREFIX):
            continue
        out.append({"date": d, "schedule_id": it.get("id"), "workout_id": it["workoutId"], "title": it.get("title")})
    return sorted(out, key=lambda r: (r["date"], r["schedule_id"] or 0))


def _fill(steps, ftp: float) -> list:
    out = []
    for s in steps:
        if isinstance(s, dict):
            out.append({"repeat": s["repeat"], "steps": _fill(s["steps"], ftp)})
        elif s[0] > 0 and not s[2]:
            out.append([s[0], round(EASY[0] * ftp), round(EASY[1] * ftp)])
        else:
            out.append(s)
    return out


def _leaves(steps):
    for s in steps:
        if isinstance(s, dict):
            yield from _leaves(s["steps"])
        else:
            yield s


def infer_type(steps, ftp: float, minutes: float) -> str:
    """Workout type from the hardest step: (intensity as a fraction of FTP, that step's length)."""
    hard = [((lo + hi) / 2 / ftp, dur) for dur, lo, hi in _leaves(steps) if dur > 0]
    if not hard:
        return "BASE"
    r, dur = max(hard)
    if r >= 1.25 and dur <= 60:
        return "ANAEROBIC_SPEED"
    if r >= 1.2:
        return "ANAEROBIC_CAPACITY"
    if r >= 1.05:
        return "VO2MAX"
    if r >= 0.9 and dur >= 300:
        return "LACTATE_THRESHOLD"
    if r >= 0.8 and dur >= 600:
        return "TEMPO"
    if minutes >= 150:
        return "LONG_WORKOUT"
    return "RECOVERY" if r < 0.56 else "BASE"


def normalize(ride: dict, detail: dict, ftp: float) -> dict:
    steps = []
    for seg in detail.get("workoutSegments") or []:
        steps += coach._steps(seg.get("workoutSteps"))
    w = {"date": ride["date"], "name": detail.get("workoutName") or ride.get("title") or "Ride",
         "description": detail.get("description") or "", "source": "garmin", "workout_id": ride["workout_id"]}
    steps = _fill(steps, ftp)
    secs = sum(d for d, _, _ in _flat(steps))
    if secs > 0:
        w["steps"] = steps
        w["duration_s"] = secs
    else:  # nothing timed (lap-button only): fall back to Garmin's estimate at easy Z2
        w["duration_s"] = detail.get("estimatedDurationInSecs") or 3600
        w["avg_power_w"] = round(sum(EASY) / 2 * ftp)
    w["type"] = infer_type(steps, ftp, w["duration_s"] / 60) if secs > 0 else "BASE"
    return w


def _flat(steps):
    for s in steps:
        if isinstance(s, dict):
            for _ in range(s["repeat"]):
                yield from _flat(s["steps"])
        else:
            yield s


def stress(w: dict, ftp: float) -> float:
    """Training stress (TSS-like): hours x IF^2 x 100, summed over steps."""
    if w.get("steps"):
        return sum(d / 3600 * ((lo + hi) / 2 / ftp) ** 2 * 100 for d, lo, hi in _flat(w["steps"]))
    return (w.get("duration_s") or 0) / 3600 * ((w.get("avg_power_w") or 0) / ftp) ** 2 * 100


def fetch(client, today: date, end: date, ftp: float, cache: dict | None = None) -> tuple[list[dict], dict]:
    """(normalized rides, updated detail cache). cache = {workout_id: {"t": epoch, "detail": {...}}}."""
    cache = dict(cache or {})
    months, y, m = [], today.year, today.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    items = []
    for y, m in months:
        items += (client.get_scheduled_workouts(y, m) or {}).get("calendarItems", [])
    now = time.time()
    out = []
    for r in calendar_rides(items, today, end):
        key = str(r["workout_id"])
        hit = cache.get(key)
        if not hit or now - hit["t"] > DETAIL_TTL_S:
            hit = {"t": now, "detail": client.get_workout_by_id(r["workout_id"]) or {}}
            cache[key] = hit
        out.append(normalize(r, hit["detail"], ftp))
    live = {str(r["workout_id"]) for r in out}
    return out, {k: v for k, v in cache.items() if k in live}
