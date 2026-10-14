"""Planner-generated cycling weeks, used when no Garmin Coach plan is active.

Output uses the same workout shape as normalized Coach workouts (see coach.normalize):
{date, name, type, description, duration_s, steps, source}, where steps are [seconds, low_W, high_W]
or {"repeat": n, "steps": [...]}. Power bands are % of FTP.

Week shape (5 rides on the athlete's available days): two intensity days (VO2, threshold),
one long Z2 ride on a preferred long day, Z2 base on the rest, one rest day if 6+ days are
available. Every 4th week is a recovery week (intensity -> tempo/base, shorter long ride).
"""
from __future__ import annotations

from datetime import date, timedelta

WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

Z1 = (0.45, 0.55)
Z2 = (0.60, 0.72)
TEMPO = (0.80, 0.88)
THRESH = (0.95, 1.00)
VO2 = (1.10, 1.18)

THRESH_LEVELS = [(3, 600), (3, 720), (2, 1200), (3, 900)]   # (reps, seconds)
VO2_LEVELS = [(5, 180), (5, 240), (6, 240), (5, 300)]
LONG_MIN = [150, 165, 180]


def _w(ftp: float, band: tuple[float, float]) -> tuple[int, int]:
    return round(ftp * band[0]), round(ftp * band[1])


def _step(sec: int, ftp: float, band) -> list:
    lo, hi = _w(ftp, band)
    return [sec, lo, hi]


def _duration(steps) -> int:
    tot = 0
    for s in steps:
        tot += _duration(s["steps"]) * s["repeat"] if isinstance(s, dict) else s[0]
    return tot


def _workout(name, wtype, desc, steps):
    return {"name": name, "type": wtype, "description": desc, "steps": steps, "duration_s": _duration(steps)}


def base(ftp, minutes):
    return _workout("Base", "BASE", f"{minutes} min Z2", [_step(minutes * 60, ftp, Z2)])


def long_ride(ftp, minutes):
    return _workout("Long Ride", "LONG_WORKOUT", f"{minutes} min Z2", [_step(minutes * 60, ftp, Z2)])


def threshold(ftp, level):
    reps, sec = THRESH_LEVELS[level % len(THRESH_LEVELS)]
    steps = [_step(900, ftp, Z2), {"repeat": reps, "steps": [_step(sec, ftp, THRESH), _step(300, ftp, Z1)]},
             _step(600, ftp, Z1)]
    return _workout("Threshold", "LACTATE_THRESHOLD", f"{reps}x{sec // 60}:00 @ 95-100% FTP", steps)


def vo2(ftp, level):
    reps, sec = VO2_LEVELS[level % len(VO2_LEVELS)]
    steps = [_step(900, ftp, Z2), {"repeat": reps, "steps": [_step(sec, ftp, VO2), _step(sec, ftp, Z1)]},
             _step(600, ftp, Z1)]
    return _workout("VO2 Max", "VO2MAX", f"{reps}x{sec // 60}:00 @ 110-118% FTP", steps)


def tempo(ftp):
    steps = [_step(900, ftp, Z2), {"repeat": 2, "steps": [_step(900, ftp, TEMPO), _step(300, ftp, Z1)]},
             _step(600, ftp, Z1)]
    return _workout("Tempo", "TEMPO", "2x15:00 @ 80-88% FTP", steps)


def assign_days(available: list[str], long_days: list[str]) -> dict[str, str]:
    """Map weekday -> role (long | vo2 | threshold | base | rest) for one week."""
    avail = [d for d in WEEKDAYS if d in set(available)] or WEEKDAYS[:6]
    long_day = next((d for d in ("SATURDAY", "SUNDAY") if d in avail and d in long_days), None) \
        or next((d for d in long_days if d in avail), None) or avail[-1]
    li = WEEKDAYS.index(long_day)
    others = [d for d in avail if d != long_day]
    # intensity days: not the day before the long ride, and not back to back
    cand = [d for d in others if WEEKDAYS.index(d) != (li - 1) % 7]
    picks = []
    for pref in ("TUESDAY", "THURSDAY", "WEDNESDAY", "MONDAY", "FRIDAY"):
        if pref in cand and all(abs(WEEKDAYS.index(pref) - WEEKDAYS.index(p)) > 1 for p in picks):
            picks.append(pref)
        if len(picks) == 2:
            break
    roles = {d: "rest" for d in WEEKDAYS}
    roles[long_day] = "long"
    if picks:
        roles[picks[0]] = "vo2"
    if len(picks) > 1:
        roles[picks[1]] = "threshold"
    rest_left = len(avail) >= 6
    for d in others:
        if roles[d] != "rest":
            continue
        if rest_left and WEEKDAYS.index(d) == (li - 1) % 7:
            rest_left = False      # rest the day before the long ride
            continue
        roles[d] = "base"
    return roles


def generate_week(monday: date, week_index: int, ftp: float, available: list[str], long_days: list[str]) -> list[dict]:
    recovery = week_index % 4 == 3
    level = week_index - week_index // 4   # progression pauses on recovery weeks
    roles = assign_days(available, long_days)
    out = []
    for i, wd in enumerate(WEEKDAYS):
        role = roles[wd]
        ds = (monday + timedelta(days=i)).isoformat()
        if role == "rest":
            w = {"rest": True, "type": "REST"}
        elif role == "long":
            w = long_ride(ftp, 90 if recovery else LONG_MIN[min(level, len(LONG_MIN) - 1)])
        elif role == "vo2":
            w = base(ftp, 60) if recovery else vo2(ftp, level)
        elif role == "threshold":
            w = tempo(ftp) if recovery else threshold(ftp, level)
        else:
            w = base(ftp, 60 if recovery else 75)
        out.append({**w, "date": ds, "source": "generated", "week_index": week_index, "recovery_week": recovery})
    return out


def generate(start: date, end: date, ftp: float, available: list[str], long_days: list[str],
             first_monday: date | None = None) -> list[dict]:
    """Generated workouts for start..end. Week numbering counts from first_monday (the start of
    planner-generated training), so progression is stable across refreshes."""
    m0 = first_monday or (start - timedelta(days=start.weekday()))
    out = []
    monday = start - timedelta(days=start.weekday())
    while monday <= end:
        idx = max(0, (monday - m0).days // 7)
        out += [w for w in generate_week(monday, idx, ftp, available, long_days) if start.isoformat() <= w["date"] <= end.isoformat()]
        monday += timedelta(days=7)
    return out
