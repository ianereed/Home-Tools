"""Fuel-plan engine: sessions -> schedule -> daily energy/macros -> meals.

Pure functions over a snapshot dict (see out/snapshot.json); no I/O here.

Method (open re-implementation of the Hexis / Fuelin ideas, see journal-167):
  * RMR: Mifflin-St Jeor (what Hexis reportedly uses). Katch-McArdle shown for reference.
  * Non-exercise day = RMR x PAL (1.35; cross-check against Garmin's totals on no-workout days).
  * Session energy is ACTIVE kcal only (resting metabolism removed):
      planned  -> kJ from Coach step power targets (midpoint)
      actual   -> kJ = avg power x timer time from the bike's power meter (falls back to Garmin
                  calories - bmr_calories when a ride has no power)
    gross kcal = kJ / (0.217 gross efficiency x 4.184); active = gross - RMR for the moving minutes.
    Day burn = RMR x PAL + active - the everyday movement (PAL - 1) displaced by the session.
    Completed activities replace the planned estimate for that day.
  * Weight goal: weekly deficit = pace x 3500 kcal/lb, spread by day type (most on rest/easy days,
    none on long days -- "fuel for the work required", Impey 2018), capped at 25% of the day's burn.
  * Carbs: 2.5 g/kg base + the carbohydrate the session burns (CHO fraction by intensity) +
    0.5 g/kg the day before a key/long session.
  * Protein: 2.0 g/kg/day (~170 g), spread across 4 eating occasions (>=0.4 g/kg per meal).
  * Fat: the remainder, floor 0.7 g/kg, ceiling 30% kcal. If the floor bites, carbs give way first
    (down to base + 85% of session carbs), then the deficit shrinks.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from dashboard.targets import BODY_FAT_GOALS, weight_at_body_fat

from . import foods as F

ENGINE_VERSION = "3.0"

GROSS_EFF = 0.217
PAL = 1.35
PROTEIN_G_PER_KG = 2.0
CARB_BASE_G_PER_KG = 2.5
CARB_MIN_BASE_G_PER_KG = 2.0
FUEL_TOMORROW_G_PER_KG = 0.5
FAT_FLOOR_G_PER_KG = 0.7
FAT_CEIL_PCT = 0.30
DEFICIT_CAP_PCT = 0.25
KCAL_PER_LB = 3500
FIBER_MIN = 25
SODIUM_CEIL = 2300
SATFAT_PCT = 0.06
CHOL_MAX = None  # display-only unless settings limits.cholesterol_mg sets a number

EARLIEST_RIDE = "09:00"
PREFERRED_RIDE = "10:00"
LATEST_END = "19:30"
SHOWER_MIN = 30
EVENT_BUFFER_MIN = 30
TRAVEL_RULES: list[dict] = []  # [{"match": "<location substring>", "pre_min": 60}] -- supplied from gitignored settings

DEFAULT_SLOT_TIMES = {"B": "07:30", "L": "12:45", "S": "15:45", "D": "19:00"}
SLOT_NAMES = {"B": "Breakfast", "L": "Lunch", "S": "Snack", "D": "Dinner"}
DINNER_FLOOR = 17 * 60 + 30

CHO_FRACTION = {
    "ANAEROBIC_SPEED": 0.65, "ANAEROBIC_CAPACITY": 0.75, "VO2MAX": 0.8, "LACTATE_THRESHOLD": 0.75,
    "TEMPO": 0.65, "BASE": 0.5, "AEROBIC_LOW_SHORTAGE_BASE": 0.5, "LONG_WORKOUT": 0.5,
    "RECOVERY": 0.45, "BIKEPACKING": 0.5, "STRENGTH": 0.6,
}
KEY_TYPES = {"ANAEROBIC_SPEED", "ANAEROBIC_CAPACITY", "VO2MAX", "LACTATE_THRESHOLD", "TEMPO"}
DEFICIT_WEIGHT = {"rest": 1.0, "easy": 0.8, "moderate": 0.6, "key": 0.35, "long": 0.0}


# ------------------------------------------------------------------ config from the snapshot
def config(snap: dict) -> dict:
    """Per-run knobs. Personal values (wake time, travel buffers, limits) arrive in the snapshot
    from gitignored settings / Garmin / clinical constraints -- never hardcoded here."""
    s = snap.get("settings", {})
    c = snap.get("constraints", {})
    limits = {"sodium_mg": SODIUM_CEIL, "cholesterol_mg": CHOL_MAX, "satfat_pct": SATFAT_PCT,
              **(s.get("limits") or {})}
    slots = dict(DEFAULT_SLOT_TIMES)
    earliest_food = c.get("earliest_food")           # "HH:MM": wake + medication gap
    if earliest_food and tmin(slots["B"]) < tmin(earliest_food):
        slots["B"] = earliest_food
    earliest_ride = max(tmin(s.get("earliest_ride", EARLIEST_RIDE)),
                        tmin(earliest_food) + 90 if earliest_food else 0)
    return {"limits": limits, "slots": slots, "earliest_food": earliest_food,
            "earliest_ride": earliest_ride, "preferred_ride": tmin(s.get("preferred_ride", PREFERRED_RIDE)),
            "travel_rules": s.get("travel_rules", TRAVEL_RULES)}


DEFAULT_CFG = None  # filled lazily for callers that don't pass a config


def _cfg(cfg):
    global DEFAULT_CFG
    if cfg is not None:
        return cfg
    if DEFAULT_CFG is None:
        DEFAULT_CFG = config({})
    return DEFAULT_CFG


def monday_of(ds: str) -> str:
    x = d(ds)
    return (x - timedelta(days=x.weekday())).isoformat()


def pace_fn(snap: dict, pace):
    """pace: a number (lb/week, constant) or 'plan' (settings pace_schedule: [[from_date, lb_wk], ...])."""
    if pace != "plan":
        return lambda ds: float(pace)
    sched = sorted((snap.get("settings", {}).get("pace_schedule") or [[snap["plan_start"], 0.5]]), key=lambda x: x[0])

    def f(ds):
        cur = sched[0][1]
        for frm, v in sched:
            if ds >= frm:
                cur = v
        return float(cur)
    return f


# ------------------------------------------------------------------ time helpers
def tmin(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def hhmm(minutes: float) -> str:
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def d(s: str) -> date:
    return date.fromisoformat(s)


# ------------------------------------------------------------------ body
def age_on(birth: str, on: date) -> int:
    b = d(birth)
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def rmr_mifflin(kg: float, cm: float, age: int, male: bool = True) -> float:
    return 10 * kg + 6.25 * cm - 5 * age + (5 if male else -161)


def rmr_katch(ffm_kg: float) -> float:
    return 370 + 21.6 * ffm_kg


def body_targets(dexa: dict) -> list[dict]:
    """The body-fat goals (dashboard.targets.BODY_FAT_GOALS) holding the scan's lean and bone mass.
    % = fat / (fat + lean) tissue, DEXA-style."""
    lean, bone, fat = dexa["lean_mass_kg"], dexa["bone_mass_kg"], dexa["fat_mass_kg"]
    out = []
    for label, pct in BODY_FAT_GOALS:
        total = weight_at_body_fat(lean, bone, pct)
        f2 = total - lean - bone
        out.append({"label": f"{label} · {pct * 100:.0f}%", "pct": pct, "lean_kg": lean, "fat_kg": f2,
                    "total_kg": total, "total_lb": total * 2.20462, "fat_loss_kg": fat - f2, "lean_gain_kg": 0.0,
                    "scale_change_kg": total - dexa["total_mass_kg"]})
    return out


def timeline(fat_loss_kg: float, start: date, race: date, pre_race_lb_wk: float, post_lb_wk: float) -> date:
    """Date the fat-loss target is reached. Assumes ~100% of the deficit comes off as fat (high protein + lifting);
    real-world is closer to 75-85%, so treat as an optimistic bound."""
    kg_per_lb = 0.453592
    pre_weeks = (race - start).days / 7
    lost_pre = pre_race_lb_wk * kg_per_lb * pre_weeks
    if lost_pre >= fat_loss_kg:
        return start + timedelta(weeks=fat_loss_kg / (pre_race_lb_wk * kg_per_lb))
    remaining = fat_loss_kg - lost_pre
    if post_lb_wk <= 0:
        return None
    return race + timedelta(weeks=remaining / (post_lb_wk * kg_per_lb))


# ------------------------------------------------------------------ sessions
def flatten_steps(steps) -> list[tuple[float, float]]:
    out = []
    for s in steps:
        if isinstance(s, dict):
            inner = flatten_steps(s["steps"])
            out.extend(inner * s["repeat"])
        else:
            dur, lo, hi = s
            out.append((dur, (lo + hi) / 2))
    return out


def ride_session(w: dict, ftp: float, projected: bool = False) -> dict:
    if "steps" in w:
        flat = flatten_steps(w["steps"])
        secs = sum(x[0] for x in flat)
        kj = sum(x[0] * x[1] for x in flat) / 1000
    else:
        secs = w["duration_s"]
        kj = w["avg_power_w"] * secs / 1000
    minutes = secs / 60
    avg_w = kj * 1000 / secs
    gross = kj / (GROSS_EFF * 4.184)
    t = w["type"]
    if t == "BIKEPACKING" or t == "LONG_WORKOUT" or minutes >= 150:
        cat = "long"
    elif t in KEY_TYPES:
        cat = "key"
    elif minutes >= 75:
        cat = "moderate"
    else:
        cat = "easy"
    if t == "BIKEPACKING":
        gph = 50
    elif minutes >= 150:
        gph = 60
    elif minutes >= 75:
        gph = 60 if cat == "key" else 40
    else:
        gph = 0
    return {
        "kind": "ride", "name": w.get("name", t.title()), "type": t, "description": w.get("description", ""),
        "minutes": minutes, "kj": kj, "avg_w": avg_w, "if_avg": avg_w / ftp, "gross_kcal": gross,
        "cho_frac": CHO_FRACTION.get(t, 0.55), "category": cat, "projected": projected,
        "on_bike_carb_g": gph * minutes / 60, "on_bike_gph": gph, "assumption": w.get("assumption"),
        "te": w.get("te"), "steps": w.get("steps"),
    }


def strength_session(kg: float, minutes: int = 45) -> dict:
    gross = 4.5 * kg * minutes / 60  # MET 4.5, vigorous-ish resistance training
    return {"kind": "strength", "name": "Strength", "type": "STRENGTH", "minutes": minutes, "gross_kcal": gross,
            "cho_frac": CHO_FRACTION["STRENGTH"], "category": "easy", "on_bike_carb_g": 0, "projected": False}


NON_WORKOUT_TYPES = {"walking"}  # everyday walking is already inside PAL


def is_cycling(t: str) -> bool:
    return "cycling" in t or "biking" in t


def actual_session(a: dict, ftp: float, rmr: float) -> dict:
    """A completed Garmin activity -> session with ACTIVE kcal (power meter first, Garmin HR model fallback)."""
    minutes = a["duration_s"] / 60
    moving = a.get("moving_s") or a["duration_s"]
    garmin_active = a["kcal"] - a.get("bmr_kcal", 0)
    if a.get("avg_power_w"):
        kj = a["avg_power_w"] * a["duration_s"] / 1000
        gross = kj / (GROSS_EFF * 4.184)
        active = gross - rmr / 86400 * moving
        source = "power meter"
    else:
        kj = None
        gross = a["kcal"]
        active = garmin_active
        source = "Garmin estimate (no power recorded)"
    if_np = a["np_w"] / ftp if a.get("np_w") else None
    cyc = is_cycling(a["type"])
    if not cyc:
        cho, cat = 0.5, ("long" if minutes >= 150 else "easy")
    else:
        x = if_np or 0.7
        cho = 0.5 if x < 0.75 else 0.6 if x < 0.85 else 0.7 if x < 0.95 else 0.8
        cat = ("long" if minutes >= 150 else "key" if (if_np or 0) >= 0.85 and minutes >= 30
               else "moderate" if minutes >= 75 else "easy")
    kind = "ride" if cyc else ("strength" if "strength" in a["type"] else "other")
    st = tmin(a["start"])
    return {"kind": kind, "name": a["name"], "type": a["type"], "minutes": minutes, "kj": kj, "gross_kcal": gross,
            "active_kcal": max(0.0, active), "garmin_active_kcal": garmin_active, "energy_source": source,
            "if_np": if_np, "avg_w": a.get("avg_power_w"), "np_w": a.get("np_w"), "cho_frac": cho, "category": cat,
            "completed": True, "projected": False, "on_bike_carb_g": 0, "start": st, "end": st + round(minutes),
            "reason": "completed"}


def actual_days(acts: list[dict], dates: list[str], snap: dict) -> list[dict]:
    rmr = baseline_kcal(snap)["rmr"]
    ftp = snap["athlete"]["ftp_w"]
    out = []
    for ds in dates:
        sess = [actual_session(a, ftp, rmr) for a in acts if a["date"] == ds and a["type"] not in NON_WORKOUT_TYPES]
        out.append({"date": ds, "dow": d(ds).strftime("%a"), "sessions": sess, "source": "actual", "note": None, "events": []})
    return out


def build_days(snap: dict) -> list[dict]:
    """One dict per plan day. Session precedence: completed activity > calendar trip/extra >
    Coach (published) > projected/generated (snap['projected_workouts']) > rest."""
    start = d(snap["plan_start"])
    ftp = snap["athlete"]["ftp_w"]
    coach = {w["date"]: w for w in snap["coach_workouts"]}
    extras = {w["date"]: w for w in snap.get("planned_extras", [])}
    projected = {w["date"]: w for w in snap.get("projected_workouts", [])}
    window_end = d(snap["coach_plan"]["window_end"]) if snap.get("coach_plan", {}).get("window_end") else None
    legacy_mirror = "projected_workouts" not in snap
    days = []
    for i in range(snap["plan_days"]):
        day = start + timedelta(days=i)
        ds = day.isoformat()
        sessions, source, notes = [], "coach", []
        if ds in extras:
            x = extras[ds]
            sessions.append(ride_session(x, ftp))
            source = "calendar"
            notes.append({"kind": "calendar", "text": "Session from your calendar replaces the planned ride."})
            if x.get("assumption"):
                notes.append({"kind": "assumption", "text": x["assumption"]})
        elif ds in coach:
            w = coach[ds]
            if not w.get("rest"):
                sessions.append(ride_session(w, ftp))
        elif ds in projected:
            w = projected[ds]
            source = w.get("source", "projected")
            if not w.get("rest"):
                sessions.append(ride_session(w, ftp, projected=True))
                sessions[-1]["source"] = source
            if w.get("note"):
                notes.append({"kind": source, "text": w["note"]})
        elif legacy_mirror and window_end and day > window_end:
            # Prototype fallback: mirror the same weekday from the published week.
            src = (day - timedelta(days=7)).isoformat()
            source = "projected"
            if src in coach and not coach[src].get("rest"):
                sessions.append(ride_session(coach[src], ftp, projected=True))
            notes.append({"kind": "projected", "text": "Projected: mirrors the same weekday of Coach's published week."})
        else:
            source = "rest" if ds in coach or window_end is None or day <= window_end else source
        actual = actual_days(snap.get("activities_plan_window", []), [ds], snap)[0]["sessions"]
        if actual:
            done_kinds = {s["kind"] for s in actual}
            planned_ride = next((s for s in sessions if s["kind"] == "ride"), None)
            if planned_ride and planned_ride["category"] in ("key", "long"):
                # A completed ride that is clearly the planned session keeps the plan's intent: short sprint
                # work has a modest IF but is still a key day (fueling, deficit weight, lift spacing).
                main = max((a for a in actual if a["kind"] == "ride"), key=lambda a: a["minutes"], default=None)
                if main and main["minutes"] >= 0.6 * planned_ride["minutes"] and (main.get("if_np") or 0) >= 0.75:
                    main["category"] = planned_ride["category"]
                    main["cho_frac"] = max(main["cho_frac"], planned_ride["cho_frac"])
                    main["planned_as"] = planned_ride["name"]
            sessions = [s for s in sessions if s["kind"] not in done_kinds] + actual
            source = "actual"
            for a in actual:
                if a["kind"] == "ride" and a["energy_source"] != "power meter":
                    notes.append({"kind": "data", "text": f"{a['name']}: no power recorded; using Garmin's estimate."})
        events = [e for e in snap.get("calendar", []) if e["date"] == ds]
        for e in events:
            if e.get("meal_out"):
                notes.append({"kind": "meal_out", "text": f"Eating out: {e['title']}."})
            if e.get("packed_lunch"):
                notes.append({"kind": "packed", "text": f"{e['title']}: packed lunch; ride after."})
            if e.get("trip"):
                notes.append({"kind": "trip", "text": f"Trip day: {e['title']}."})
            if e.get("workout") and not sessions:
                notes.append({"kind": "conflict", "text": f"'{e['title']}' is on your calendar on a rest day. "
                              "If you go, keep it easy and add ~70 g carbs around it."})
        days.append({"date": ds, "dow": day.strftime("%a"), "sessions": sessions, "source": source,
                     "note": None, "notes": notes, "events": events})
    return days


def day_category(day: dict) -> str:
    order = ["rest", "easy", "moderate", "key", "long"]
    cats = [s["category"] for s in day["sessions"] if s["kind"] == "ride"]
    if not cats:
        return "rest" if not day["sessions"] else "easy"
    return max(cats, key=order.index)


# ------------------------------------------------------------------ scheduling
def busy_intervals(day: dict, cfg: dict | None = None) -> list[tuple[int, int, str]]:
    rules = _cfg(cfg)["travel_rules"]
    out = []
    for e in day["events"]:
        if not e.get("busy") or e.get("all_day"):
            continue
        loc = (e.get("location") or "").lower()
        pre = next((r["pre_min"] for r in rules if r["match"].lower() in loc), EVENT_BUFFER_MIN)
        out.append((tmin(e["start"]) - pre, tmin(e["end"]) + EVENT_BUFFER_MIN, e["title"]))
    return out


def fits(start: int, end: int, busy) -> bool:
    return all(end <= b0 or start >= b1 for b0, b1, _ in busy)


def schedule_ride(day: dict, ride: dict, cfg: dict | None = None) -> dict | None:
    cfg = _cfg(cfg)
    busy = busy_intervals(day, cfg)
    dur = math.ceil(ride["minutes"])
    best = None
    first = int(math.ceil(cfg["earliest_ride"] / 15) * 15)
    for start in range(first, tmin(LATEST_END) - dur + 1, 15):
        end = start + dur
        if not fits(start, end + SHOWER_MIN, busy):
            continue
        score = abs(start - cfg["preferred_ride"])
        if best is None or score < best[0]:
            best = (score, start, end)
    if best is None:
        return None
    reason = "preferred slot" if best[1] == cfg["preferred_ride"] else "moved to fit around calendar"
    return {"start": best[1], "end": best[2], "reason": reason}


def schedule(days: list[dict], kg: float, cfg: dict | None = None, place_lifts: bool = True) -> None:
    """Assign ride times, then (unless a strength plan is supplied separately) place 2 lifts per 7-day block."""
    cfg = _cfg(cfg)
    for day in days:
        for s in day["sessions"]:
            if s["kind"] != "ride" or s.get("completed"):
                continue
            if s["type"] == "BIKEPACKING":
                s["start"], s["end"], s["reason"] = tmin("08:00"), tmin("08:00") + int(s["minutes"]) + 120, "trip day (moving time + stops)"
                continue
            slot = schedule_ride(day, s, cfg)
            if slot:
                s["start"], s["end"], s["reason"] = slot["start"], slot["end"], slot["reason"]
            else:
                s["start"], s["end"], s["reason"] = None, None, "no free window -- needs a manual call"

    if not place_lifts:
        return

    def cat(i):
        return day_category(days[i]) if 0 <= i < len(days) else "unknown"

    last_lift = -9
    for block in range(0, len(days), 7):
        placed = 0
        for i in range(block, min(block + 7, len(days))):
            if placed >= 2:
                break
            c, nxt = cat(i), cat(i + 1)
            if c in ("rest", "long") or nxt in ("key", "long") or i - last_lift < 2:
                continue
            ride = next((s for s in days[i]["sessions"] if s["kind"] == "ride" and s.get("start") is not None), None)
            if ride is None:
                continue
            busy = busy_intervals(days[i], cfg) + [(ride["start"], ride["end"] + SHOWER_MIN, "ride")]
            gap = 180 if ride["category"] == "key" else 30
            lift = strength_session(kg)
            first = math.ceil((ride["end"] + gap) / 15) * 15
            for st in range(first, tmin(LATEST_END) - lift["minutes"] + 1, 15):
                if fits(st, st + lift["minutes"], busy):
                    lift["start"], lift["end"] = st, st + lift["minutes"]
                    lift["reason"] = ("right after the easy ride, which doubles as the warm-up" if gap == 30
                                      else "3+ h after the key ride so the intervals stay fresh")
                    days[i]["sessions"].append(lift)
                    placed += 1
                    last_lift = i
                    break


# ------------------------------------------------------------------ energy + macros
def baseline_kcal(snap: dict) -> dict:
    a, x = snap["athlete"], snap["dexa"]
    age = age_on(a["birth_date"], d(snap["plan_start"]))
    mif = rmr_mifflin(snap.get("weight_kg") or x["total_mass_kg"], a["height_cm"], age, a.get("sex", "male") != "female")
    kat = rmr_katch(x["lean_mass_kg"] + x["bone_mass_kg"])
    return {"age": age, "rmr": mif, "rmr_mifflin": mif, "rmr_katch": kat, "pal": PAL, "baseline": mif * PAL}


def session_energy(s: dict, rmr: float) -> None:
    """Fill active kcal (planned sessions) and the everyday movement the session displaces."""
    rmr_min = rmr / 1440
    if not s.get("completed"):
        s["active_kcal"] = max(0.0, s["gross_kcal"] - rmr_min * s["minutes"])
        s["energy_source"] = "plan (Coach power targets)" if s["kind"] == "ride" else "plan (MET estimate)"
    s["displaced_kcal"] = (PAL - 1) * rmr_min * s["minutes"]


def _realized_prior(snap: dict, pace_at, block_monday: str) -> float:
    """Deficit already banked on this week's days before the plan starts (from logged intake).
    A day whose log looks incomplete (< 60% of what the plan would have allowed) counts as on-plan."""
    total = 0.0
    seen = set()
    for p in snap.get("week_to_date", []):
        if monday_of(p["date"]) != block_monday or p["date"] >= snap["plan_start"]:
            continue
        seen.add(p["date"])
        planned = pace_at(p["date"]) * KCAL_PER_LB / 7
        intake = p.get("intake_kcal")
        if intake is not None and intake >= 0.6 * (p["tdee"] - planned):
            total += p["tdee"] - intake
        else:
            total += planned
    m = d(block_monday)
    for i in range(7):
        ds = (m + timedelta(days=i)).isoformat()
        if ds < snap["plan_start"] and ds not in seen:
            total += pace_at(ds) * KCAL_PER_LB / 7
    return total


def plan_energy(days: list[dict], snap: dict, pace_lb_wk) -> list[dict]:
    """Daily energy + macro targets. The weight-loss deficit is allocated per Mon-Sun week, so a hard
    week's shortfall never leaks into the next one. pace_lb_wk: number or 'plan' (pace schedule)."""
    pace_at = pace_fn(snap, pace_lb_wk)
    kg = snap.get("weight_kg") or snap["dexa"]["total_mass_kg"]
    b = baseline_kcal(snap)
    rows = []
    for i, day in enumerate(days):
        cat = day_category(day)
        nxt = day_category(days[i + 1]) if i + 1 < len(days) else "rest"
        for s in day["sessions"]:
            session_energy(s, b["rmr"])
        ex_active = sum(s["active_kcal"] for s in day["sessions"])
        displaced = sum(s["displaced_kcal"] for s in day["sessions"])
        ex_net = ex_active - displaced
        tdee = b["baseline"] + ex_net
        session_carbs = sum(s["gross_kcal"] * s["cho_frac"] / 4 for s in day["sessions"])
        w = DEFICIT_WEIGHT[cat] * (0.6 if nxt in ("key", "long") else 1.0)
        rows.append({"date": day["date"], "category": cat, "next": nxt, "tdee": tdee, "ex_net": ex_net,
                     "ex_active": ex_active, "displaced": displaced,
                     "actual": any(s.get("completed") for s in day["sessions"]),
                     "ex_gross": sum(s["gross_kcal"] for s in day["sessions"]),
                     "session_carbs": session_carbs, "w": w, "cap": DEFICIT_CAP_PCT * tdee})

    blocks: dict[str, list[dict]] = {}
    for r in rows:
        r["pace"] = pace_at(r["date"])
        blocks.setdefault(monday_of(r["date"]), []).append(r)
    for mon, brows in blocks.items():
        target = sum(r["pace"] for r in brows) * KCAL_PER_LB / 7
        if brows[0]["date"] == snap["plan_start"] or monday_of(snap["plan_start"]) == mon:
            planned_prior = sum(pace_at((d(mon) + timedelta(days=i)).isoformat()) for i in range(7)
                                if (d(mon) + timedelta(days=i)).isoformat() < snap["plan_start"]) * KCAL_PER_LB / 7
            target = max(0.0, target + planned_prior - _realized_prior(snap, pace_at, mon))

        def alloc(k, brows=brows):
            return [min(r["cap"], r["w"] * k) for r in brows]

        lo, hi = 0.0, 5000.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if sum(alloc(mid)) < target:
                lo = mid
            else:
                hi = mid
        for r, dfc in zip(brows, alloc(hi) if target > 0 else [0.0] * len(brows)):
            r["deficit_planned"] = dfc
            r["week_target"] = target

    P = PROTEIN_G_PER_KG * kg
    limits = config(snap)["limits"]
    for r, day in zip(rows, days):
        bonus = FUEL_TOMORROW_G_PER_KG * kg if r["next"] in ("key", "long") and r["category"] != "long" else 0.0
        kcal, C, Fg, flags = _macros(r["tdee"] - r["deficit_planned"], P, kg, r["session_carbs"], bonus)
        r.update({"kcal": kcal, "deficit": r["tdee"] - kcal, "protein_g": P, "carb_g": C, "fat_g": Fg,
                  "fuel_tomorrow_g": bonus, "fiber_target": max(FIBER_MIN, 14 * kcal / 1000),
                  "satfat_max_g": limits["satfat_pct"] * kcal / 9, "sodium_max_mg": limits["sodium_mg"],
                  "chol_max_mg": limits.get("cholesterol_mg"), "flags": flags, "carb_g_per_kg": C / kg})
        r["logged_goal"] = logged_goal(r, day, b, P, kg, bonus)
    return rows


def _macros(kcal: float, P: float, kg: float, session_carbs: float, bonus: float) -> tuple[float, float, float, list[str]]:
    """Carbs + fat for a kcal target (protein fixed). Returns (kcal, carbs, fat, flags); kcal only rises (fat floor)."""
    C = CARB_BASE_G_PER_KG * kg + session_carbs + bonus
    c_min = CARB_MIN_BASE_G_PER_KG * kg + 0.85 * session_carbs + bonus
    f_floor = FAT_FLOOR_G_PER_KG * kg
    Fg = (kcal - 4 * P - 4 * C) / 9
    flags = []
    if Fg < f_floor:
        C = max(c_min, (kcal - 4 * P - 9 * f_floor) / 4)
        Fg = (kcal - 4 * P - 4 * C) / 9
        flags.append("carbs trimmed to make room for the fat floor")
        if Fg < f_floor - 0.01:
            Fg = f_floor
            new_kcal = 4 * P + 4 * C + 9 * Fg
            flags.append(f"deficit reduced by {new_kcal - kcal:.0f} kcal to protect carbs + fat floor")
            kcal = new_kcal
    f_ceil = FAT_CEIL_PCT * kcal / 9
    if Fg > f_ceil:
        C += (Fg - f_ceil) * 9 / 4
        Fg = f_ceil
        flags.append("fat capped at 30% of kcal; extra went to carbs")
    return kcal, C, Fg, flags


def logged_goal(r: dict, day: dict, b: dict, P: float, kg: float, bonus: float) -> dict:
    """The day's goal counting only exercise already done (what MyFitnessPal shows). Same planned deficit as the
    full target, so a skipped workout still lands on plan; each completed session adds its own energy + carbs."""
    done = [s for s in day["sessions"] if s.get("completed")]
    ex_net = sum(s["active_kcal"] - s["displaced_kcal"] for s in done)
    tdee = b["baseline"] + ex_net
    kcal, C, Fg, _ = _macros(tdee - min(r["deficit_planned"], DEFICIT_CAP_PCT * tdee), P, kg,
                             sum(s["gross_kcal"] * s["cho_frac"] / 4 for s in done), bonus)
    return {"kcal": kcal, "carb_g": C, "protein_g": P, "fat_g": Fg, "fiber_target": max(FIBER_MIN, 14 * kcal / 1000),
            "tdee": tdee, "ex_net": ex_net, "sessions_done": len(done),
            "sessions_pending": len(day["sessions"]) - len(done)}


# ------------------------------------------------------------------ meal timing
def meal_slots(day: dict, cfg: dict | None = None) -> list[dict]:
    cfg = _cfg(cfg)
    earliest = tmin(cfg["earliest_food"]) if cfg.get("earliest_food") else 0
    slots = [{"slot": k, "time": tmin(v), "role": "", "mode": "home"} for k, v in cfg["slots"].items()]
    trip = any(e.get("trip") for e in day["events"])
    rides = sorted([s for s in day["sessions"] if s["kind"] == "ride" and s.get("start") is not None], key=lambda s: s["start"])
    lifts = [s for s in day["sessions"] if s["kind"] == "strength"]
    for e in day["events"]:
        if e.get("meal_out"):
            for sl in slots:
                if sl["slot"] == "D":
                    sl["mode"], sl["role"] = "out", f"out: {e['title']}"
                    sl["time"] = tmin(e["start"]) + 30
                    # pull the snack earlier so there's a real gap
                    for s2 in slots:
                        if s2["slot"] == "S" and s2["time"] > tmin(e["start"]) - 150:
                            s2["time"] = tmin(e["start"]) - 150
        if e.get("packed_lunch"):
            for sl in slots:
                if sl["slot"] == "L":
                    sl["mode"], sl["role"], sl["time"] = "packed", "packed", 12 * 60
                if sl["slot"] == "B":
                    sl["time"] = min(sl["time"], tmin(e["start"]) - 45)
    if trip:
        for sl in slots:
            sl["mode"] = "trip"
        return slots

    raw_busy = [(tmin(e["start"]), tmin(e["end"]), e["title"]) for e in day["events"]
                if e.get("busy") and not e.get("all_day") and not e.get("meal_out")]
    home = [s for s in slots if s["mode"] in ("home", "packed")]

    def tag(sl, text):
        sl["role"] = (sl["role"] + "; " if sl["role"] else "") + text

    # Training blocks: a lift that follows a ride within 45 min shares one recovery meal with it.
    blocks = []
    for ride in rides:
        blocks.append({"start": ride["start"], "end": ride["end"], "what": "ride"})
    for lift in lifts:
        if lift.get("start") is None:
            continue
        joined = next((b for b in blocks if 0 <= lift["start"] - b["end"] <= 45), None)
        if joined:
            joined["end"], joined["what"] = lift["end"], "ride+lift"
        else:
            blocks.append({"start": lift["start"], "end": lift["end"], "what": "lift"})
    for blk in sorted(blocks, key=lambda b: b["start"]):
        start, end, what = blk["start"], blk["end"], blk["what"]
        before = [s for s in home if s["time"] <= start]
        p = None
        if what != "lift" and before:
            p = before[-1]
            need = 30 if p["slot"] == "S" else 90
            if p["time"] > start - need:
                p["time"] = max(earliest, start - need)
            tag(p, "pre-ride")
        after = [s for s in home if s["time"] >= start and s is not p]
        if after:
            q = after[0]
            target = end + (20 if what == "ride" else 30)
            if q["slot"] == "D":
                target = max(target, DINNER_FLOOR)
            if q["time"] < target or q["time"] - target > 60:
                q["time"] = target
            tag(q, {"ride": "post-ride recovery", "lift": "post-lift (40 g+ protein)",
                    "ride+lift": "post-ride + lift recovery (40 g+ protein)"}[what])
    # a meal that lands inside an appointment moves just before it (dinner: just after)
    for sl in home:
        for b0, b1, _ in raw_busy:
            if b0 <= sl["time"] < b1 and sl["mode"] == "home":
                sl["time"] = b1 + 15 if sl["slot"] == "D" else b0 - 15
    for sl in slots:
        sl["time"] = max(earliest, int(round(sl["time"] / 5) * 5))
    slots.sort(key=lambda s: s["time"])
    return slots


# ------------------------------------------------------------------ meal building
def nutrients(ingr: dict) -> dict:
    tot = dict(kcal=0.0, p=0.0, c=0.0, f=0.0, fiber=0.0, sat=0.0, na=0.0, k=0.0, chol=0.0)
    for name, g in ingr.items():
        food = F.FOODS[name]
        kcal, p, c, f, fib, sat, na, k = food[:8]
        r = g / 100
        tot["chol"] += food.chol * r
        tot["kcal"] += kcal * r
        tot["p"] += p * r
        tot["c"] += c * r
        tot["f"] += f * r
        tot["fiber"] += fib * r
        tot["sat"] += sat * r
        tot["na"] += na * r
        tot["k"] += k * r
    return tot


def build_meal(meal: dict, target: dict) -> dict:
    """Scale the meal's levers so it hits target protein/carb/fat (Gauss-Seidel, clamped).

    If a lever hits its portion cap and the meal is still short, a side is added
    (fruit / rice for carbs, oil or almonds for fat, yogurt for protein) and the levers re-solve.
    """
    levers = meal["levers"]
    idx = {"p": 1, "c": 2, "f": 3}
    sides: dict[str, float] = {}

    def solve():
        grams = {k: 0.0 for k in ("p", "c", "f") if k in levers}

        def ingredients():
            ing = dict(meal["fixed"])
            for name, g in sides.items():
                ing[name] = ing.get(name, 0) + g
            for k, g in grams.items():
                ing[levers[k]] = ing.get(levers[k], 0) + g
            for extra, ratio in meal.get("c_extra", {}).items():
                ing[extra] = ing.get(extra, 0) + ratio * grams.get("c", 0)
            return ing

        for _ in range(12):
            for k in grams:
                grams[k] = 0.0
                have = nutrients(ingredients())[k]
                per_g = F.FOODS[levers[k]][idx[k]] / 100
                if k == "c":
                    per_g += sum(r * F.FOODS[e][2] / 100 for e, r in meal.get("c_extra", {}).items())
                gmax = F.LEVER_MAX.get(levers[k], 450)
                grams[k] = min(gmax, max(0.0, (target[k] - have) / per_g if per_g else 0.0))
        return ingredients()

    ing = solve()
    for _ in range(2):
        n = nutrients(ing)
        changed = False
        for k in ("c", "f", "p"):
            short = target[k] - n[k]
            if short <= 4:
                continue
            fb = F.FALLBACK[k]
            if k == "c":
                name, gmax = fb[meal["slot"]]
            elif k == "f":
                name, gmax = fb["oil"] if levers.get("f") == "extra-virgin olive oil" else fb["*"]
            else:
                name, gmax = fb["*"]
            have = sides.get(name, 0)
            add = min(gmax - have, short / (F.FOODS[name][idx[k]] / 100))
            if add > 1:
                sides[name] = have + add
                changed = True
        if not changed:
            break
        ing = solve()

    out = {}
    for name, g in ing.items():
        step = 1 if name in ("extra-virgin olive oil", "low-sodium soy sauce", "chia seeds") else 5
        g = round(g / step) * step
        if g > 0:
            out[name] = g
    return {"id": meal["id"], "name": meal["name"], "method": meal["method"], "ingredients": out,
            "sides": sorted(n for n in sides if sides[n] >= 1), "nutrients": nutrients(out), "target": target}


def unit_hint(name: str, g: float) -> str:
    unit, gpu = F.FOODS[name][8], F.FOODS[name][9]
    n = g / gpu
    if unit in ("large egg", "date", "tortilla", "slice", "medium", "scoop", "can (drained)"):
        q = round(n * 2) / 2
        if q == 0:
            return ""
        q_txt = f"{q:g}"
        return f"~{q_txt} {unit}{'s' if q > 1 and unit not in ('medium',) else ''}"
    if unit == "block (14 oz)":
        return f"~{n:.2f} block"
    q = round(n * 4) / 4
    if q == 0:
        return ""
    frac = {0.25: "¼", 0.5: "½", 0.75: "¾"}
    whole, part = int(q), q - int(q)
    txt = (str(whole) if whole else "") + frac.get(part, "")
    return f"~{txt or '0'} {unit}"


def pick_meal(slot: str, need_pre: bool, need_portable: bool, day_index: int, used: set) -> dict:
    opts = [m for m in F.MEALS if m["slot"] == slot]
    if need_pre:
        opts = [m for m in opts if m["pre_ok"]] or opts
    if need_portable:
        opts = [m for m in opts if m["portable"]] or opts
    n = len(opts)
    for j in range(n):
        m = opts[(day_index + j) % n]
        if m["id"] not in used:
            return m
    return opts[day_index % n]


def plan_meals(day: dict, energy: dict, day_index: int, prev_dinner: str | None, cfg: dict | None = None) -> dict:
    slots = meal_slots(day, cfg)
    on_bike = sum(s.get("on_bike_carb_g", 0) for s in day["sessions"])
    carbs_left = max(0.0, energy["carb_g"] - on_bike)
    cw = {"B": 1.0, "L": 1.0, "S": 0.6, "D": 1.0}
    pw = {"B": 0.26, "L": 0.26, "S": 0.18, "D": 0.30}
    fw = {"B": 1.0, "L": 1.0, "S": 0.5, "D": 1.2}
    for sl in slots:
        k = sl["slot"]
        if "pre-ride" in sl["role"]:
            cw[k] *= 1.6 if k == "S" else 1.4
            fw[k] *= 0.5
        if "post-ride" in sl["role"]:
            cw[k] *= 1.4
        if k == "D" and energy["fuel_tomorrow_g"] > 0:
            cw[k] += energy["fuel_tomorrow_g"] / max(1.0, carbs_left) * sum(cw.values())
    sc, sp, sf = sum(cw.values()), sum(pw.values()), sum(fw.values())
    out_slots = []
    used = {prev_dinner} if prev_dinner else set()
    for sl in slots:
        k = sl["slot"]
        target = {"p": energy["protein_g"] * pw[k] / sp, "c": carbs_left * cw[k] / sc, "f": energy["fat_g"] * fw[k] / sf}
        if "40 g+" in sl["role"]:
            target["p"] = max(target["p"], 40)
        entry = {"slot": k, "name": SLOT_NAMES[k], "time": hhmm(sl["time"]), "role": sl["role"], "mode": sl["mode"],
                 "target": target}
        if sl["mode"] in ("home", "packed"):
            meal = pick_meal(k, "pre-ride" in sl["role"], sl["mode"] == "packed", day_index, used)
            used.add(meal["id"])
            entry["meal"] = build_meal(meal, target)
        out_slots.append(entry)

    flags = enforce_limits(out_slots, energy, used)
    ride_fuel = None
    if on_bike > 0:
        mix_g = on_bike / 0.95
        ride_fuel = {"carb_g": on_bike, "ingredients": {"sports drink mix": round(mix_g / 5) * 5},
                     "gph": max(s.get("on_bike_gph", 0) for s in day["sessions"])}
    return {"slots": out_slots, "ride_fuel": ride_fuel, "flags": flags}


def _substitute(meal: dict, subs: dict) -> dict | None:
    """Copy of a meal with ingredients swapped per subs; None if nothing in it matches."""
    hit = False
    fixed = {}
    for n, g in meal["fixed"].items():
        if n in subs:
            hit = True
            fixed[subs[n]] = fixed.get(subs[n], 0) + g
        else:
            fixed[n] = fixed.get(n, 0) + g
    levers = {}
    for k, n in meal["levers"].items():
        if n in subs:
            hit = True
            levers[k] = subs[n]
        else:
            levers[k] = n
    if not hit:
        return None
    return {**meal, "fixed": fixed, "levers": levers, "id": meal["id"] + "~sub"}


LIMIT_CHECKS = [  # (key, nutrient, label, limit(energy)->float | None; None = no limit)
    ("sodium", "na", "food sodium", lambda e: e.get("sodium_max_mg", SODIUM_CEIL)),
    ("chol", "chol", "cholesterol", lambda e: e.get("chol_max_mg")),
    ("satfat", "sat", "saturated fat", lambda e: e.get("satfat_max_g", SATFAT_PCT * e["kcal"] / 9)),
    ("fiber", "fiber", "fiber", lambda e: F.FIBER_SOFT_MAX),
]


def enforce_limits(out_slots: list[dict], energy: dict, used: set, max_iter: int = 8) -> list[str]:
    """Keep the planned (home) meals inside the day's limits: food sodium, cholesterol (only when a limit is
    set), sat fat, and a fiber ceiling. First try ingredient substitutions (cholesterol), then swap the meal contributing most
    for the option that contributes least. Returns flags for anything still over."""
    def total(nut):
        return sum(e["meal"]["nutrients"][nut] for e in out_slots if "meal" in e)

    for _ in range(max_iter):
        over = [(key, nut, label, lim(energy)) for key, nut, label, lim in LIMIT_CHECKS
                if lim(energy) is not None and total(nut) > lim(energy) + 0.5]
        if not over:
            break
        key, nut, label, limit = over[0]
        meals = sorted((e for e in out_slots if "meal" in e), key=lambda e: e["meal"]["nutrients"][nut], reverse=True)
        progressed = False
        for worst in meals:
            base_meal = next((m for m in F.MEALS if m["id"] == worst["meal"]["id"].split("~")[0]), None)
            candidates = []
            if key == "chol" and base_meal is not None:
                sub = _substitute(base_meal, F.CHOL_SUBS)
                if sub is not None:
                    candidates.append(sub)
            need_portable = worst["mode"] == "packed"
            candidates += [m for m in F.MEALS if m["slot"] == worst["slot"] and m["id"] != worst["meal"]["id"]
                           and m["id"] not in used and (m["portable"] or not need_portable)
                           and (m["pre_ok"] or "pre-ride" not in worst["role"])]
            built = [build_meal(m, worst["target"]) for m in candidates]
            if not built:
                continue
            best = min(built, key=lambda b: b["nutrients"][nut])
            if best["nutrients"][nut] + 0.5 >= worst["meal"]["nutrients"][nut]:
                continue
            best["adjusted"] = f"swapped to keep {label} under {limit:.0f}"
            used.add(best["id"].split("~")[0])
            worst["meal"] = best
            progressed = True
            break
        if not progressed:
            break
    flags = []
    for key, nut, label, lim in LIMIT_CHECKS:
        if lim(energy) is not None and total(nut) > lim(energy) + 0.5:
            flags.append(f"planned meals still over the {label} limit ({total(nut):.0f} vs {lim(energy):.0f})")
    return flags


def day_totals(meals: dict) -> dict:
    tot = dict(kcal=0.0, p=0.0, c=0.0, f=0.0, fiber=0.0, sat=0.0, na=0.0, k=0.0, chol=0.0)
    planned_slots = 0
    for sl in meals["slots"]:
        if "meal" in sl:
            planned_slots += 1
            for k, v in sl["meal"]["nutrients"].items():
                tot[k] += v
        else:  # eating out / trip: count the target so totals stay comparable
            t = sl["target"]
            tot["p"] += t["p"]
            tot["c"] += t["c"]
            tot["f"] += t["f"]
            tot["kcal"] += 4 * t["p"] + 4 * t["c"] + 9 * t["f"]
    if meals["ride_fuel"]:
        n = nutrients(meals["ride_fuel"]["ingredients"])
        for k in ("kcal", "c"):
            tot[k] += n[k]
        tot["na_ride"] = n["na"]
    tot["all_planned"] = planned_slots == len(meals["slots"])
    return tot


def grocery(days_meals: list[tuple[str, dict]]) -> dict:
    agg = {}
    for _, m in days_meals:
        for sl in m["slots"]:
            if "meal" in sl:
                for name, g in sl["meal"]["ingredients"].items():
                    agg[name] = agg.get(name, 0) + g
        if m["ride_fuel"]:
            for name, g in m["ride_fuel"]["ingredients"].items():
                agg[name] = agg.get(name, 0) + g
    return agg


def grocery_by_week(days_meals: list[tuple[str, dict]]) -> dict[str, dict]:
    weeks: dict[str, list] = {}
    for ds, m in days_meals:
        weeks.setdefault(monday_of(ds), []).append((ds, m))
    return {mon: grocery(items) for mon, items in weeks.items()}


# ------------------------------------------------------------------ history
def history(snap: dict, paces: list[float]) -> dict:
    """Last 14 days from actual activities: active kcal by power meter vs Garmin, model burn vs Garmin total,
    and what the targets would have been (retro), per pace."""
    lthr, ftp = snap["athlete"]["lthr_bpm"], snap["athlete"]["ftp_w"]
    rmr = baseline_kcal(snap)["rmr"]
    acts = [a for a in snap["activities_prev_14d"] if a["type"] not in NON_WORKOUT_TYPES]
    rows = []
    for a in acts:
        s = actual_session(a, ftp, rmr)
        rows.append({**a, "session": s, "pct_lthr": a["avg_hr"] / lthr if a.get("avg_hr") else None})
    stats = snap["daily_stats_prev_14d"]
    dates = [x["date"] for x in stats]
    retro_days = actual_days(acts, dates, snap)
    retro_snap = {**snap, "plan_start": dates[0], "week_to_date": []}
    retro = {p: plan_energy(retro_days, retro_snap, p) for p in paces}
    garmin_total = {x["date"]: x["total"] for x in stats}
    workout_dates = {a["date"] for a in acts if a["duration_s"] >= 900}
    quiet = [x["total"] for x in stats if x["date"] not in workout_dates]
    with_power = [r for r in rows if r["session"]["energy_source"] == "power meter"]
    return {"rides": rows, "stats": stats, "garmin_total": garmin_total, "retro": retro, "retro_days": retro_days,
            "mean_total": sum(x["total"] for x in stats) / len(stats),
            "model_mean": sum(r["tdee"] for r in retro[paces[0]]) / len(stats),
            "quiet_mean": sum(quiet) / len(quiet) if quiet else None, "quiet_n": len(quiet),
            "no_power": [r for r in rows if is_cycling(r["type"]) and not r.get("avg_power_w")],
            "power_vs_garmin": (sum(r["session"]["active_kcal"] for r in with_power),
                                sum(r["session"]["garmin_active_kcal"] for r in with_power))}


def run(snap: dict, paces: list, lift_plan: list[dict] | None = None, meal_paces: list | None = None) -> dict:
    """paces: numbers (lb/week) and/or 'plan'. lift_plan: strength sessions from strength.plan_lifts
    ([{date, start, end, minutes, ...}]); when None the engine places lifts itself (prototype behavior)."""
    cfg = config(snap)
    kg = snap.get("weight_kg") or snap["dexa"]["total_mass_kg"]
    days = build_days(snap)
    schedule(days, kg, cfg, place_lifts=lift_plan is None)
    if lift_plan:
        by_date = {}
        for lp in lift_plan:
            by_date.setdefault(lp["date"], []).append(lp)
        for day in days:
            for lp in by_date.get(day["date"], []):
                sess = strength_session(kg, lp.get("minutes", 45))
                sess.update({k: v for k, v in lp.items() if k not in ("date",)})
                day["sessions"].append(sess)
    plans = {}
    for pace in paces:
        energy = plan_energy(days, snap, pace)
        for e, day in zip(energy, days):
            e["source"] = day["source"]
        if meal_paces is not None and pace not in meal_paces:
            plans[pace] = {"energy": energy}
            continue
        meals, prev = [], None
        for i, (day, e) in enumerate(zip(days, energy)):
            m = plan_meals(day, e, i, prev, cfg)
            dn = next((s for s in m["slots"] if s["slot"] == "D" and "meal" in s), None)
            prev = dn["meal"]["id"].split("~")[0] if dn else None
            meals.append((day["date"], m))
        plans[pace] = {"energy": energy, "meals": meals,
                       "totals": [day_totals(m) for _, m in meals],
                       "grocery_by_week": grocery_by_week(meals),
                       "grocery_w1": grocery(meals[:7]), "grocery_w2": grocery(meals[7:14])}
    hist = history(snap, [p for p in paces]) if snap.get("daily_stats_prev_14d") else None
    return {"engine_version": ENGINE_VERSION, "plan_start": snap["plan_start"],
            "plan_end": days[-1]["date"] if days else snap["plan_start"],
            "days": days, "plans": plans, "baseline": baseline_kcal(snap),
            "history": hist, "targets": body_targets(snap["dexa"]), "config": cfg}
