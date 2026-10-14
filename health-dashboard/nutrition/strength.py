"""Strength plan: 2 gym lifts per Mon-Sun week, placed around rides/calendar, adjusted to training load.

Templates (Garmin exercise keys verified against connect.garmin.com/web-data/exercises/Exercises.json):
lower body + core until upper-body work is cleared in settings; then A+/B+ add a push and a pull.
Progression: level 1-6 per template, +1 level every 2 completed sessions; every 4th planner week is a
deload (-1 set). Load rules can swap in the 'light' variant (-1 set, +1 rep in reserve) or move a lift;
every decision carries a human-readable reason.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, timedelta

from . import engine as E

# (label, Garmin category, Garmin exerciseName, rep scheme: "main" | "accessory" | "core")
TEMPLATES = {
    "A": [("Goblet squat", "SQUAT", "GOBLET_SQUAT", "main"),
          ("Romanian deadlift", "DEADLIFT", "ROMANIAN_DEADLIFT", "main"),
          ("Bulgarian split squat", "LUNGE", "DUMBBELL_BULGARIAN_SPLIT_SQUAT", "accessory"),
          ("Standing calf raise", "CALF_RAISE", "STANDING_CALF_RAISE", "accessory"),
          ("Dead bug", "HIP_STABILITY", "DEAD_BUG", "core")],
    "B": [("Trap-bar deadlift", "DEADLIFT", "TRAP_BAR_DEADLIFT", "main"),
          ("Dumbbell step-up", "SQUAT", "DUMBBELL_STEP_UP", "accessory"),
          ("Hip thrust", "HIP_RAISE", "BARBELL_HIP_THRUST_WITH_BENCH", "main"),
          ("Single-leg RDL", "DEADLIFT", "SINGLE_LEG_ROMANIAN_DEADLIFT_WITH_DUMBBELL", "accessory"),
          ("Side plank", "PLANK", "SIDE_PLANK", "core")],
}
UPPER = {"A": [("Dumbbell bench press", "BENCH_PRESS", "DUMBBELL_BENCH_PRESS", "accessory")],
         "B": [("Dumbbell row", "ROW", "DUMBBELL_ROW", "accessory")]}

# level -> (sets, reps) for main lifts; accessories run one more rep, core is fixed
LEVELS = {1: (2, 10), 2: (3, 10), 3: (3, 8), 4: (4, 8), 5: (4, 6), 6: (4, 5)}
RIR = {1: 3, 2: 3, 3: 2, 4: 2, 5: 2, 6: 1}
LIFT_MIN = 45
REST_S = {"main": 120, "accessory": 75, "core": 45}


def prescription(template: str, level: int, variant: str, deload: bool, upper: bool) -> list[dict]:
    sets, reps = LEVELS[max(1, min(6, level))]
    rir = RIR[max(1, min(6, level))]
    if deload or variant == "light":
        sets, rir = max(2, sets - 1), rir + 1
    out = []
    for label, cat, name, kind in TEMPLATES[template] + (UPPER[template] if upper else []):
        if kind == "core":
            out.append({"label": label, "category": cat, "exercise": name, "sets": 2 if variant == "light" else 3,
                        "reps": 10 if name != "SIDE_PLANK" else None, "seconds": 30 if name == "SIDE_PLANK" else None,
                        "rest_s": REST_S[kind]})
        else:
            out.append({"label": label, "category": cat, "exercise": name, "sets": sets,
                        "reps": reps + (2 if kind == "accessory" else 0), "rir": rir, "rest_s": REST_S[kind]})
    return out


def spec_hash(spec: dict) -> str:
    keep = {k: spec[k] for k in ("template", "level", "variant", "deload", "upper", "exercises")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:10]


# ------------------------------------------------------------------ load
def daily_load(activities: list[dict], ftp: float) -> dict[str, float]:
    """Training stress per day: TSS from normalized power when available, else Garmin's training load."""
    out: dict[str, float] = {}
    for a in activities:
        hours = (a.get("duration_s") or 0) / 3600
        if not a.get("np_w") and a.get("training_load") is None:
            continue  # rows collected before load fields existed: unknown, not zero
        if a.get("np_w") and ftp:
            ifv = a["np_w"] / ftp
            tss = hours * ifv * ifv * 100
        else:
            tss = a.get("training_load") or 0
        out[a["date"]] = out.get(a["date"], 0.0) + tss
    return out


def acwr(load: dict[str, float], today: date, min_history_days: int = 21) -> float | None:
    """7-day : 28-day mean daily load. None until load data reaches back `min_history_days` (otherwise
    the chronic side is mostly empty days and the ratio is wildly high)."""
    if not load or (today - date.fromisoformat(min(load))).days < min_history_days:
        return None

    def mean(n):
        return sum(load.get((today - timedelta(days=i)).isoformat(), 0.0) for i in range(1, n + 1)) / n
    chronic = mean(28)
    return mean(7) / chronic if chronic > 0 else None


# ------------------------------------------------------------------ placement
def _valid(i: int, days: list[dict], taken: list[int], cfg: dict) -> tuple[bool, str, int | None]:
    """(ok, why_not, start_minute) for a lift on days[i]."""
    day = days[i]
    cat = E.day_category(day)
    if cat in ("rest", "long"):
        return False, f"{cat} day", None
    nxt = E.day_category(days[i + 1]) if i + 1 < len(days) else "rest"
    if nxt in ("key", "long"):
        return False, f"day before a {nxt} session", None
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(days) and any(e.get("trip") for e in days[j].get("events", [])):
            return False, "trip within a day", None
    if any(abs(i - t) < 2 for t in taken):
        return False, "less than 48 h from another lift", None
    ride = next((s for s in day["sessions"] if s["kind"] == "ride" and s.get("start") is not None), None)
    if ride is None:
        return False, "no ride to anchor to", None
    gap = 180 if ride["category"] == "key" else 30
    busy = E.busy_intervals(day, cfg) + [(ride["start"], ride["end"] + E.SHOWER_MIN, "ride")]
    first = int(math.ceil((ride["end"] + gap) / 15) * 15)
    for st in range(first, E.tmin(E.LATEST_END) - LIFT_MIN + 1, 15):
        if E.fits(st, st + LIFT_MIN, busy):
            return True, "", st
    return False, "no free 45-min window", None


def plan_lifts(days: list[dict], *, today: date, cfg: dict, settings: dict, completed: list[dict],
               previous: list[dict], load: dict[str, float], readiness: float | None,
               progress: dict[str, int], generated_week0: date | None = None) -> list[dict]:
    """Desired lifts over the plan horizon. `completed`: strength activities (date) this horizon/week;
    `previous`: last run's desired lifts (sticky dates); `progress`: completed sessions per template."""
    idx = {d["date"]: i for i, d in enumerate(days)}
    t = today.isoformat()
    upper = bool(settings.get("upper_body_cleared"))
    ratio = acwr(load, today)
    prev_by_week: dict[str, list[dict]] = {}
    for p in previous:
        prev_by_week.setdefault(E.monday_of(p["date"]), []).append(p)
    done_dates = {c["date"] for c in completed}
    last_template = max(completed, key=lambda c: c["date"]).get("template") if completed else None
    tmpl_cycle = "B" if last_template == "A" else "A"
    out: list[dict] = []
    taken = [idx[d] for d in done_dates if d in idx]
    weeks = sorted({E.monday_of(d["date"]) for d in days})
    week0 = generated_week0 or date.fromisoformat(weeks[0])
    for mon in weeks:
        wk_days = [i for i, d in enumerate(days) if E.monday_of(d["date"]) == mon]
        want = 2 - sum(1 for d in done_dates if E.monday_of(d) == mon)
        reasons_week = []
        if ratio is not None and ratio > 1.5 and mon == E.monday_of(t):
            want = min(want, 1)
            reasons_week.append(f"7d:28d load ratio {ratio:.2f} > 1.5: one lift this week")
        if want <= 0:
            continue
        picks: list[tuple[int, int, list[str], str | None]] = []
        # 1) keep last run's dates when still valid (no flapping); lifts Ian moved by hand are pinned
        for p in sorted(prev_by_week.get(mon, []), key=lambda p: p["date"]):
            ds = p["date"]
            if ds < t or ds not in idx or len(picks) >= want:
                continue
            ok, why, st = _valid(idx[ds], days, taken + [q[0] for q in picks], cfg)
            if p.get("pinned"):
                st = st if st is not None else E.tmin("17:00")
                picks.append((idx[ds], st, ["date set by you in Garmin Connect"], p.get("plan_key")))
            elif ok:
                picks.append((idx[ds], st, ["kept from the previous plan"], p.get("plan_key")))
            else:
                reasons_week.append(f"moved off {ds}: {why}")
        # 2) fill: easy-ride days first, then moderate
        for want_cat in ("easy", "moderate", "key"):
            for i in wk_days:
                if len(picks) >= want:
                    break
                if days[i]["date"] < t or any(p[0] == i for p in picks) or E.day_category(days[i]) != want_cat:
                    continue
                ok, why, st = _valid(i, days, taken + [p[0] for p in picks], cfg)
                if ok:
                    picks.append((i, st, [f"{want_cat}-ride day"], None))
        used_keys = {p[3] for p in picks if p[3]}
        free = (f"lift-{mon}-{n}" for n in range(1, 9) if f"lift-{mon}-{n}" not in used_keys)
        for i, st, why, key in sorted(picks):
            ds = days[i]["date"]
            weeks_in = max(0, (date.fromisoformat(mon) - week0).days // 7)
            deload = weeks_in % 4 == 3
            template = tmpl_cycle
            tmpl_cycle = "B" if tmpl_cycle == "A" else "A"
            level = min(6, 1 + progress.get(template, 0) // 2)
            variant, reasons = "normal", list(why) + reasons_week
            if deload:
                reasons.append("deload week (every 4th week)")
            if ratio is not None and ratio > 1.3 and (date.fromisoformat(ds) - today).days < 3:
                variant = "light"
                reasons.append(f"7d:28d load ratio {ratio:.2f} > 1.3: light session")
            yday = (date.fromisoformat(ds) - timedelta(days=1)).isoformat()
            if load.get(yday, 0) >= 100 and ds > t:
                variant = "light"
                reasons.append("hard ride the day before (TSS >= 100): light session")
            if ds == t and readiness is not None and readiness < 25:
                variant = "light"
                reasons.append(f"Garmin readiness {readiness:.0f} < 25: light session")
            ex = prescription(template, level, variant, deload, upper)
            spec = {"template": template, "level": level, "variant": variant, "deload": deload, "upper": upper,
                    "exercises": ex}
            out.append({"plan_key": key or next(free), "kind": "lift", "date": ds, "start": st, "end": st + LIFT_MIN,
                        "minutes": LIFT_MIN, **spec, "spec_hash": spec_hash(spec), "reasons": reasons,
                        "name": f"FP · Lift {template}{level}{' light' if variant == 'light' else ''}"
                                f"{' deload' if deload else ''} · {'Full body' if upper else 'Lower+Core'}"})
            taken.append(i)
    return out


# ------------------------------------------------------------------ Garmin workout JSON
def _step_type(key: str, sid: int) -> dict:
    return {"stepTypeId": sid, "stepTypeKey": key}


def to_garmin_json(lift: dict) -> dict:
    """Garmin Connect strength workout DTO (reps-based steps in repeat groups, one per exercise)."""
    sport = {"sportTypeId": 5, "sportTypeKey": "strength_training"}
    steps, order = [], 1
    steps.append({"type": "ExecutableStepDTO", "stepOrder": order, "stepType": _step_type("warmup", 1),
                  "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"}, "endConditionValue": 300,
                  "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                  "description": "5 min easy: bike, row, or dynamic warm-up"})
    order += 1
    for ex in lift["exercises"]:
        group_order = order
        order += 1
        work = {"type": "ExecutableStepDTO", "stepOrder": order, "stepType": _step_type("interval", 3),
                "category": ex["category"], "exerciseName": ex["exercise"],
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                "description": f"{ex['label']}" + (f" · {ex['rir']} reps in reserve" if ex.get("rir") else "")}
        if ex.get("reps"):
            work |= {"endCondition": {"conditionTypeId": 10, "conditionTypeKey": "reps"}, "endConditionValue": ex["reps"]}
        else:
            work |= {"endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"}, "endConditionValue": ex["seconds"]}
        order += 1
        rest = {"type": "ExecutableStepDTO", "stepOrder": order, "stepType": _step_type("rest", 5),
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"}, "endConditionValue": ex["rest_s"],
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}}
        order += 1
        steps.append({"type": "RepeatGroupDTO", "stepOrder": group_order, "stepType": _step_type("repeat", 6),
                      "numberOfIterations": ex["sets"], "smartRepeat": False, "workoutSteps": [work, rest]})
    steps.append({"type": "ExecutableStepDTO", "stepOrder": order, "stepType": _step_type("cooldown", 2),
                  "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"}, "endConditionValue": 300,
                  "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}})
    why = "; ".join(lift.get("reasons", []))[:400]
    return {"workoutName": lift["name"][:80], "sportType": sport, "estimatedDurationInSecs": lift["minutes"] * 60,
            "description": f"fuel-planner:{lift['plan_key']}:{lift['spec_hash']} · {why}",
            "workoutSegments": [{"segmentOrder": 1, "sportType": sport, "workoutSteps": steps}]}
