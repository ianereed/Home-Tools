"""Fuel planner settings: public defaults + gitignored overrides in data/fuel_settings.json.

Anything personal (calendar id, keyword rules naming people/places, travel buffers, trip
assumptions) belongs in the JSON file, not here. Medical constraints come from the gitignored
cardiology/clinical_data.py (FUEL_CONSTRAINTS), read with getattr like the other PHI views.
"""
from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "data" / "fuel_settings.json"
CLINICAL_PATH = ROOT / "cardiology" / "clinical_data.py"

DEFAULTS = {
    "horizon_end": "2026-12-31",
    "calendar_ids": [],                      # e.g. ["you@gmail.com"]; empty -> no calendar
    "treat_needs_action_as_busy": True,
    "preferred_ride": "10:00",
    "earliest_ride": "09:00",
    "pace_schedule": [["2026-10-14", 0.75], ["2026-11-21", 1.0]],
    "paces": [0, 0.5, 0.75, 1.0, 1.5],       # comparison paces shown alongside the plan
    "limits": {"sodium_mg": 2300, "cholesterol_mg": None, "satfat_pct": 0.06},  # cholesterol None = display-only
    "travel_rules": [],                      # [{"match": "<location substring>", "pre_min": 60}]
    "calendar_rules": {
        "trip_keywords": ["bike packing", "bikepacking", "camping"],
        "workout_keywords": ["group ride", "ride", "race"],
        "meal_out_keywords": ["dinner", "party", "night"],
        "meal_out_window": ["17:00", "20:30"],
        "meal_out_min_minutes": 90,
        "packed_lunch_span": ["11:30", "13:30"],
    },
    "trip_sessions": [                       # calendar trip -> planned session assumption
        {"match": "bike packing", "type": "BIKEPACKING", "hours_per_day": 5, "avg_power_w": 120},
        {"match": "bikepacking", "type": "BIKEPACKING", "hours_per_day": 5, "avg_power_w": 120},
    ],
    "mfp_push_goal": True,                   # today's plan target -> MyFitnessPal goal after each refresh
    "garmin_write_mode": "off",              # off | dry_run | live  (strength/ride sync, Phase 6+)
    "strength_sync_days": 10,
    "upper_body_cleared": False,
    "lift_equipment": "gym",                 # gym | home (bodyweight)
    "lift_equipment_by_date": {},            # {"YYYY-MM-DD": "home"} one-off overrides
    "lift_weekdays_only": False,             # True: never plan a lift on Sat/Sun
    "ride_by_date": {},                      # {"YYYY-MM-DD": "rest" | {"start": "09:00", "minutes": 120, "avg_power_w": 145, "name": ...}}
                                             # one-off ride swaps (fuel plan + Google Calendar)
    "lift_start_by_date": {},                # {"YYYY-MM-DD": "18:00"} one-off lift times (fuel plan + Google Calendar)
    "strength_block_start": None,            # "YYYY-MM-DD" first lift of the block: deload weeks count from it
    "strength_weights": {},                  # {"GOBLET_SQUAT": {"lb": 30, "note": "..."}} working weights, from Ian's notes
    "athlete_fallback": {},                  # used only if Garmin profile fields are missing
    "gcal_sync": {                           # Garmin calendar -> Google Calendar (python -m nutrition.gcal_workouts)
        "mode": "live",                      # off | dry_run | live
        "calendar_id": "primary",            # resolved by the mini's Google token (= personal gmail)
        "window_days": 45,
        "start_times": {"strength_training": "17:00", "default": "10:00"},   # cycling falls back to preferred_ride
        "default_minutes": {"cycling": 60, "strength_training": 45, "default": 60},
        "include_rest_days": True,
    },
    "sweat_sodium": {                        # ride sweat sodium -> negative-sodium MFP entry (nutrition/sweat.py)
        "mode": "dry_run",                   # off | dry_run | live
        "mg_per_l": None,                    # sweat sodium concentration; Ian's number, nothing is logged while unset
        "fallback_sweat_ml_per_h": 600,      # only when Garmin has no waterEstimated for the ride
        "activity_types": ["cycling", "road_biking", "gravel_cycling", "mountain_biking", "indoor_cycling",
                           "virtual_ride", "e_bike_fitness", "downhill_biking"],
        "min_minutes": 15,
        "min_mg": 50,                        # smaller losses aren't worth a diary line
        "lookback_days": 3,
        "since": None,                       # "YYYY-MM-DD": never touch rides before this date
        "override_mg": {},                   # {"<garmin activity id>": mg} hand corrections
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(path: Path | None = None) -> dict:
    path = path or SETTINGS_PATH
    if path.exists():
        return _merge(DEFAULTS, json.loads(path.read_text()))
    return deepcopy(DEFAULTS)


def load_constraints(path: Path | None = None) -> dict:
    """Medical constraints from the PHI module, e.g. {'first_food_min_after_wake': 30,
    'potassium': 'display_only'}. Empty dict when the module or attribute is absent."""
    path = path or CLINICAL_PATH
    if not path.exists():
        return {}
    try:
        spec = importlib.util.spec_from_file_location("clinical_data_fuel", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        return {}
    return dict(getattr(mod, "FUEL_CONSTRAINTS", {}) or {})
