"""Strength plan: 2 gym lifts per Mon-Sun week (settings.lift_phases can change that per season phase),
placed around rides/calendar, adjusted to training load.

Placement (2026-11-04): hard rules in `_valid` (no lift the day before a key ride, on a long-ride day, a rest
weekday, a planned rest day, a blackout, near a trip, or within 48 h of another; ride first, lift 3 h after a key
ride). Preference: key days (after the ride, so easy days stay easy), then easy, moderate, and days with no ride
(14:00). Last resort: the day before a long ride, as a light session. A missed lift is caught up in the same week
when a day passes those rules, never doubled up; otherwise the week's note says it was dropped.

Templates (Garmin exercise keys verified against connect.garmin.com/web-data/exercises/Exercises.json):
lower body + core until upper-body work is cleared in settings; then A+/B+ add a push and a pull.
Progression: the first session of each template is an intro (level 0: fewer reps, more in reserve,
bodyweight accessories), then level 1-6, +1 level every 2 sessions of that template. Levels are projected
forward through the horizon, so future lifts continue from the last completed one. Every 4th week from
the block start is a deload (-1 set). While lifting is new (first INTRO_SESSIONS sessions) a lift is kept
off the day two days before a key ride when another day works, since soreness peaks ~48 h later. Load rules can swap in the 'light' variant (-1 set, +1 rep in reserve) or move a lift
(settings.lift_variant_by_date {date: 'normal'|'light'} overrides them for a day);
Working weights (settings.strength_weights, {exerciseName: {"lb": n, "note": "...", "label": "..."}}) come from Ian's notes
after a session (an optional "label" renames the step, e.g. when he uses a machine in place of the cable); they go on the watch as each step's weight target. Every decision carries a human-readable reason.
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
          ("Single-leg hip thrust (shoulders on bench, each leg)", "HIP_RAISE", "SINGLE_LEG_HIP_RAISE", "hip"),
          ("Cable hip abduction (ankle strap, each leg)", "HIP_STABILITY", "STANDING_CABLE_HIP_ABDUCTION", "hip"),
          ("Cable knee drive (ankle strap, knee above hip, each leg)", "LEG_RAISE", "LEG_RAISE", "hip"),
          ("Dead bug", "HIP_STABILITY", "DEAD_BUG", "core")],
    "B": [("Trap-bar deadlift", "DEADLIFT", "TRAP_BAR_DEADLIFT", "main"),
          ("Dumbbell step-up", "SQUAT", "DUMBBELL_STEP_UP", "accessory"),
          ("Hip thrust", "HIP_RAISE", "BARBELL_HIP_THRUST_WITH_BENCH", "main"),
          ("Single-leg RDL", "DEADLIFT", "SINGLE_LEG_ROMANIAN_DEADLIFT_WITH_DUMBBELL", "accessory"),
          ("Cable hip abduction (ankle strap, each leg)", "HIP_STABILITY", "STANDING_CABLE_HIP_ABDUCTION", "hip"),
          ("Cable knee drive (ankle strap, knee above hip, each leg)", "LEG_RAISE", "LEG_RAISE", "hip"),
          ("Reverse crunch (curl the pelvis up, slow down)", "CRUNCH", "REVERSE_CRUNCH", "hip"),
          ("Side plank (2 per side, alternate)", "PLANK", "SIDE_PLANK", "core")],
}
UPPER = {"A": [("Bench press", "BENCH_PRESS", "BARBELL_BENCH_PRESS", "accessory")],
         "B": [("Dumbbell row", "ROW", "DUMBBELL_ROW", "accessory")]}

# Home / bodyweight versions (no equipment): (label, category, exerciseName, reps, seconds).
# Push-ups are in both (Ian is cleared for them); there is no pull without a bar, so the superman /
# floor Y-raise cover the upper back.
HOME_TEMPLATES = {
    "A": [("Air squat", "SQUAT", "AIR_SQUAT", 15, None),
          ("Push-up", "PUSH_UP", "PUSH_UP", 10, None),
          ("Reverse lunge (each leg)", "LUNGE", "LUNGE", 10, None),
          ("Single-leg glute bridge (each leg)", "HIP_RAISE", "SINGLE_LEG_HIP_RAISE", 12, None),
          ("Single-leg calf raise (each leg)", "CALF_RAISE", "SINGLE_LEG_STANDING_CALF_RAISE", 15, None),
          ("Superman", "HYPEREXTENSION", "SUPERMAN_FROM_FLOOR", 12, None),
          ("Dead bug (each side)", "HIP_STABILITY", "DEAD_BUG", 10, None)],
    "B": [("Walking lunge (each leg)", "LUNGE", "WALKING_LUNGE", 10, None),
          ("Decline push-up (feet on a chair)", "PUSH_UP", "DECLINE_PUSH_UP", 8, None),
          ("Glute bridge", "HIP_RAISE", "HIP_RAISE", 15, None),
          ("Wall sit", "SQUAT", "BODY_WEIGHT_WALL_SQUAT", None, 45),
          ("Single-leg calf raise (each leg)", "CALF_RAISE", "SINGLE_LEG_STANDING_CALF_RAISE", 15, None),
          ("Floor Y-raise", "SHOULDER_STABILITY", "FLOOR_Y_RAISE", 12, None),
          ("Side plank (2 per side, alternate)", "PLANK", "SIDE_PLANK", None, 30)],
}

# level -> (sets, reps) for main lifts; accessories run two more reps (10 at the intro level), core is fixed
LEVELS = {0: (2, 8), 1: (2, 10), 2: (3, 10), 3: (3, 8), 4: (4, 8), 5: (4, 6), 6: (4, 5)}
RIR = {0: 4, 1: 3, 2: 3, 3: 2, 4: 2, 5: 2, 6: 1}
INTRO_NOTE = {"main": "light, find your starting weight", "accessory": "bodyweight or light"}
# Hip block for anterior pelvic tilt / weak glutes and hip flexors from sitting and cycling: glute max at end-range
# extension, glute med, hip flexors through full range (knee above hip), posterior-tilt core. Fixed reps; 2 sets
# through level 1, light or deload, else 3.
HIP_REPS = {"SINGLE_LEG_HIP_RAISE": 10, "STANDING_CABLE_HIP_ABDUCTION": 15, "LEG_RAISE": 12, "REVERSE_CRUNCH": 12}
INTRO_SESSIONS = 6
LIFT_MIN = 55
REST_S = {"main": 120, "accessory": 75, "core": 45}
LB = {"unitId": 9, "unitKey": "pound", "factor": 453.59237}


def level_for(sessions_done: int) -> int:
    """Template level after `sessions_done` sessions of it: 0 (intro), 1, 1, 2, 2, ... 6."""
    return 0 if sessions_done <= 0 else min(6, 1 + (sessions_done - 1) // 2)


def prescription(template: str, level: int, variant: str, deload: bool, upper: bool,
                 equipment: str = "gym", weights: dict | None = None, pt: list[dict] | None = None) -> list[dict]:
    intro = level <= 0 and equipment != "home"
    sets, reps = LEVELS[max(0 if intro else 1, min(6, level))]
    rir = RIR[max(0 if intro else 1, min(6, level))]
    if deload or variant == "light":
        sets, rir = max(2, sets - 1), rir + 1
    out = []
    if equipment == "home":
        bump = (max(1, min(6, level)) - 1) * 2   # +2 reps / +10 s per level
        for label, cat, name, r, sec in HOME_TEMPLATES[template]:
            out.append({"label": label, "category": cat, "exercise": name,
                        "sets": 4 if name == "SIDE_PLANK" else sets,   # 2 per side
                        "reps": r + bump if r else None, "seconds": sec + 5 * bump if sec else None,
                        "rir": rir if r else None, "rest_s": 60})
        return out + pt_exercises(pt)
    for label, cat, name, kind in TEMPLATES[template] + (UPPER[template] if upper else []):
        if kind == "hip":
            ex = {"label": label, "category": cat, "exercise": name,
                  "sets": 2 if level <= 1 or deload or variant == "light" else 3, "reps": HIP_REPS[name], "rest_s": 45}
            w = (weights or {}).get(name)
            if w and w.get("lb"):
                ex["weight_lb"] = w["lb"]
            elif cat in ("HIP_STABILITY", "LEG_RAISE"):
                ex["note"] = "light cable, find your starting weight"
            if w and w.get("label"):
                ex["label"] = w["label"]
            out.append(ex)
        elif kind == "core":
            out.append({"label": label, "category": cat, "exercise": name,
                        "sets": 4 if name == "SIDE_PLANK" else 2 if variant == "light" or intro else 3,
                        "reps": (8 if intro else 10) if name != "SIDE_PLANK" else None,
                        "seconds": (20 if intro else 30) if name == "SIDE_PLANK" else None,
                        "rest_s": REST_S[kind]})
        else:
            ex = {"label": label, "category": cat, "exercise": name, "sets": sets,
                  "reps": (10 if intro else reps + 2) if kind == "accessory" else reps, "rir": rir,
                  "rest_s": 90 if intro and kind == "main" else REST_S[kind]}
            if intro:
                ex["note"] = INTRO_NOTE[kind]
            w = (weights or {}).get(name)
            if w and w.get("lb"):
                ex["weight_lb"] = w["lb"]
                ex.pop("note", None)  # a known weight replaces "find your starting weight"
            if w and w.get("note"):
                ex["note"] = w["note"]
            if w and w.get("label"):
                ex["label"] = w["label"]
            out.append(ex)
    return out + pt_exercises(pt)


def apply_swaps(exercises: list[dict], swaps: dict | None, ds: str) -> tuple[list[dict], bool]:
    """Temporary changes (settings.exercise_swaps) for lifts on or before swaps['until'], keyed by Garmin
    exerciseName: swap {name: None} drops it, {name: {...}} replaces it (keeps sets/reps/rest unless given),
    note {name: "..."} adds a note. A swap can also be a list of stages [{"from": date, ...}, ...]: the last
    stage starting on or before the lift wins ({"drop": true} drops it). Returns (exercises, applied)."""
    if not swaps or ds > swaps.get("until", ""):
        return exercises, False
    out = []
    for e in exercises:
        k = e["exercise"]
        if k in (swaps.get("swap") or {}):
            new = swaps["swap"][k]
            if isinstance(new, list):
                due = [s for s in new if s.get("from", "") <= ds]
                new = {x: v for x, v in due[-1].items() if x != "from"} if due else {}
                if new.get("drop"):
                    new = None
            if new is None:
                continue
            pt = e["label"].startswith("PT · ")
            e = {**{x: v for x, v in e.items() if x not in ("weight_lb", "note", "rir")}, **new}
            if pt and not e["label"].startswith("PT · "):
                e["label"] = f"PT · {e['label']}"
        if k in (swaps.get("note") or {}):
            e = {**e, "note": swaps["note"][k]}
        out.append(e)
    return out, True


# Gym session order by muscle group, so PT work sits next to the main lift that warms the same muscles:
# legs, core, pressing (bench, then PT push work), shoulder raises, pulling (rows, then PT pulls).
GROUP_ORDER = {"SQUAT": 0, "LUNGE": 0, "DEADLIFT": 0, "HIP_RAISE": 0, "CALF_RAISE": 0, "LEG_CURL": 0,
               "HIP_STABILITY": 1, "LEG_RAISE": 1, "CRUNCH": 1, "PLANK": 1,
               "BENCH_PRESS": 2, "PUSH_UP": 2, "SHOULDER_PRESS": 2,
               "LATERAL_RAISE": 3, "SHOULDER_STABILITY": 3,
               "ROW": 4, "PULL_UP": 4, "SHRUG": 4}


def arrange_gym(exercises: list[dict]) -> list[dict]:
    """Gym rules: no push-ups on a bench-press day (scapular push-ups stay), then group by muscle with the
    main lift ahead of its PT work. Unknown categories keep their place at the end."""
    if any(e["exercise"] == "BARBELL_BENCH_PRESS" for e in exercises):
        exercises = [e for e in exercises if e["exercise"] != "PUSH_UP"]
    return sorted(exercises, key=lambda e: (GROUP_ORDER.get(e["category"], 9), e["label"].startswith("PT · ")))


def pt_exercises(pt: list[dict] | None) -> list[dict]:
    """Your physio routine (settings.pt_block), appended unchanged to every lift: no progression or deload."""
    return [{"rest_s": 45, **x, "label": f"PT · {x['label']}"} for x in pt or []]


def spec_hash(spec: dict) -> str:
    keep = {k: spec.get(k) for k in ("template", "level", "variant", "deload", "upper", "equipment", "exercises")}
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
FILL_ORDER = ("key", "easy", "moderate", "rest")
FILL_REASON = {"key": "hard day: after the key ride, so the easy days stay easy", "easy": "easy-ride day",
               "moderate": "moderate-ride day", "rest": "no ride planned that day"}
DEFAULT_START = "14:00"  # = gcal_sync.start_times.strength_training; the calendar event's time wins once it exists


def _valid(i: int, days: list[dict], taken: list[int], cfg: dict,
           soreness_guard: bool = False, before_long: bool = False) -> tuple[bool, str, int | None]:
    """(ok, why_not, start_minute) for a lift on days[i].
    Hard rules: no lift on a rest weekday (settings.lift_rest_weekdays), inside a blackout (settings.lift_blackouts,
    e.g. a trip the calendar doesn't flag), on a planned rest day, a long-ride day,
    the day before a key ride, within a day of a trip, or within 48 h of another lift; after a ride it waits
    3 h (key) or 30 min. A day with no ride is fine (from 14:00). `before_long`: allow the day before a long
    ride (plan_lifts makes that session light). `soreness_guard`: also refuse the day two days before a key
    ride (new lifters' soreness peaks ~48 h after a lift)."""
    day = days[i]
    wd = date.fromisoformat(day["date"])
    if cfg.get("lift_weekdays_only") and wd.weekday() >= 5:
        return False, "weekend (lifts on weekdays only)", None
    if wd.strftime("%a") in (cfg.get("lift_rest_weekdays") or ()):
        return False, f"{wd:%A} is a rest day", None
    for b in cfg.get("lift_blackouts") or ():
        if b["from"] <= day["date"] <= b["to"]:
            return False, f"{b.get('note') or 'no lifts'} ({b['from']} to {b['to']})", None
    cat = E.day_category(day)
    if cat == "long":
        return False, "long day", None
    if cat == "rest" and day.get("rest_planned"):
        return False, "rest day", None
    nxt = E.day_category(days[i + 1]) if i + 1 < len(days) else "rest"
    if nxt == "key" or (nxt == "long" and not before_long):
        return False, f"day before a {nxt} session", None
    if soreness_guard and i + 2 < len(days) and E.day_category(days[i + 2]) == "key":
        return False, "key ride 2 days later while lifting is new (soreness peaks ~48 h after)", None
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(days) and any(e.get("trip") for e in days[j].get("events", [])):
            return False, "trip within a day", None
    if any(abs(i - t) < 2 for t in taken):
        return False, "less than 48 h from another lift", None
    ride = next((s for s in day["sessions"] if s["kind"] == "ride" and s.get("start") is not None), None)
    busy = E.busy_intervals(day, cfg)
    if ride is None:
        first = E.tmin(cfg.get("lift_default_start") or DEFAULT_START)
    else:
        gap = 180 if ride["category"] == "key" else 30
        busy = busy + [(ride["start"], ride["end"] + E.SHOWER_MIN, "ride")]
        first = int(math.ceil((ride["end"] + gap) / 15) * 15)
    first = max(first, int(math.ceil((cfg.get("not_before") or {}).get(day["date"], 0) / 15) * 15))
    mins = cfg.get("lift_min", LIFT_MIN)
    for st in range(first, E.tmin(E.LATEST_END) - mins + 1, 15):
        if E.fits(st, st + mins, busy):
            return True, "", st
    return False, f"no free {mins}-min window", None


def lifts_per_week(settings: dict, monday: str) -> tuple[int, str | None]:
    """(lifts wanted, phase note) for the week starting `monday`. settings.lift_phases:
    [{"from": "YYYY-MM-DD", "per_week": 1, "note": "..."}]; the latest phase whose week has started wins."""
    n, note = 2, None
    for ph in sorted(settings.get("lift_phases") or [], key=lambda p: p["from"]):
        if E.monday_of(ph["from"]) <= monday:
            n, note = int(ph["per_week"]), ph.get("note")
    return n, note


def plan_lifts(days: list[dict], *, today: date, cfg: dict, settings: dict, completed: list[dict],
               previous: list[dict], load: dict[str, float], readiness: float | None,
               progress: dict[str, int], generated_week0: date | None = None,
               missed: list[str] | None = None, notes: list[str] | None = None,
               reserved_keys: frozenset[str] | set[str] = frozenset(), now_min: int | None = None) -> list[dict]:
    """Desired lifts over the plan horizon. `completed`: strength activities (date) this horizon/week;
    `previous`: last run's desired lifts (sticky dates); `progress`: completed sessions per template;
    `missed`: dates of planned lifts that passed undone (a catch-up is placed in the same week if a day
    passes the rules, never doubled up); `notes`: collects week-level messages (a lift that did not fit);
    `reserved_keys`: plan keys of past lifts (never reused, so a new lift can't overwrite history);
    `now_min`: minutes since midnight now, so a lift placed today starts at least 30 min from now."""
    idx = {d["date"]: i for i, d in enumerate(days)}
    t = today.isoformat()
    upper = bool(settings.get("upper_body_cleared"))
    pt = settings.get("pt_block") or []
    minutes = LIFT_MIN + (int(settings.get("pt_minutes", 20)) if pt else 0)
    cfg = {**cfg, "lift_weekdays_only": bool(settings.get("lift_weekdays_only")), "lift_min": minutes,
           "lift_rest_weekdays": list(settings.get("lift_rest_weekdays") or []),
           "lift_blackouts": list(settings.get("lift_blackouts") or []),
           "not_before": {t: now_min + 30} if now_min is not None else {}}
    ratio = acwr(load, today)
    prev_by_week: dict[str, list[dict]] = {}
    for p in previous:
        prev_by_week.setdefault(E.monday_of(p["date"]), []).append(p)
    done_dates = {c["date"] for c in completed}
    last_template = max(completed, key=lambda c: c["date"]).get("template") if completed else None
    tmpl_cycle = "B" if last_template == "A" else "A"
    sim = {"A": progress.get("A", 0), "B": progress.get("B", 0)}  # projected sessions per template
    out: list[dict] = []
    taken = [idx[d] for d in done_dates if d in idx]
    weeks = sorted({E.monday_of(d["date"]) for d in days})
    week0 = generated_week0 or date.fromisoformat(weeks[0])
    for mon in weeks:
        wk_days = [i for i, d in enumerate(days) if E.monday_of(d["date"]) == mon]
        per_week, phase_note = lifts_per_week(settings, mon)
        want = per_week - sum(1 for d in done_dates if E.monday_of(d) == mon)
        reasons_week = [f"{phase_note}: {per_week} lift{'' if per_week == 1 else 's'} a week"] if phase_note else []
        wk_missed = sorted(d for d in (missed or []) if E.monday_of(d) == mon and d < t and d not in done_dates)
        if ratio is not None and ratio > 1.5 and mon == E.monday_of(t):
            want = min(want, 1)
            reasons_week.append(f"7d:28d load ratio {ratio:.2f} > 1.5: one lift this week")
        if want <= 0:
            continue
        picks: list[tuple[int, int, list[str], str | None]] = []
        soft: list[tuple[int, int, list[str], str | None, str]] = []  # kept dates refused only by the soreness guard
        intro = sim["A"] + sim["B"] < INTRO_SESSIONS
        # 1) keep last run's dates when still valid (no flapping); lifts Ian moved by hand are pinned (and win a
        #    full week, so a lift he drags into another week replaces one there rather than being dropped)
        for p in sorted(prev_by_week.get(mon, []), key=lambda p: (not p.get("pinned"), p["date"])):  # yours first
            ds = p["date"]
            if ds < t or ds not in idx or len(picks) >= want:
                continue
            ok, why, st = _valid(idx[ds], days, taken + [q[0] for q in picks], cfg, soreness_guard=intro,
                                 before_long=True)
            if p.get("pinned"):
                st = st if st is not None else E.tmin(DEFAULT_START)
                picks.append((idx[ds], st, ["date set by you (Garmin Connect or Google Calendar)"], p.get("plan_key")))
            elif ok:
                picks.append((idx[ds], st, ["kept from the previous plan"], p.get("plan_key")))
            else:
                ok2, _, st2 = (_valid(idx[ds], days, taken + [q[0] for q in picks], cfg, before_long=True)
                               if intro else (False, "", None))
                if ok2:
                    soft.append((idx[ds], st2, ["kept from the previous plan",
                                                "no other day this week avoids a key ride 2 days later"],
                                 p.get("plan_key"), f"moved off {ds}: {why}"))
                else:
                    reasons_week.append(f"moved off {ds}: {why}")
        # 2) fill: key days first (lift after the ride, so easy days stay easy), then easy, moderate, and days
        #    with no ride. While lifting is new, first without the day two days before a key ride. Last resort:
        #    the day before a long ride, as a light session.
        passes = ([(True, False)] if intro else []) + [(False, False), (False, True)]
        soft_done = False
        for guard, before_long in passes:
            if not guard and not soft_done:  # a refused kept date beats a brand-new one when the guard gives way
                soft_done = True
                for q in soft:
                    if len(picks) < want and _valid(q[0], days, taken + [p[0] for p in picks], cfg,
                                                    before_long=True)[0]:
                        picks.append(q[:4])
            for want_cat in FILL_ORDER:
                for i in wk_days:
                    if len(picks) >= want:
                        break
                    if days[i]["date"] < t or any(p[0] == i for p in picks) or E.day_category(days[i]) != want_cat:
                        continue
                    ok, why, st = _valid(i, days, taken + [p[0] for p in picks], cfg, soreness_guard=guard,
                                         before_long=before_long)
                    if ok:
                        why = [FILL_REASON[want_cat]]
                        if wk_missed:
                            why.append(f"catch-up for the missed {wk_missed[-1]} lift")
                        picks.append((i, st, why, None))
        reasons_week += [q[4] for q in soft if all(p[0] != q[0] for p in picks)]
        if len(picks) < want and notes is not None and (wk_missed or len(wk_days) == 7):
            msg = f"Week of {mon}: {len(picks)} of {want} lift{'' if want == 1 else 's'} placed"
            if wk_missed:
                msg += f"; the missed {', '.join(wk_missed)} lift is dropped, not doubled up"
            notes.append(msg + (f" ({'; '.join(reasons_week)})" if reasons_week else ""))
        used_keys = {p[3] for p in picks if p[3]} | set(reserved_keys)
        free = (f"lift-{mon}-{n}" for n in range(1, 9) if f"lift-{mon}-{n}" not in used_keys)
        for i, st, why, key in sorted(picks):
            ds = days[i]["date"]
            weeks_in = max(0, (date.fromisoformat(mon) - week0).days // 7)
            deload = weeks_in % 4 == 3
            template = tmpl_cycle
            tmpl_cycle = "B" if tmpl_cycle == "A" else "A"
            level = level_for(sim[template])
            sim[template] += 1
            variant, reasons = "normal", list(why) + reasons_week
            if level == 0:
                reasons.append(f"first {template} session: intro level")
            if deload:
                reasons.append("deload week (every 4th week)")
            if ratio is not None and ratio > 1.3 and (date.fromisoformat(ds) - today).days < 3:
                variant = "light"
                reasons.append(f"7d:28d load ratio {ratio:.2f} > 1.3: light session")
            if i + 1 < len(days) and E.day_category(days[i + 1]) == "long":
                variant = "light"
                reasons.append("long ride tomorrow: light session")
            yday = (date.fromisoformat(ds) - timedelta(days=1)).isoformat()
            if load.get(yday, 0) >= 100 and ds > t:
                variant = "light"
                reasons.append("hard ride the day before (TSS >= 100): light session")
            if ds == t and readiness is not None and readiness < 25:
                variant = "light"
                reasons.append(f"Garmin readiness {readiness:.0f} < 25: light session")
            cal_lift = next((w for w in days[i].get("workout_slots", []) if w["sport"] == "strength_training"), None)
            if cal_lift:  # your calendar is the source of truth for when you lift
                st = E.tmin(cal_lift["start"])
                reasons.append(f"time from your Google Calendar ({cal_lift['start']})")
            elif ds in (settings.get("lift_start_by_date") or {}):
                st = E.tmin(settings["lift_start_by_date"][ds])
                reasons.append(f"time set by you ({settings['lift_start_by_date'][ds]})")
            equipment = (settings.get("lift_equipment_by_date") or {}).get(ds) or settings.get("lift_equipment", "gym")
            if equipment == "home":
                reasons.append("bodyweight session at home")
            forced = (settings.get("lift_variant_by_date") or {}).get(ds)
            if forced in ("normal", "light") and forced != variant:
                variant = forced
                reasons = [r for r in reasons if not r.endswith(": light session")]
                reasons.append(f"{forced} session set by you")
            ex = prescription(template, level, variant, deload, upper, equipment,
                              settings.get("strength_weights"), pt)
            ex, swapped = apply_swaps(ex, settings.get("exercise_swaps"), ds)
            if equipment != "home":
                ex = arrange_gym(ex)
            if swapped:
                sw = settings["exercise_swaps"]
                reasons.append(f"{sw.get('reason') or 'temporary exercise swaps'} (until {sw['until']})")
            spec = {"template": template, "level": level, "variant": variant, "deload": deload, "upper": upper,
                    "equipment": equipment, "exercises": ex}
            out.append({"plan_key": key or next(free), "kind": "lift", "date": ds, "start": st, "end": st + minutes,
                        "minutes": minutes, **spec, "spec_hash": spec_hash(spec), "reasons": reasons,
                        "name": f"FP · Lift {template}{level or ' intro'}{' light' if variant == 'light' else ''}"
                                f"{' deload' if deload else ''} · "
                                f"{'Home bodyweight' if equipment == 'home' else 'Full body' if upper else 'Lower+Core'}"
                                f"{' + PT' if pt else ''}"})
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
                "description": f"{ex['label']}" + (f" · {ex['weight_lb']:g} lb" if ex.get("weight_lb") else "")
                + (f" · {ex['rir']} reps in reserve" if ex.get("rir") else "")
                + (f" · {ex['note']}" if ex.get("note") else "")}
        weight = 0.0 if ex["category"] == "PUSH_UP" else ex.get("weight_lb")  # push-ups are always bodyweight
        if weight is not None:
            work |= {"weightValue": weight, "weightUnit": LB}
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
