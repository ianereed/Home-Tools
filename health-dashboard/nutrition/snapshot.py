"""Assemble the engine's snapshot from the health DB plus live Garmin/calendar sources.

fetch_* functions do I/O and are wrapped by the refresh runner so one failing source falls back
(cached copy / empty) instead of failing the run. assemble() is pure.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import calendar_source, projection
from .intake import intake_by_date

ACTIVITY_SQL = """SELECT date, start_time, name, type, duration_s, moving_s, calories, bmr_kcal, avg_power_w, np_w,
                         avg_hr, source_id FROM activities
                  WHERE source = 'garmin' AND date BETWEEN ? AND ? ORDER BY start_time"""


def db_activities(conn, start: str, end: str) -> list[dict]:
    out = []
    for r in conn.execute(ACTIVITY_SQL, (start, end)).fetchall():
        dur = r[4] or 0
        if not dur:
            continue  # manual entries without a duration (e.g. gear mileage)
        out.append({"date": r[0], "start": (r[1] or "")[11:16] or "12:00", "name": r[2] or r[3], "type": r[3],
                    "duration_s": dur, "moving_s": r[5] or dur, "kcal": r[6] or 0, "bmr_kcal": r[7] or 0,
                    "avg_power_w": r[8], "np_w": r[9], "avg_hr": r[10], "id": r[11]})
    return [a for a in out if a["type"] != "incident_detected"]


def db_daily_stats(conn, start: str, end: str) -> list[dict]:
    rows = conn.execute("SELECT date, total_kcal, active_kcal, bmr_kcal FROM daily_energy WHERE source='garmin' "
                        "AND date BETWEEN ? AND ? ORDER BY date", (start, end)).fetchall()
    return [{"date": r[0], "total": r[1], "active": r[2], "resting": r[3]} for r in rows if r[1]]


def db_body(conn) -> tuple[dict | None, float | None]:
    """(dexa-like body composition, current weight kg). Prefers a DEXA row; falls back to the newest
    Garmin row that carries lean mass (e.g. a DEXA scan entered into Garmin)."""
    row = conn.execute("SELECT timestamp, weight_kg, body_fat_pct, lean_mass_kg, fat_mass_kg, bone_mass_kg, source "
                       "FROM body_composition WHERE lean_mass_kg IS NOT NULL ORDER BY (source = 'dexa') DESC, timestamp DESC "
                       "LIMIT 1").fetchone()
    dexa = None
    if row and row[1]:
        total, lean, bone = row[1], row[3], row[5] or 0.0
        fat = row[4] if row[4] is not None else max(0.0, total - lean - bone)
        dexa = {"date": row[0][:10], "total_mass_kg": total, "lean_mass_kg": lean, "bone_mass_kg": bone,
                "fat_mass_kg": fat, "tissue_fat_pct": row[2], "source": row[6]}
    wrows = conn.execute("SELECT timestamp, weight_kg FROM body_weight WHERE weight_kg IS NOT NULL "
                         "ORDER BY timestamp DESC LIMIT 7").fetchall()
    weight = None
    if wrows and dexa:
        newest = date.fromisoformat(wrows[0][0][:10])
        if (date.today() - newest).days <= 10:
            weight = sum(w for _, w in wrows) / len(wrows)
    return dexa, weight


def athlete_from_garmin(user_settings: dict | None, ftp: float | None, fallback: dict) -> dict:
    ud = (user_settings or {}).get("userData") or {}
    sleep = (user_settings or {}).get("userSleep") or {}
    a = {
        "sex": (ud.get("gender") or fallback.get("sex") or "male").lower(),
        "birth_date": ud.get("birthDate") or fallback.get("birth_date"),
        "height_cm": ud.get("height") or fallback.get("height_cm"),
        "ftp_w": ftp or fallback.get("ftp_w"),
        "lthr_bpm": ud.get("lactateThresholdHeartRate") or fallback.get("lthr_bpm"),
        "available_days": ud.get("availableTrainingDays") or fallback.get("available_days"),
        "long_days": ud.get("preferredLongTrainingDays") or fallback.get("long_days"),
        "wake_s": sleep.get("wakeTime") if sleep.get("wakeTime") is not None else fallback.get("wake_s"),
    }
    missing = [k for k in ("birth_date", "height_cm", "ftp_w") if not a.get(k)]
    if missing:
        raise ValueError(f"athlete profile missing {missing} (Garmin profile + athlete_fallback)")
    return a


def earliest_food(athlete: dict, constraints: dict) -> str | None:
    if athlete.get("wake_s") is None:
        return None
    gap = constraints.get("first_food_min_after_wake") or 0   # never before waking, even without a med gap
    m = int(athlete["wake_s"] // 60 + gap)
    return f"{m // 60:02d}:{m % 60:02d}"


def assemble(*, today: date, settings: dict, constraints: dict, athlete: dict, dexa: dict, weight_kg: float | None,
             coach_plan: dict | None, coach_workouts: list[dict], coach_versions: list[dict],
             activities: list[dict], daily_stats: list[dict], intake: dict, calendar_events: list[dict],
             generated_since: date | None = None) -> dict:
    end = date.fromisoformat(settings["horizon_end"])
    end = max(end, today + timedelta(days=13))
    days = (end - today).days + 1
    window_end = date.fromisoformat(coach_workouts[-1]["date"]) if coach_workouts else None
    plan_end = date.fromisoformat(coach_plan["end"]) if coach_plan else None
    coach_active = coach_plan is not None
    projected = projection.project(today, end, coach_window_end=window_end, plan_end=plan_end, coach_active=coach_active,
                                   versions=coach_versions, athlete=athlete, generated_since=generated_since)
    t = today.isoformat()
    prev_start = (today - timedelta(days=14)).isoformat()
    in_window = [w for w in coach_workouts if w["date"] >= t]
    return {
        "plan_start": t, "plan_days": days, "plan_end": end.isoformat(),
        "athlete": athlete, "dexa": dexa, "weight_kg": weight_kg,
        "coach_plan": {"window_end": window_end.isoformat() if window_end else None,
                       "plan_end": coach_plan["end"] if coach_plan else None, "active": coach_active,
                       "name": coach_plan.get("name") if coach_plan else None},
        "coach_workouts": in_window,
        "projected_workouts": projected,
        "planned_extras": calendar_source.trip_sessions(calendar_events, settings),
        "activities_prev_14d": [a for a in activities if prev_start <= a["date"] < t],
        "activities_plan_window": [a for a in activities if a["date"] >= t],
        "daily_stats_prev_14d": [x for x in daily_stats if prev_start <= x["date"] < t],
        "intake": intake,
        "calendar": calendar_events,
        "settings": settings,
        "constraints": {**constraints, "earliest_food": earliest_food(athlete, constraints)},
    }


def week_to_date(snap: dict, retro_rows: list[dict]) -> list[dict]:
    """Prior days of the current week with model burn + logged intake (feeds the weekly deficit block)."""
    t = date.fromisoformat(snap["plan_start"])
    mon = (t - timedelta(days=t.weekday())).isoformat()
    tdee = {r["date"]: r["tdee"] for r in retro_rows}
    out = []
    for ds in sorted(tdee):
        if mon <= ds < snap["plan_start"]:
            ik = (snap.get("intake") or {}).get(ds, {}).get("calories_kcal")
            out.append({"date": ds, "tdee": tdee[ds], "intake_kcal": ik})
    return out
