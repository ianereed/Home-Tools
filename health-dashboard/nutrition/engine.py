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

from . import foods as F

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

EARLIEST_RIDE = "09:00"
PREFERRED_RIDE = "10:00"
LATEST_END = "19:30"
SHOWER_MIN = 30
EVENT_BUFFER_MIN = 30
TRAVEL_RULES: list[dict] = []  # [{"match": "<location substring>", "pre_min": 60}] -- supplied from gitignored settings

DEFAULT_SLOT_TIMES = {"B": "07:30", "L": "12:30", "S": "15:30", "D": "18:45"}
SLOT_NAMES = {"B": "Breakfast", "L": "Lunch", "S": "Snack", "D": "Dinner"}
DINNER_FLOOR = 17 * 60 + 30

CHO_FRACTION = {
    "ANAEROBIC_SPEED": 0.65, "ANAEROBIC_CAPACITY": 0.75, "VO2MAX": 0.8, "LACTATE_THRESHOLD": 0.75,
    "TEMPO": 0.65, "BASE": 0.5, "AEROBIC_LOW_SHORTAGE_BASE": 0.5, "LONG_WORKOUT": 0.5,
    "RECOVERY": 0.45, "BIKEPACKING": 0.5, "STRENGTH": 0.6,
}
KEY_TYPES = {"ANAEROBIC_SPEED", "ANAEROBIC_CAPACITY", "VO2MAX", "LACTATE_THRESHOLD", "TEMPO"}
DEFICIT_WEIGHT = {"rest": 1.0, "easy": 0.8, "moderate": 0.6, "key": 0.35, "long": 0.0}


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
    """Body-fat endpoints holding (or adding) lean tissue. % = fat / (fat + lean) tissue, DEXA-style."""
    lean, bone, fat = dexa["lean_mass_kg"], dexa["bone_mass_kg"], dexa["fat_mass_kg"]
    out = []
    for label, pct, lean_gain in [("25%", 0.25, 0.0), ("22%", 0.22, 0.0), ("20%", 0.20, 0.0),
                                  ("20% + 2 kg lean", 0.20, 2.0)]:
        l2 = lean + lean_gain
        f2 = l2 * pct / (1 - pct)
        total = l2 + f2 + bone
        out.append({"label": label, "pct": pct, "lean_kg": l2, "fat_kg": f2, "total_kg": total,
                    "total_lb": total * 2.20462, "fat_loss_kg": fat - f2, "lean_gain_kg": lean_gain,
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
        "te": w.get("te"),
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
    """One dict per plan day with sessions (Coach known, Coach projected, or calendar extras)."""
    start = d(snap["plan_start"])
    ftp = snap["athlete"]["ftp_w"]
    coach = {w["date"]: w for w in snap["coach_workouts"]}
    extras = {w["date"]: w for w in snap.get("planned_extras", [])}
    window_end = d(snap["coach_plan"]["window_end"])
    days = []
    for i in range(snap["plan_days"]):
        day = start + timedelta(days=i)
        ds = day.isoformat()
        sessions, source, note = [], "coach", None
        if ds in extras:
            sessions.append(ride_session(extras[ds], ftp))
            source = "calendar"
            proj_src = (day - timedelta(days=7)).isoformat()
            if proj_src in coach and not coach[proj_src].get("rest"):
                note = f"Replaces the projected Coach '{coach[proj_src]['name']}' for this weekday."
            elif proj_src in coach:
                note = "Replaces a projected Coach rest day."
        elif ds in coach:
            w = coach[ds]
            if not w.get("rest"):
                sessions.append(ride_session(w, ftp))
        elif day > window_end:
            # Coach (adaptive) only publishes ~7 days ahead: mirror the same weekday from week 1.
            src = (day - timedelta(days=7)).isoformat()
            source = "projected"
            if src in coach and not coach[src].get("rest"):
                sessions.append(ride_session(coach[src], ftp, projected=True))
        actual = actual_days(snap.get("activities_plan_window", []), [ds], snap)[0]["sessions"]
        if actual:
            done_kinds = {s["kind"] for s in actual}
            sessions = [s for s in sessions if s["kind"] not in done_kinds] + actual
            source = "actual"
        days.append({"date": ds, "dow": day.strftime("%a"), "sessions": sessions, "source": source,
                     "note": note, "events": [e for e in snap["calendar"] if e["date"] == ds]})
    return days


def day_category(day: dict) -> str:
    order = ["rest", "easy", "moderate", "key", "long"]
    cats = [s["category"] for s in day["sessions"] if s["kind"] == "ride"]
    if not cats:
        return "rest" if not day["sessions"] else "easy"
    return max(cats, key=order.index)


# ------------------------------------------------------------------ scheduling
def busy_intervals(day: dict) -> list[tuple[int, int, str]]:
    out = []
    for e in day["events"]:
        if not e.get("busy") or e.get("all_day"):
            continue
        loc = (e.get("location") or "").lower()
        pre = next((r["pre_min"] for r in TRAVEL_RULES if r["match"].lower() in loc), EVENT_BUFFER_MIN)
        out.append((tmin(e["start"]) - pre, tmin(e["end"]) + EVENT_BUFFER_MIN, e["title"]))
    return out


def fits(start: int, end: int, busy) -> bool:
    return all(end <= b0 or start >= b1 for b0, b1, _ in busy)


def schedule_ride(day: dict, ride: dict) -> dict | None:
    busy = busy_intervals(day)
    dur = math.ceil(ride["minutes"])
    best = None
    for start in range(tmin(EARLIEST_RIDE), tmin(LATEST_END) - dur + 1, 15):
        end = start + dur
        if not fits(start, end + SHOWER_MIN, busy):
            continue
        score = abs(start - tmin(PREFERRED_RIDE))
        if best is None or score < best[0]:
            best = (score, start, end)
    if best is None:
        return None
    reason = "preferred mid-morning slot" if best[1] == tmin(PREFERRED_RIDE) else "moved to fit around calendar"
    return {"start": best[1], "end": best[2], "reason": reason}


def schedule(days: list[dict], kg: float) -> None:
    """Assign ride times, then place 2 strength sessions per 7-day block."""
    for day in days:
        for s in day["sessions"]:
            if s["kind"] != "ride" or s.get("completed"):
                continue
            if s["type"] == "BIKEPACKING":
                s["start"], s["end"], s["reason"] = tmin("08:00"), tmin("08:00") + int(s["minutes"]) + 120, "trip day (moving time + stops)"
                continue
            slot = schedule_ride(day, s)
            if slot:
                s["start"], s["end"], s["reason"] = slot["start"], slot["end"], slot["reason"]
            else:
                s["start"], s["end"], s["reason"] = None, None, "no free window -- needs a manual call"

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
            busy = busy_intervals(days[i]) + [(ride["start"], ride["end"] + SHOWER_MIN, "ride")]
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
    mif = rmr_mifflin(x["total_mass_kg"], a["height_cm"], age)
    kat = rmr_katch(x["lean_mass_kg"] + x["bone_mass_kg"])
    return {"age": age, "rmr": mif, "rmr_mifflin": mif, "rmr_katch": kat, "pal": PAL, "baseline": mif * PAL}


def session_energy(s: dict, rmr: float) -> None:
    """Fill active kcal (planned sessions) and the everyday movement the session displaces."""
    rmr_min = rmr / 1440
    if not s.get("completed"):
        s["active_kcal"] = max(0.0, s["gross_kcal"] - rmr_min * s["minutes"])
        s["energy_source"] = "plan (Coach power targets)" if s["kind"] == "ride" else "plan (MET estimate)"
    s["displaced_kcal"] = (PAL - 1) * rmr_min * s["minutes"]


def plan_energy(days: list[dict], snap: dict, pace_lb_wk: float) -> list[dict]:
    kg = snap["dexa"]["total_mass_kg"]
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

    total_def = pace_lb_wk * KCAL_PER_LB * len(days) / 7

    def alloc(k):
        return [min(r["cap"], r["w"] * k) for r in rows]

    lo, hi = 0.0, 5000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if sum(alloc(mid)) < total_def:
            lo = mid
        else:
            hi = mid
    for r, dfc in zip(rows, alloc(hi) if total_def > 0 else [0] * len(rows)):
        r["deficit_planned"] = dfc

    P = PROTEIN_G_PER_KG * kg
    for r in rows:
        kcal = r["tdee"] - r["deficit_planned"]
        bonus = FUEL_TOMORROW_G_PER_KG * kg if r["next"] in ("key", "long") and r["category"] != "long" else 0.0
        C = CARB_BASE_G_PER_KG * kg + r["session_carbs"] + bonus
        c_min = CARB_MIN_BASE_G_PER_KG * kg + 0.85 * r["session_carbs"] + bonus
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
        r.update({"kcal": kcal, "deficit": r["tdee"] - kcal, "protein_g": P, "carb_g": C, "fat_g": Fg,
                  "fuel_tomorrow_g": bonus, "fiber_target": max(FIBER_MIN, 14 * kcal / 1000),
                  "satfat_max_g": SATFAT_PCT * kcal / 9, "flags": flags, "carb_g_per_kg": C / kg})
    return rows


# ------------------------------------------------------------------ meal timing
def meal_slots(day: dict) -> list[dict]:
    slots = [{"slot": k, "time": tmin(v), "role": "", "mode": "home"} for k, v in DEFAULT_SLOT_TIMES.items()]
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
                p["time"] = start - need
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
        sl["time"] = int(round(sl["time"] / 5) * 5)
    slots.sort(key=lambda s: s["time"])
    return slots


# ------------------------------------------------------------------ meal building
def nutrients(ingr: dict) -> dict:
    tot = dict(kcal=0.0, p=0.0, c=0.0, f=0.0, fiber=0.0, sat=0.0, na=0.0, k=0.0)
    for name, g in ingr.items():
        kcal, p, c, f, fib, sat, na, k, *_ = F.FOODS[name]
        r = g / 100
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


def plan_meals(day: dict, energy: dict, day_index: int, prev_dinner: str | None) -> dict:
    slots = meal_slots(day)
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

    # Too much fiber (>~55 g) is a GI risk around rides: swap the highest-fiber meal for a lower-fiber option.
    for _ in range(3):
        fiber = sum(e["meal"]["nutrients"]["fiber"] for e in out_slots if "meal" in e)
        if fiber <= F.FIBER_SOFT_MAX:
            break
        worst = max((e for e in out_slots if "meal" in e), key=lambda e: e["meal"]["nutrients"]["fiber"])
        need_portable = worst["mode"] == "packed"
        opts = [m for m in F.MEALS if m["slot"] == worst["slot"] and m["id"] != worst["meal"]["id"] and m["id"] not in used
                and (m["portable"] or not need_portable) and (m["pre_ok"] or "pre-ride" not in worst["role"])]
        built = [build_meal(m, worst["target"]) for m in opts]
        if not built:
            break
        best = min(built, key=lambda b: b["nutrients"]["fiber"])
        if best["nutrients"]["fiber"] >= worst["meal"]["nutrients"]["fiber"]:
            break
        best["swapped_for_fiber"] = True
        worst["meal"] = best
    ride_fuel = None
    if on_bike > 0:
        mix_g = on_bike / 0.95
        ride_fuel = {"carb_g": on_bike, "ingredients": {"sports drink mix": round(mix_g / 5) * 5},
                     "gph": max(s.get("on_bike_gph", 0) for s in day["sessions"])}
    return {"slots": out_slots, "ride_fuel": ride_fuel}


def day_totals(meals: dict) -> dict:
    tot = dict(kcal=0.0, p=0.0, c=0.0, f=0.0, fiber=0.0, sat=0.0, na=0.0, k=0.0)
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
    retro = {p: plan_energy(retro_days, snap, p) for p in paces}
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


def run(snap: dict, paces: list[float]) -> dict:
    kg = snap["dexa"]["total_mass_kg"]
    days = build_days(snap)
    schedule(days, kg)
    plans = {}
    for pace in paces:
        energy = plan_energy(days, snap, pace)
        meals, prev = [], None
        for i, (day, e) in enumerate(zip(days, energy)):
            m = plan_meals(day, e, i, prev)
            dn = next((s for s in m["slots"] if s["slot"] == "D" and "meal" in s), None)
            prev = dn["meal"]["id"] if dn else None
            meals.append((day["date"], m))
        plans[pace] = {"energy": energy, "meals": meals,
                       "totals": [day_totals(m) for _, m in meals],
                       "grocery_w1": grocery(meals[:7]), "grocery_w2": grocery(meals[7:])}
    return {"days": days, "plans": plans, "baseline": baseline_kcal(snap), "history": history(snap, paces),
            "targets": body_targets(snap["dexa"])}
