"""Daily training and sleep recommendations based on recovery data."""

import sqlite3
from datetime import date, timedelta

from .engine import get_full_recovery_report, get_connection

SLEEP_NEED_HOURS = 8.0


def calculate_sleep_debt(conn: sqlite3.Connection, days: int = 7) -> float:
    """Calculate accumulated sleep debt over the past N nights.

    Sleep debt = sum of (target - actual) for nights where actual < target.
    Capped at 14h — beyond that it doesn't accumulate meaningfully.
    """
    rows = conn.execute(
        """SELECT date, total_minutes FROM sleep
           WHERE date >= date('now', ?) AND source = 'apple'
           ORDER BY date DESC LIMIT ?""",
        (f"-{days} days", days),
    ).fetchall()

    # Fall back to any source if no apple data
    if not rows:
        rows = conn.execute(
            """SELECT date, total_minutes FROM sleep
               WHERE date >= date('now', ?)
               ORDER BY date DESC LIMIT ?""",
            (f"-{days} days", days),
        ).fetchall()

    debt = 0.0
    for row in rows:
        actual_hours = (row[1] or 0) / 60
        daily_debt = max(0, SLEEP_NEED_HOURS - actual_hours)
        debt += daily_debt

    return min(debt, 14.0)


def get_last_night_sleep(conn: sqlite3.Connection) -> dict:
    """Get last night's sleep data."""
    row = conn.execute(
        """SELECT date, total_minutes, deep_minutes, rem_minutes,
                  light_minutes, awake_minutes, source
           FROM sleep ORDER BY date DESC LIMIT 1"""
    ).fetchone()

    if not row:
        return None

    total_hrs = (row[1] or 0) / 60

    # 7-day average
    avg_row = conn.execute(
        """SELECT AVG(total_minutes) FROM sleep
           WHERE date >= date('now', '-7 days')"""
    ).fetchone()
    avg_hrs = (avg_row[0] or 0) / 60

    # Sleep score from wellness
    score_row = conn.execute(
        "SELECT sleep_score FROM wellness WHERE date = ? AND sleep_score IS NOT NULL",
        (row[0],),
    ).fetchone()

    return {
        "date": row[0],
        "total_hours": round(total_hrs, 1),
        "deep_minutes": row[2] or 0,
        "rem_minutes": row[3] or 0,
        "light_minutes": row[4] or 0,
        "awake_minutes": row[5] or 0,
        "source": row[6],
        "avg_7d_hours": round(avg_hrs, 1),
        "diff_from_avg": round(total_hrs - avg_hrs, 1),
        "sleep_score": score_row[0] if score_row else None,
    }


def get_sleep_recommendation(sleep_debt: float, last_night_hours: float) -> dict:
    """Generate sleep recommendation based on accumulated debt."""
    if sleep_debt < 2:
        return {
            "level": "good",
            "color": "green",
            "message": "Sleep is on track. Normal routine tonight.",
        }
    elif sleep_debt < 4:
        return {
            "level": "mild",
            "color": "yellow",
            "message": "Mild sleep debt. Try to get to bed 30 minutes earlier tonight.",
        }
    elif sleep_debt < 7:
        return {
            "level": "moderate",
            "color": "orange",
            "message": (
                "Moderate sleep debt. Prioritize sleep tonight — aim for 9+ hours. "
                "Avoid alcohol and caffeine after noon."
            ),
        }
    else:
        return {
            "level": "severe",
            "color": "red",
            "message": (
                "Significant sleep debt. Make sleep your #1 priority tonight. "
                "Go to bed early, avoid screens, skip alcohol. Consider a rest day tomorrow."
            ),
        }


def get_training_recommendation(
    tsb: float,
    hrv_zscore: float | None,
    rhr_deviation: float | None,
    last_night_hours: float,
    sleep_debt: float,
) -> dict:
    """Generate training intensity recommendation.

    Based on combined decision framework from:
    - HRV4Training (Marco Altini) — HRV z-score thresholds
    - TrainingPeaks — TSB zones
    - Elite training centers — RHR elevation rules
    """
    hrv_z = hrv_zscore if hrv_zscore is not None else 0.0
    rhr_dev = rhr_deviation if rhr_deviation is not None else 0.0

    # Red: Rest day
    if hrv_z < -1.0 or rhr_dev < -1.5 or last_night_hours < 5:
        return {
            "level": "rest",
            "color": "red",
            "message": "Rest Day — Your body needs recovery. Take the day off or do very light walking only.",
            "icon": "bed",
        }

    # Orange: Easy only
    if hrv_z < -0.5 or rhr_dev < -0.75 or last_night_hours < 6 or tsb < -30:
        return {
            "level": "easy",
            "color": "orange",
            "message": "Easy Activity Only — Light movement is fine, but keep heart rate low. Zone 1-2 only.",
            "icon": "walking",
        }

    # Yellow: Take it easy (fatigue + sleep debt combo)
    if tsb < -10 and sleep_debt > 3:
        return {
            "level": "moderate_low",
            "color": "orange",
            "message": "Take It Easy — You have accumulated fatigue plus sleep debt. Keep intensity low today.",
            "icon": "walking",
        }

    # Green: Train hard
    if tsb > 0 and hrv_z > -0.5 and last_night_hours >= 7:
        return {
            "level": "hard",
            "color": "green",
            "message": "Ready to Train Hard — You're fresh and well-rested. Push yourself today.",
            "icon": "fire",
        }

    # Default: Moderate
    return {
        "level": "moderate",
        "color": "blue",
        "message": "Moderate Activity OK — Structured training is fine. Listen to your body and adjust if needed.",
        "icon": "running",
    }


def get_data_timestamps(conn: sqlite3.Connection) -> dict:
    """Get the most recent data timestamp for each source used on the Today page."""
    timestamps = {}

    # Sleep: latest date in sleep table
    row = conn.execute("SELECT date FROM sleep ORDER BY date DESC LIMIT 1").fetchone()
    timestamps["sleep"] = row[0] if row else None

    # Heart rate (resting): latest timestamp
    row = conn.execute(
        "SELECT timestamp FROM heart_rate WHERE context = 'resting' ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    timestamps["resting_hr"] = row[0] if row else None

    # HRV: latest date in wellness with non-null hrv
    row = conn.execute(
        "SELECT date FROM wellness WHERE hrv IS NOT NULL ORDER BY date DESC LIMIT 1"
    ).fetchone()
    timestamps["hrv"] = row[0] if row else None

    # Activities (for TRIMP/TSB): latest activity date
    row = conn.execute("SELECT date FROM activities ORDER BY date DESC LIMIT 1").fetchone()
    timestamps["activities"] = row[0] if row else None

    # Sleep score: latest wellness with non-null sleep_score
    row = conn.execute(
        "SELECT date FROM wellness WHERE sleep_score IS NOT NULL ORDER BY date DESC LIMIT 1"
    ).fetchone()
    timestamps["sleep_score"] = row[0] if row else None

    return timestamps


def get_today_summary() -> dict:
    """Generate the complete daily summary for the Today tab."""
    conn = get_connection()

    try:
        report = get_full_recovery_report()
        sleep_debt = calculate_sleep_debt(conn)
        last_night = get_last_night_sleep(conn)
        data_timestamps = get_data_timestamps(conn)

        last_night_hours = last_night["total_hours"] if last_night else 0

        # Extract z-scores from recovery report
        hrv_zscore = report["physio"]["hrv"].get("deviation")
        rhr_deviation = report["physio"]["rhr"].get("deviation")
        tsb_val = report["tsb"]["tsb"]

        training_rec = get_training_recommendation(
            tsb=tsb_val,
            hrv_zscore=hrv_zscore,
            rhr_deviation=rhr_deviation,
            last_night_hours=last_night_hours,
            sleep_debt=sleep_debt,
        )

        sleep_rec = get_sleep_recommendation(sleep_debt, last_night_hours)

        return {
            "training": training_rec,
            "sleep_last_night": last_night,
            "sleep_debt_hours": round(sleep_debt, 1),
            "sleep_rec": sleep_rec,
            "recovery": {
                "trimp_remaining": report["fatigue"]["total_remaining_trimp"],
                "hours_to_recovered": report["fatigue"]["hours_to_recovered"],
                "ctl": report["tsb"]["ctl"],
                "atl": report["tsb"]["atl"],
                "tsb": report["tsb"]["tsb"],
                "tsb_interpretation": report["tsb"]["interpretation"],
                "hrv": report["physio"]["hrv"],
                "rhr": report["physio"]["rhr"],
            },
            "data_timestamps": data_timestamps,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    summary = get_today_summary()
    print(f"\n{'='*60}")
    print(f"TODAY'S RECOMMENDATION: {summary['training']['message']}")
    print(f"{'='*60}")
    if summary["sleep_last_night"]:
        s = summary["sleep_last_night"]
        print(f"\nLast night: {s['total_hours']}h ({s['diff_from_avg']:+.1f}h vs 7-day avg)")
    print(f"Sleep debt: {summary['sleep_debt_hours']}h")
    print(f"Tonight: {summary['sleep_rec']['message']}")
    print(f"\nRecovery: TRIMP={summary['recovery']['trimp_remaining']:.0f}, "
          f"TSB={summary['recovery']['tsb']:.1f} ({summary['recovery']['tsb_interpretation']})")
