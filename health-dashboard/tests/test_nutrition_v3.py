"""Engine v3: weekly deficit blocks, pace schedule, cardio limits, meal timing, projection, generated rides."""
import time
from datetime import date, timedelta

import pytest

from nutrition import engine as E
from nutrition import foods as F
from nutrition import projection, rides

KG = 80.0


def _snap(start="2026-10-14", days=14, **extra):
    s = {
        "plan_start": start, "plan_days": days,
        "athlete": {"sex": "male", "birth_date": "1980-01-01", "height_cm": 175.0, "ftp_w": 250, "lthr_bpm": 170},
        "dexa": {"total_mass_kg": KG, "fat_mass_kg": 20.0, "lean_mass_kg": 57.0, "bone_mass_kg": 3.0},
        "coach_plan": {"window_end": (date.fromisoformat(start) + timedelta(days=6)).isoformat()},
        "coach_workouts": [], "activities_prev_14d": [], "calendar": [], "projected_workouts": [],
    }
    s.update(extra)
    return s


def _week_sums(rows):
    out = {}
    for r in rows:
        out.setdefault(E.monday_of(r["date"]), 0.0)
        out[E.monday_of(r["date"])] += r["deficit"]
    return out


# ---------------- weekly blocks + pace schedule ----------------

def test_deficit_allocated_per_week_not_across_weeks():
    s = _snap(start="2026-10-12", days=14)  # Monday start: two full weeks, all rest days
    days = E.build_days(s)
    E.schedule(days, KG, place_lifts=False)
    rows = E.plan_energy(days, s, 0.5)
    for mon, tot in _week_sums(rows).items():
        assert tot == pytest.approx(0.5 * 3500, rel=0.02), mon


def test_midweek_start_counts_earlier_days_as_on_plan():
    s = _snap(start="2026-10-14", days=5)  # Wed..Sun: 5/7 of the weekly deficit
    days = E.build_days(s)
    E.schedule(days, KG, place_lifts=False)
    rows = E.plan_energy(days, s, 0.5)
    assert sum(r["deficit"] for r in rows) == pytest.approx(0.5 * 3500 * 5 / 7, rel=0.02)


def test_logged_overeating_earlier_in_week_raises_remaining_deficit():
    base = _snap(start="2026-10-14", days=5)
    over = _snap(start="2026-10-14", days=5, week_to_date=[
        {"date": "2026-10-12", "tdee": 2600, "intake_kcal": 2900},   # ate 300 over
        {"date": "2026-10-13", "tdee": 2600, "intake_kcal": 2350}])  # on plan (-250)
    def total(s):
        days = E.build_days(s)
        E.schedule(days, KG, place_lifts=False)
        return sum(r["deficit"] for r in E.plan_energy(days, s, 0.5))
    # planned prior = 2*250 = 500; realized = -300 + 250 = -50 -> remaining rises by 550 (cap permitting)
    assert total(over) == pytest.approx(total(base) + 550, rel=0.02)


def test_incomplete_log_counts_as_on_plan():
    base = _snap(start="2026-10-14", days=5)
    partial = _snap(start="2026-10-14", days=5, week_to_date=[{"date": "2026-10-13", "tdee": 2600, "intake_kcal": 600}])
    def total(s):
        days = E.build_days(s)
        E.schedule(days, KG, place_lifts=False)
        return sum(r["deficit"] for r in E.plan_energy(days, s, 0.5))
    assert total(partial) == pytest.approx(total(base), rel=0.001)


def test_pace_schedule_switches_on_date():
    s = _snap(start="2026-10-12", days=14, settings={"pace_schedule": [["2026-10-12", 0.5], ["2026-10-19", 1.0]]})
    days = E.build_days(s)
    E.schedule(days, KG, place_lifts=False)
    rows = E.plan_energy(days, s, "plan")
    sums = _week_sums(rows)
    assert sums["2026-10-12"] == pytest.approx(1750, rel=0.02)
    assert sums["2026-10-19"] > 1750 * 1.5   # 1 lb/wk, capped by the 25%-of-burn rule on rest days


# ---------------- cardio limits ----------------

def test_enforce_limits_brings_cholesterol_and_sodium_under():
    egg_heavy = next(m for m in F.MEALS if m["id"] == "scramble")
    shrimp = next(m for m in F.MEALS if m["id"] == "shrimp_quinoa")
    energy = {"kcal": 2600, "sodium_max_mg": 2300, "chol_max_mg": 300, "satfat_max_g": 17}
    slots = [
        {"slot": "B", "role": "", "mode": "home", "target": {"p": 45, "c": 80, "f": 20},
         "meal": E.build_meal({**egg_heavy, "fixed": {**egg_heavy["fixed"], "whole egg": 150}}, {"p": 45, "c": 80, "f": 20})},
        {"slot": "D", "role": "", "mode": "home", "target": {"p": 55, "c": 90, "f": 25},
         "meal": E.build_meal(shrimp, {"p": 55, "c": 90, "f": 25})},
    ]
    assert sum(sl["meal"]["nutrients"]["chol"] for sl in slots) > 300
    flags = E.enforce_limits(slots, energy, set())
    assert sum(sl["meal"]["nutrients"]["chol"] for sl in slots) <= 300.5
    assert sum(sl["meal"]["nutrients"]["na"] for sl in slots) <= 2300.5
    assert not [f for f in flags if "cholesterol" in f]


def test_full_plan_respects_limits():
    s = _snap(days=14)
    res = E.run(s, [0.75])
    for (ds, m), tot in zip(res["plans"][0.75]["meals"], res["plans"][0.75]["totals"]):
        assert tot["na"] <= 2300.5, ds
    energy = res["plans"][0.75]["energy"]
    assert all(e["chol_max_mg"] is None for e in energy)                       # display-only by default
    assert not [f for m in res["plans"][0.75]["meals"] for f in m[1].get("flags", []) if "cholesterol" in f]


def test_cholesterol_limit_from_settings_is_enforced():
    s = _snap(days=14, settings={"limits": {"cholesterol_mg": 300}})
    res = E.run(s, [0.75])
    assert all(e["chol_max_mg"] == 300 for e in res["plans"][0.75]["energy"])
    for (ds, m), tot in zip(res["plans"][0.75]["meals"], res["plans"][0.75]["totals"]):
        assert tot["chol"] <= 300.5, ds


def test_bar_row_without_target_shows_value_only():
    from nutrition import fragments as H
    html = H.bar_row("Cholesterol", 412, None, "mg", ceiling=True)
    assert "412 mg" in html and " / " not in html and "over" not in html
    assert "— mg" in H.bar_row("Cholesterol", None, None, "mg")


# ---------------- meal timing ----------------

def test_breakfast_not_before_earliest_food_and_ride_after():
    s = _snap(days=3, constraints={"earliest_food": "08:30"},
              coach_workouts=[{"date": "2026-10-14", "name": "Base", "type": "BASE", "duration_s": 3600,
                               "steps": [[3600, 140, 170]]}])
    res = E.run(s, [0.5])
    meals = res["plans"][0.5]["meals"]
    for _, m in meals:
        assert min(E.tmin(sl["time"]) for sl in m["slots"]) >= E.tmin("08:30")
    ride = next(x for x in res["days"][0]["sessions"] if x["kind"] == "ride")
    assert ride["start"] >= E.tmin("10:00")


# ---------------- projection + generated rides ----------------

def test_generated_week_shape():
    wk = rides.generate_week(date(2026, 11, 23), 0, 250, rides.WEEKDAYS[:6], ["SATURDAY", "TUESDAY"])
    roles = {w["date"]: w.get("type", "REST") for w in wk}
    assert roles["2026-11-28"] == "LONG_WORKOUT"          # Saturday long
    assert roles["2026-11-29"] == "REST"                   # Sunday not available
    types = [w.get("type") for w in wk]
    assert "VO2MAX" in types and "LACTATE_THRESHOLD" in types
    thr = next(w for w in wk if w.get("type") == "LACTATE_THRESHOLD")
    work = thr["steps"][1]["steps"][0]
    assert work[1] == round(250 * 0.95) and work[2] == 250


def test_every_fourth_week_is_recovery():
    wk = rides.generate_week(date(2026, 12, 14), 3, 250, rides.WEEKDAYS[:6], ["SATURDAY"])
    types = {w.get("type") for w in wk}
    assert "VO2MAX" not in types and "LACTATE_THRESHOLD" not in types
    assert all(w["recovery_week"] for w in wk)


def test_projection_uses_template_then_generates_after_plan_end():
    versions = [{"date": "2026-10-15", "type": "LACTATE_THRESHOLD", "name": "Threshold", "duration_s": 3000,
                 "steps": [[3000, 200, 230]]},
                {"date": "2026-10-16", "rest": True, "type": "REST"}]
    out = projection.project(date(2026, 10, 14), date(2026, 11, 30), coach_window_end=date(2026, 10, 20),
                             plan_end=date(2026, 11, 20), coach_active=True, versions=versions,
                             athlete={"ftp_w": 250, "available_days": rides.WEEKDAYS[:6], "long_days": ["SATURDAY"]})
    by = {w["date"]: w for w in out}
    assert "2026-10-20" not in by                          # inside Coach's window
    assert by["2026-10-22"]["type"] == "LACTATE_THRESHOLD" and by["2026-10-22"]["source"] == "projected"
    assert by["2026-10-23"].get("rest")
    assert by["2026-11-21"]["source"] == "generated"


def test_no_coach_plan_generates_everything():
    out = projection.project(date(2026, 11, 21), date(2026, 11, 27), coach_window_end=None, plan_end=None,
                             coach_active=False, versions=[], athlete={"ftp_w": 250})
    assert len(out) == 7 and all(w["source"] == "generated" for w in out)


def test_engine_uses_projected_workouts():
    s = _snap(days=10, projected_workouts=[{"date": "2026-10-22", "name": "Threshold", "type": "LACTATE_THRESHOLD",
                                            "duration_s": 3000, "steps": [[3000, 200, 230]], "source": "projected",
                                            "note": "Projected."}])
    days = E.build_days(s)
    d22 = next(x for x in days if x["date"] == "2026-10-22")
    assert d22["source"] == "projected" and d22["sessions"][0]["projected"]
    assert any(n["kind"] == "projected" for n in d22["notes"])


# ---------------- groceries + scale ----------------

def test_grocery_by_week_keys_are_mondays_and_sum_to_total():
    s = _snap(days=14)
    plan = E.run(s, [0.5])["plans"][0.5]
    weeks = plan["grocery_by_week"]
    assert all(date.fromisoformat(k).weekday() == 0 for k in weeks)
    total = E.grocery(plan["meals"])
    merged = {}
    for w in weeks.values():
        for n, g in w.items():
            merged[n] = merged.get(n, 0) + g
    assert merged == pytest.approx(total)


def test_78_days_six_paces_is_fast():
    s = _snap(days=78, settings={"pace_schedule": [["2026-10-14", 0.75], ["2026-11-21", 1.0]]})
    t0 = time.time()
    E.run(s, [0, 0.5, 0.75, 1.0, 1.5, "plan"])
    assert time.time() - t0 < 20


def test_ride_by_date_swaps_a_coach_ride_for_rest_and_adds_a_fixed_ride():
    coach = [{"date": "2026-10-17", "name": "Base", "type": "BASE", "duration_s": 7200, "avg_power_w": 140}]
    s = _snap(start="2026-10-14", days=5, coach_workouts=coach,
              settings={"ride_by_date": {"2026-10-17": "rest",
                                         "2026-10-18": {"start": "09:00", "minutes": 120, "avg_power_w": 145,
                                                        "name": "Group ride"}}})
    days = {d["date"]: d for d in E.build_days(s)}
    E.schedule(list(days.values()), KG, place_lifts=False)
    sat, sun = days["2026-10-17"], days["2026-10-18"]
    assert sat["sessions"] == [] and sat["source"] == "you"
    ride = sun["sessions"][0]
    assert ride["name"] == "Group ride" and ride["minutes"] == 120 and ride["avg_w"] == pytest.approx(145)
    assert (ride["start"], ride["end"], ride["reason"]) == (540, 660, "time set by you")
