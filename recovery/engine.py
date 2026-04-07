"""Recovery estimation engine using TRIMP, Banister fitness-fatigue, and physiological signals."""

import math
import sqlite3
from datetime import date, timedelta
from typing import Optional

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from collectors.db import get_connection

# Fallback max HR based on age 34 (220 - 34)
DEFAULT_MAX_HR = 186


def get_resting_hr(conn: sqlite3.Connection) -> float:
    """Get average resting HR from the last 7 days of data."""
    row = conn.execute(
        """SELECT AVG(bpm) FROM heart_rate
           WHERE context = 'resting' AND timestamp >= date('now', '-7 days')"""
    ).fetchone()
    return row[0] if row[0] else 60.0


def get_max_hr(conn: sqlite3.Connection) -> int:
    """Get max HR ever recorded from activities."""
    row = conn.execute(
        "SELECT MAX(max_hr) FROM activities WHERE max_hr IS NOT NULL"
    ).fetchone()
    return row[0] if row[0] else DEFAULT_MAX_HR


# ============================================================
# Component 1: TRIMP (Training Impulse)
# ============================================================

def calculate_trimp_from_stream(activity_id: str, resting_hr: float, max_hr: int,
                                 conn: sqlite3.Connection) -> float:
    """Calculate Banister's exponential TRIMP from an activity's HR stream.

    TRIMP = Σ(Δt × hr_reserve_fraction × 0.64 × e^(1.92 × hr_reserve_fraction))
    Where Δt is in minutes and hr_reserve_fraction = (HR - HRrest) / (HRmax - HRrest)
    """
    rows = conn.execute(
        "SELECT timestamp_offset, bpm FROM activity_streams WHERE activity_id = ? ORDER BY timestamp_offset",
        (activity_id,),
    ).fetchall()

    if len(rows) < 2:
        return 0.0

    hr_reserve_range = max_hr - resting_hr
    if hr_reserve_range <= 0:
        return 0.0

    trimp = 0.0
    for i in range(1, len(rows)):
        dt_minutes = (rows[i][0] - rows[i - 1][0]) / 60.0
        hr = rows[i][1]

        # Clamp HR to valid range
        hr = max(resting_hr, min(hr, max_hr))
        hr_fraction = (hr - resting_hr) / hr_reserve_range

        trimp += dt_minutes * hr_fraction * 0.64 * math.exp(1.92 * hr_fraction)

    return round(trimp, 1)


def calculate_trimp_from_summary(avg_hr: float, duration_min: float,
                                  resting_hr: float, max_hr: int) -> float:
    """Estimate TRIMP from activity summary (when no HR stream is available)."""
    hr_reserve_range = max_hr - resting_hr
    if hr_reserve_range <= 0 or not avg_hr:
        return 0.0

    avg_hr = max(resting_hr, min(avg_hr, max_hr))
    hr_fraction = (avg_hr - resting_hr) / hr_reserve_range

    return round(duration_min * hr_fraction * 0.64 * math.exp(1.92 * hr_fraction), 1)


def get_daily_trimp(target_date: str, conn: sqlite3.Connection) -> list[dict]:
    """Calculate TRIMP for all activities on a given date. Returns list of activity details."""
    resting_hr = get_resting_hr(conn)
    max_hr = get_max_hr(conn)

    activities = conn.execute(
        """SELECT source_id, type, duration_minutes, avg_hr, max_hr, date
           FROM activities WHERE date = ?""",
        (target_date,),
    ).fetchall()

    results = []
    for act in activities:
        source_id, act_type, duration, avg_hr_act, max_hr_act, act_date = act

        # Try stream-based TRIMP first
        stream_count = conn.execute(
            "SELECT COUNT(*) FROM activity_streams WHERE activity_id = ?",
            (str(source_id),),
        ).fetchone()[0]

        if stream_count > 0:
            trimp = calculate_trimp_from_stream(str(source_id), resting_hr, max_hr, conn)
            method = "stream"
        elif avg_hr_act:
            trimp = calculate_trimp_from_summary(avg_hr_act, duration or 0, resting_hr, max_hr)
            method = "estimate"
        else:
            trimp = 0.0
            method = "no_hr"

        results.append({
            "date": act_date,
            "type": act_type,
            "duration_min": duration,
            "avg_hr": avg_hr_act,
            "trimp": trimp,
            "method": method,
        })

    return results


def get_trimp_history(days: int, conn: sqlite3.Connection) -> dict[str, float]:
    """Get daily total TRIMP for the past N days."""
    start = (date.today() - timedelta(days=days)).isoformat()
    daily = {}

    for i in range(days + 1):
        d = (date.today() - timedelta(days=days - i)).isoformat()
        daily[d] = 0.0

    activities = conn.execute(
        """SELECT source_id, date, duration_minutes, avg_hr
           FROM activities WHERE date >= ?""",
        (start,),
    ).fetchall()

    resting_hr = get_resting_hr(conn)
    max_hr = get_max_hr(conn)

    for act in activities:
        source_id, act_date, duration, avg_hr_act = act
        stream_count = conn.execute(
            "SELECT COUNT(*) FROM activity_streams WHERE activity_id = ?",
            (str(source_id),),
        ).fetchone()[0]

        if stream_count > 0:
            trimp = calculate_trimp_from_stream(str(source_id), resting_hr, max_hr, conn)
        elif avg_hr_act:
            trimp = calculate_trimp_from_summary(avg_hr_act, duration or 0, resting_hr, max_hr)
        else:
            trimp = 0.0

        if act_date in daily:
            daily[act_date] += trimp

    return daily


def get_current_fatigue(conn: sqlite3.Connection) -> dict:
    """Calculate current remaining fatigue from recent activities using exponential decay."""
    resting_hr = get_resting_hr(conn)
    max_hr = get_max_hr(conn)
    now = date.today()

    activities = conn.execute(
        """SELECT source_id, date, type, duration_minutes, avg_hr
           FROM activities WHERE date >= date('now', '-7 days')
           ORDER BY date DESC""",
    ).fetchall()

    total_remaining = 0.0
    activity_fatigue = []

    for act in activities:
        source_id, act_date, act_type, duration, avg_hr_act = act

        stream_count = conn.execute(
            "SELECT COUNT(*) FROM activity_streams WHERE activity_id = ?",
            (str(source_id),),
        ).fetchone()[0]

        if stream_count > 0:
            trimp = calculate_trimp_from_stream(str(source_id), resting_hr, max_hr, conn)
        elif avg_hr_act:
            trimp = calculate_trimp_from_summary(avg_hr_act, duration or 0, resting_hr, max_hr)
        else:
            continue

        # Decay constant based on TRIMP magnitude
        if trimp < 50:
            tau_hours = 24
        elif trimp < 150:
            tau_hours = 36
        elif trimp < 300:
            tau_hours = 48
        else:
            tau_hours = 72

        days_ago = (now - date.fromisoformat(act_date)).days
        hours_ago = days_ago * 24
        remaining = trimp * math.exp(-hours_ago / tau_hours)

        total_remaining += remaining
        activity_fatigue.append({
            "date": act_date,
            "type": act_type,
            "trimp": round(trimp, 1),
            "tau_hours": tau_hours,
            "remaining": round(remaining, 1),
            "hours_ago": hours_ago,
        })

    # Estimate hours until fatigue drops below threshold (TRIMP < 10 = recovered)
    hours_to_recovered = 0
    if total_remaining > 10:
        # Find the dominant tau (weighted average)
        if activity_fatigue:
            weighted_tau = sum(a["trimp"] * a["tau_hours"] for a in activity_fatigue) / sum(a["trimp"] for a in activity_fatigue if a["trimp"] > 0) if any(a["trimp"] > 0 for a in activity_fatigue) else 36
            hours_to_recovered = max(0, -weighted_tau * math.log(10 / total_remaining))

    return {
        "total_remaining_trimp": round(total_remaining, 1),
        "hours_to_recovered": round(hours_to_recovered, 1),
        "activities": activity_fatigue,
    }


# ============================================================
# Component 2: Fitness-Fatigue (CTL / ATL / TSB)
# ============================================================

def calculate_ctl_atl_tsb(conn: sqlite3.Connection, days: int = 60) -> list[dict]:
    """Calculate CTL (42-day), ATL (7-day), and TSB using exponential weighted averages."""
    daily_trimp = get_trimp_history(days, conn)
    dates = sorted(daily_trimp.keys())

    ctl = 0.0
    atl = 0.0
    results = []

    for d in dates:
        trimp = daily_trimp[d]
        # Exponential moving average: new = old + (value - old) / tau
        ctl = ctl + (trimp - ctl) / 42
        atl = atl + (trimp - atl) / 7
        tsb = ctl - atl

        results.append({
            "date": d,
            "trimp": round(trimp, 1),
            "ctl": round(ctl, 2),
            "atl": round(atl, 2),
            "tsb": round(tsb, 2),
        })

    return results


def get_current_tsb(conn: sqlite3.Connection) -> dict:
    """Get the current CTL, ATL, and TSB values with interpretation."""
    history = calculate_ctl_atl_tsb(conn, days=60)
    if not history:
        return {"ctl": 0, "atl": 0, "tsb": 0, "interpretation": "No data"}

    current = history[-1]
    tsb = current["tsb"]

    if tsb > 15:
        interpretation = "Well recovered / possibly detraining"
        color = "green"
    elif tsb >= 0:
        interpretation = "Fresh — good to train"
        color = "green"
    elif tsb >= -10:
        interpretation = "Slightly fatigued — manageable"
        color = "orange"
    else:
        interpretation = "Accumulating fatigue — recovery needed"
        color = "red"

    return {
        "ctl": current["ctl"],
        "atl": current["atl"],
        "tsb": current["tsb"],
        "interpretation": interpretation,
        "color": color,
    }


# ============================================================
# Component 3: Physiological Signals
# ============================================================

def get_physiological_status(conn: sqlite3.Connection) -> dict:
    """Assess recovery from HRV, resting HR, and sleep compared to personal baselines."""

    # HRV analysis (from wellness table)
    hrv_rows = conn.execute(
        "SELECT date, hrv FROM wellness WHERE hrv IS NOT NULL ORDER BY date DESC LIMIT 30"
    ).fetchall()

    hrv_status = {"value": None, "baseline": None, "trend": "unknown", "color": "gray"}
    if len(hrv_rows) >= 2:
        current_hrv = hrv_rows[0][1]
        baseline_hrv = sum(r[1] for r in hrv_rows[1:8]) / min(len(hrv_rows) - 1, 7)
        std_hrv = max(1.0, (sum((r[1] - baseline_hrv) ** 2 for r in hrv_rows[1:8]) / min(len(hrv_rows) - 1, 7)) ** 0.5)

        deviation = (current_hrv - baseline_hrv) / std_hrv
        hrv_status = {
            "value": current_hrv,
            "baseline": round(baseline_hrv, 1),
            "deviation": round(deviation, 2),
            "trend": "above" if deviation > 0.5 else "below" if deviation < -0.5 else "normal",
            "color": "green" if deviation > 0.5 else "red" if deviation < -0.5 else "orange",
        }

    # Resting HR analysis
    rhr_rows = conn.execute(
        """SELECT timestamp, bpm FROM heart_rate
           WHERE context = 'resting'
           ORDER BY timestamp DESC LIMIT 30"""
    ).fetchall()

    rhr_status = {"value": None, "baseline": None, "trend": "unknown", "color": "gray"}
    if len(rhr_rows) >= 2:
        current_rhr = rhr_rows[0][1]
        baseline_rhr = sum(r[1] for r in rhr_rows[1:8]) / min(len(rhr_rows) - 1, 7)
        std_rhr = max(1.0, (sum((r[1] - baseline_rhr) ** 2 for r in rhr_rows[1:8]) / min(len(rhr_rows) - 1, 7)) ** 0.5)

        # For RHR, lower is better (inverted)
        deviation = (baseline_rhr - current_rhr) / std_rhr
        rhr_status = {
            "value": current_rhr,
            "baseline": round(baseline_rhr, 1),
            "deviation": round(deviation, 2),
            "trend": "lower" if deviation > 0.5 else "elevated" if deviation < -0.5 else "normal",
            "color": "green" if deviation > 0.5 else "red" if deviation < -0.5 else "orange",
        }

    # Sleep analysis
    sleep_rows = conn.execute(
        "SELECT date, total_minutes FROM sleep ORDER BY date DESC LIMIT 14"
    ).fetchall()

    # Sleep score from wellness
    sleep_score_rows = conn.execute(
        "SELECT date, sleep_score FROM wellness WHERE sleep_score IS NOT NULL ORDER BY date DESC LIMIT 7"
    ).fetchall()

    sleep_status = {"last_night": None, "avg_7d": None, "color": "gray", "sleep_score": None}
    if sleep_rows:
        last_night = sleep_rows[0][1] / 60 if sleep_rows[0][1] else 0
        avg_7d = sum(r[1] for r in sleep_rows[:7]) / min(len(sleep_rows), 7) / 60

        if last_night >= 7.5:
            color = "green"
        elif last_night >= 6:
            color = "orange"
        else:
            color = "red"

        sleep_status = {
            "last_night_hours": round(last_night, 1),
            "avg_7d_hours": round(avg_7d, 1),
            "color": color,
        }

    if sleep_score_rows:
        sleep_status["sleep_score"] = sleep_score_rows[0][1]
        sleep_status["sleep_score_date"] = sleep_score_rows[0][0]

    # SpO2
    spo2_row = conn.execute(
        "SELECT date, spo2 FROM wellness WHERE spo2 IS NOT NULL ORDER BY date DESC LIMIT 1"
    ).fetchone()
    spo2_status = {"value": spo2_row[1], "date": spo2_row[0]} if spo2_row else None

    return {
        "hrv": hrv_status,
        "rhr": rhr_status,
        "sleep": sleep_status,
        "spo2": spo2_status,
    }


# ============================================================
# Main entry point
# ============================================================

def get_full_recovery_report() -> dict:
    """Generate a complete recovery report with all three models."""
    conn = get_connection()
    try:
        return {
            "fatigue": get_current_fatigue(conn),
            "tsb": get_current_tsb(conn),
            "tsb_history": calculate_ctl_atl_tsb(conn, days=60),
            "physio": get_physiological_status(conn),
            "resting_hr": get_resting_hr(conn),
            "max_hr": get_max_hr(conn),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    report = get_full_recovery_report()
    # Print summary
    print("=== TRAINING LOAD ===")
    f = report["fatigue"]
    print(f"Remaining fatigue: {f['total_remaining_trimp']} TRIMP")
    print(f"Hours to recovered: {f['hours_to_recovered']:.0f}h")
    for a in f["activities"]:
        print(f"  {a['date']} {a['type']}: TRIMP={a['trimp']}, remaining={a['remaining']}")

    print("\n=== FITNESS-FATIGUE ===")
    t = report["tsb"]
    print(f"CTL={t['ctl']:.1f}  ATL={t['atl']:.1f}  TSB={t['tsb']:.1f}")
    print(f"Status: {t['interpretation']}")

    print("\n=== PHYSIOLOGICAL ===")
    p = report["physio"]
    print(f"HRV: {p['hrv']['value']} (baseline {p['hrv']['baseline']}) → {p['hrv']['trend']}")
    print(f"RHR: {p['rhr']['value']} (baseline {p['rhr']['baseline']}) → {p['rhr']['trend']}")
    print(f"Sleep: {p['sleep'].get('last_night_hours', 'N/A')}h last night, {p['sleep'].get('avg_7d_hours', 'N/A')}h avg")
    if p['spo2']:
        print(f"SpO2: {p['spo2']['value']}%")
