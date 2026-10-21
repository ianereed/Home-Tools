"""Every target, limit and physiological constant the dashboard compares against.

One table so a tab, an Overview tile and a Goals lever can never disagree on
what "good" means. Public reference values only (guidelines, trials, device
settings); physician-set personal goals stay in the gitignored
`cardiology/clinical_data.py` and are read through the helpers at the bottom,
which take the CARDIO_GOALS dict as an argument so this module never imports
PHI itself.
"""
from __future__ import annotations

KG_TO_LB = 2.2046226218

# --- Heart rate ---------------------------------------------------------------
# Max HR as set on the Garmin (2026-10-08). Zones, TRIMP and the HR-zone minutes
# all key off this one number; MAX(max_hr) across activities is a sensor spike.
HRMAX = 192
# Lower bound of each zone as a fraction of HRMAX (Garmin's %HRmax zones).
ZONE_PCT = {"Z1": 0.50, "Z2": 0.60, "Z3": 0.70, "Z4": 0.80, "Z5": 0.90}
MODERATE_ZONES = ("Z3", "Z4", "Z5")     # "moderate-vigorous" minutes
VIGOROUS_ZONES = ("Z4", "Z5")

# --- Activity (AHA adult guideline, minutes/week) -------------------------------
AHA_MODERATE_MIN_WK = 150
AHA_VIGOROUS_MIN_WK = 75
AHA_GOAL_MIN_WK = 300

# --- Sleep ----------------------------------------------------------------------
SLEEP_TARGET_H = 8.0

# --- Blood pressure (AHA) -------------------------------------------------------
BP_NORMAL = (120, 80)          # default goal line when no physician goal is set
BP_STAGE2 = (140, 90)

# --- Alcohol (US Dietary Guidelines, men: <=2/day) -------------------------------
ALCOHOL_DRINKS_WK = 14

# --- Nutrition (DASH + Dietary Guidelines) ---------------------------------------
SODIUM_MG_CEILING = 2300
SODIUM_MG_IDEAL = 1500
POTASSIUM_MG = 4700
SATFAT_PCT_KCAL = 6            # DASH: saturated fat <= 6 % of calories
TOTAL_FAT_PCT_KCAL = 27
FIBER_G_PER_1000_KCAL = 14     # Dietary Guidelines adequacy rate
FIBER_MIN_G = 25               # floor on low-calorie days


def fiber_target_g(kcal: float | None) -> float:
    """Daily fiber target: 14 g per 1,000 kcal eaten, never below 25 g."""
    return max(FIBER_MIN_G, FIBER_G_PER_1000_KCAL * (kcal or 0) / 1000)


def satfat_max_g(kcal: float | None) -> float:
    """Saturated-fat ceiling in grams for a day's calories (6 % of kcal)."""
    return SATFAT_PCT_KCAL / 100 * (kcal or 0) / 9


# --- Physician goals (dict from clinical_data.CARDIO_GOALS, may be None) ----------

def bp_goal(goals: dict | None) -> tuple[int, int]:
    """(systolic, diastolic) goal; AHA normal when no physician goal is set."""
    bp = (goals or {}).get("bp") or {}
    return (bp.get("systolic") or BP_NORMAL[0], bp.get("diastolic") or BP_NORMAL[1])


def ldl_goal(goals: dict | None) -> int | None:
    """The LDL number the Goals page tracks: the stretch goal, else the target."""
    ldl = (goals or {}).get("ldl") or {}
    return ldl.get("stretch") or ldl.get("target")


def weight_band_lb(goals: dict | None) -> tuple[float, float] | None:
    """(low, high) goal band in lb from the baseline and lose-range, or None."""
    w = (goals or {}).get("weight") or {}
    if not w.get("baseline_kg") or w.get("lose_lb_min") is None or w.get("lose_lb_max") is None:
        return None
    base = w["baseline_kg"] * KG_TO_LB
    return base - w["lose_lb_max"], base - w["lose_lb_min"]


def weight_baseline_lb(goals: dict | None) -> float | None:
    w = (goals or {}).get("weight") or {}
    return w["baseline_kg"] * KG_TO_LB if w.get("baseline_kg") else None
