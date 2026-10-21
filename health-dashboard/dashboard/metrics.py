"""Every number the dashboard shows, computed one way.

Each metric has exactly one function here. Overview tiles, page charts, Goals
levers and the Nutrition tab all call these, so a value can't differ between
tabs. Targets come from `dashboard.targets`.

Functions return pandas frames or small dicts and read `collectors.db.DB_PATH`
at call time (tests point it at a tmp DB). Queries are cached for five
minutes, keyed on the DB path as well as the SQL.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import collectors.db as db
from dashboard import targets as T
from recovery.resting_hr import DAILY_RESTING_HR_SQL


# =============================================================================
# Data access
# =============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _read(sql: str, params: tuple, path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    conn = sqlite3.connect(path)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()          # pre-migration DB: missing table/column
    finally:
        conn.close()


def read(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Cached read-only query against health.db; empty frame if the DB or table is missing."""
    return _read(sql, tuple(params), db.DB_PATH)


def since(days: int, today: date | None = None) -> str:
    """First date of a `days`-long window as 'YYYY-MM-DD' (huge windows clamp to 1900)."""
    today = pd.Timestamp(today or date.today()).date()
    return max(today - timedelta(days=days), date(1900, 1, 1)).isoformat()


def week_start(ts: pd.Series) -> pd.Series:
    """Monday of each timestamp's week. Every weekly bucket on every tab uses this."""
    return pd.to_datetime(ts).dt.to_period("W-SUN").dt.start_time


def this_monday(today: date | None = None) -> pd.Timestamp:
    t = pd.Timestamp(today or date.today()).normalize()
    return t - pd.Timedelta(days=t.weekday())


def _dated(df: pd.DataFrame, col: str = "date") -> pd.DataFrame:
    if not df.empty:
        df = df.copy()
        df[col] = pd.to_datetime(df[col])
    return df


def avg_window(df: pd.DataFrame, col: str, end, days: int = 7) -> float | None:
    """Mean of `col` over the `days` days ending at `end` (inclusive). The one
    definition of "7-day average" on every tab."""
    if df.empty:
        return None
    end = pd.Timestamp(end)
    w = df[(df["date"] > end - pd.Timedelta(days=days)) & (df["date"] <= end)][col].dropna()
    return w.mean() if not w.empty else None


def rolling_avg(df: pd.DataFrame, col: str, days: int = 7) -> pd.Series:
    """Trailing `days`-calendar-day average at each row (the chart version of
    avg_window). A gap longer than the window breaks the line instead of
    averaging across it."""
    if df.empty:
        return pd.Series(dtype=float)
    s = df.set_index("date")[col].rolling(f"{days}D", min_periods=1).mean()
    gap = df["date"].diff() > pd.Timedelta(days=days)
    s[gap.to_numpy()] = float("nan")
    return pd.Series(s.to_numpy(), index=df.index)


def rolling_low(df: pd.DataFrame, col: str, days: int = 7) -> pd.Series:
    """Trailing `days`-calendar-day minimum at each row; gaps break the line."""
    if df.empty:
        return pd.Series(dtype=float)
    s = df.set_index("date")[col].rolling(f"{days}D", min_periods=1).min()
    gap = df["date"].diff() > pd.Timedelta(days=days)
    s[gap.to_numpy()] = float("nan")
    return pd.Series(s.to_numpy(), index=df.index)


def window_stats(s: pd.DataFrame, col: str, today: date | None = None) -> dict:
    """latest value + 7-day and 30-day averages of a dated daily series."""
    s = s[s[col].notna()] if not s.empty else s
    if s.empty:
        return {"latest": None, "latest_date": None, "avg7": None, "avg30": None}
    end = pd.Timestamp(today or date.today())
    return {"latest": s[col].iloc[-1], "latest_date": s["date"].iloc[-1],
            "avg7": avg_window(s, col, end, 7), "avg30": avg_window(s, col, end, 30)}


# =============================================================================
# Recovery: sleep, HRV, resting HR, SpO2
# =============================================================================

SLEEP_SOURCE_PREFERENCE = ("garmin", "apple", "suunto")
_SLEEP_RANK = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(SLEEP_SOURCE_PREFERENCE))


def sleep_nightly(start: str = "") -> pd.DataFrame:
    """One row per night, best source first (Garmin, Apple, Suunto).

    Drops Garmin firmware artifacts: nights over 14 h and the "whole wear
    period logged as deep" signature (deep > 60 % of a >6 h night).
    Columns: date, hours, deep_h, rem_h, light_h, awake_h.
    """
    df = read(f"""
        SELECT date, total_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY date ORDER BY CASE source {_SLEEP_RANK} ELSE 9 END) rn
            FROM sleep
            WHERE date >= ? AND total_minutes > 0 AND total_minutes <= 840
              AND NOT (deep_minutes > 0.6 * total_minutes AND total_minutes > 360))
        WHERE rn = 1 ORDER BY date""", (start,))
    if df.empty:
        return df
    df = _dated(df)
    out = pd.DataFrame({"date": df["date"], "hours": df["total_minutes"] / 60})
    for src, dst in (("deep_minutes", "deep_h"), ("rem_minutes", "rem_h"),
                     ("light_minutes", "light_h"), ("awake_minutes", "awake_h")):
        out[dst] = df[src] / 60
    return out.reset_index(drop=True)


def _wellness(col: str, start: str = "") -> pd.DataFrame:
    # wellness is one row per (date, source); Garmin owns it since 2026-05.
    return _dated(read(
        f"SELECT date, AVG({col}) AS {col} FROM wellness WHERE {col} IS NOT NULL AND date >= ? "
        "GROUP BY date ORDER BY date", (start,)))


def hrv_daily(start: str = "") -> pd.DataFrame:
    return _wellness("hrv", start)


def steps_daily(start: str = "") -> pd.DataFrame:
    return _wellness("steps", start)


def spo2_daily(start: str = "") -> pd.DataFrame:
    return _wellness("spo2", start)


def sleep_score_daily(start: str = "") -> pd.DataFrame:
    return _wellness("sleep_score", start)


def sleeping_hr_daily(start: str = "") -> pd.DataFrame:
    return _wellness("avg_sleeping_hr", start)


def resting_hr_daily(start: str = "") -> pd.DataFrame:
    """One source-preferred resting HR per day (see recovery.resting_hr)."""
    return _dated(read(DAILY_RESTING_HR_SQL, (start,)))


# =============================================================================
# Training: activities, volume, HR zones, load
# =============================================================================

_MAX_PLAUSIBLE_KMH = 80   # faster than this is a logging artifact, not a workout


def activities(start: str = "") -> pd.DataFrame:
    """Canonical activities (cross-source duplicates hidden), newest first.

    `stream_id` resolves the HR stream from whichever copy in the dup group
    carries it. `counts_distance` is False for rows whose distance can't be
    real (no duration, or > 80 km/h — e.g. a para_sports row logging 1,287 km).
    """
    df = read("""
        SELECT a.id, a.date, a.type, a.duration_minutes, a.distance_km, a.avg_hr, a.max_hr, a.calories,
               COALESCE(
                 (SELECT a.source_id WHERE EXISTS
                    (SELECT 1 FROM activity_streams s WHERE s.activity_id = a.source_id)),
                 (SELECT a2.source_id FROM activities a2
                    JOIN activity_streams s ON s.activity_id = a2.source_id
                   WHERE a2.dup_of = a.id LIMIT 1)) AS stream_id
        FROM activities a
        WHERE a.date >= ? AND a.dup_of IS NULL
        ORDER BY a.date DESC, a.id DESC""", (start,))
    if df.empty:
        return df
    df = _dated(df)
    hours = df["duration_minutes"].fillna(0) / 60
    speed = df["distance_km"].fillna(0) / hours.where(hours > 0)
    df["counts_distance"] = (hours > 0) & ~(speed > _MAX_PLAUSIBLE_KMH)
    return df


def distance_km(acts: pd.DataFrame) -> float:
    if acts.empty:
        return 0.0
    return float(acts.loc[acts["counts_distance"], "distance_km"].fillna(0).sum())


def weekly_volume(acts: pd.DataFrame) -> pd.DataFrame:
    """Per Monday-week: hours and session count."""
    if acts.empty:
        return pd.DataFrame(columns=["week", "hours", "sessions"])
    a = acts.assign(week=week_start(acts["date"]))
    return (a.groupby("week")
             .agg(hours=("duration_minutes", lambda x: x.fillna(0).sum() / 60),
                  sessions=("id", "count"))
             .reset_index().sort_values("week"))


def zone_of(bpm, hrmax: int = T.HRMAX):
    """Zone label(s) for bpm value(s): below Z2's floor counts as Z1."""
    pct = pd.Series(bpm, dtype=float) / hrmax
    bins = [-1] + list(T.ZONE_PCT.values())[1:] + [99]
    return pd.cut(pct, bins=bins, labels=list(T.ZONE_PCT), right=False)


_STREAM_GAP_CAP_S = 60   # a dropout must not credit minutes to a zone


def zone_minutes(start: str = "") -> pd.DataFrame:
    """Minutes per HR zone per activity, from the activity HR streams.

    Columns: date, Z1..Z5. Activities without a stream contribute nothing (HR
    zones need samples, not an average).
    """
    acts = activities(start)
    zones = list(T.ZONE_PCT)
    acts = acts[acts["stream_id"].notna()] if not acts.empty else acts
    if acts.empty:
        return pd.DataFrame(columns=["date", *zones])
    ids = tuple(str(i) for i in acts["stream_id"])
    marks = ",".join("?" * len(ids))
    s = read(f"SELECT activity_id, timestamp_offset, bpm FROM activity_streams "
             f"WHERE bpm IS NOT NULL AND activity_id IN ({marks}) "
             "ORDER BY activity_id, timestamp_offset", ids)
    if s.empty:
        return pd.DataFrame(columns=["date", *zones])
    nxt = s.groupby("activity_id")["timestamp_offset"].shift(-1)
    s["dt"] = (nxt - s["timestamp_offset"]).fillna(1).clip(0, _STREAM_GAP_CAP_S)
    s["zone"] = zone_of(s["bpm"]).astype(str)
    per = s.pivot_table(index="activity_id", columns="zone", values="dt", aggfunc="sum",
                        fill_value=0) / 60
    per = per.reindex(columns=zones, fill_value=0)
    date_of = dict(zip(acts["stream_id"].astype(str), acts["date"]))
    per["date"] = [date_of.get(str(i)) for i in per.index]
    return per.reset_index(drop=True)[["date", *zones]]


def zone_minutes_weekly(start: str = "") -> pd.DataFrame:
    """Per Monday-week: Z1..Z5 minutes plus moderate-vigorous (Z3-5) and vigorous (Z4-5)."""
    z = zone_minutes(start)
    zones = list(T.ZONE_PCT)
    if z.empty:
        return pd.DataFrame(columns=["week", *zones, "modvig", "vig"])
    w = z.assign(week=week_start(z["date"])).groupby("week")[zones].sum()
    full = pd.date_range(w.index.min(), this_monday(), freq="W-MON")
    w = w.reindex(full, fill_value=0)
    w.index.name = "week"
    w["modvig"] = w[list(T.MODERATE_ZONES)].sum(axis=1)
    w["vig"] = w[list(T.VIGOROUS_ZONES)].sum(axis=1)
    return w.reset_index()


def exercise_summary(today: date | None = None) -> dict | None:
    """4-week average of moderate-vigorous / vigorous minutes over complete
    weeks (the current, partial week is excluded) vs the AHA guideline."""
    w = zone_minutes_weekly(since(12 * 7 + 7, today))
    done = w[w["week"] < this_monday(today)].tail(4) if not w.empty else w
    if done.empty:
        return None
    mv, vg = done["modvig"].mean(), done["vig"].mean()
    return {"modvig": mv, "vig": vg,
            "meets_aha": mv >= T.AHA_MODERATE_MIN_WK or vg >= T.AHA_VIGOROUS_MIN_WK,
            "spark": w["modvig"].tolist()[-12:]}


def training_load(days: int) -> pd.DataFrame:
    """Fitness (CTL), fatigue (ATL) and form (TSB) for the last `days` days.
    The recovery engine seeds the averages from earlier history."""
    from recovery import engine
    conn = db.get_connection()
    try:
        hist = engine.calculate_ctl_atl_tsb(conn, days=days)
    finally:
        conn.close()
    return _dated(pd.DataFrame(hist)) if hist else pd.DataFrame(columns=["date", "ctl", "atl", "tsb"])


LOAD_MAX_DAYS = 365   # the load model scans per-second HR streams; a year is plenty


@st.cache_data(ttl=300, show_spinner=False)
def _training_load_year(path: str) -> pd.DataFrame:
    return training_load(LOAD_MAX_DAYS)


def training_load_cached(days: int) -> pd.DataFrame:
    """training_load for the last min(days, 365) days, from one cached year."""
    df = _training_load_year(db.DB_PATH)
    if df.empty:
        return df
    return df[df["date"] >= pd.Timestamp(since(min(days, LOAD_MAX_DAYS)))].reset_index(drop=True)


# =============================================================================
# Body: weight
# =============================================================================

def weight_daily(start: str = "") -> pd.DataFrame:
    """Per day, in lb (all scale/DEXA sources): the mean of that day's
    readings and the lowest single reading. Columns: date, lb, low."""
    w = read("SELECT date(timestamp) AS date, AVG(weight_kg) AS kg, MIN(weight_kg) AS low_kg "
             "FROM body_weight WHERE timestamp >= ? GROUP BY date(timestamp) ORDER BY date", (start,))
    if w.empty:
        return pd.DataFrame(columns=["date", "lb", "low"])
    w = _dated(w)
    return pd.DataFrame({"date": w["date"], "lb": w["kg"] * T.KG_TO_LB, "low": w["low_kg"] * T.KG_TO_LB})


def weight_now(today: date | None = None) -> dict | None:
    """The current weight, two ways, over the last 7 days:

    - lb: the mean of daily means (the trend number goals and deltas use)
    - low_lb: the lowest single reading, the closest to "true" weight
      (least water, food and clothing; a dehydrated post-ride reading can
      still read low)

    With no weigh-in in the last 7 days both fall back to the latest day,
    flagged stale. Also returns the latest reading and its age."""
    w = weight_daily()
    if w.empty:
        return None
    end = pd.Timestamp(today or date.today())
    last7 = w[w["date"] > end - pd.Timedelta(days=7)]
    latest = w.iloc[-1]
    age = int((end.normalize() - latest["date"].normalize()).days)
    win, stale = (last7, False) if not last7.empty else (w.tail(1), True)
    low = win.loc[win["low"].idxmin()]
    return {"lb": win["lb"].mean(), "low_lb": low["low"], "low_date": low["date"],
            "basis": "latest" if stale else "7-day mean", "n": len(win),
            "latest_lb": latest["lb"], "age_days": age, "stale": stale}


def weight_weekly(start: str = "") -> pd.DataFrame:
    """Per Monday-week: mean of the daily means, the lowest single reading, and
    the number of weigh-in days. Weeks with no weigh-ins are kept as gaps."""
    w = weight_daily(start)
    if w.empty:
        return pd.DataFrame(columns=["week", "lb", "low", "n"])
    wk = (w.assign(week=week_start(w["date"]))
           .groupby("week").agg(lb=("lb", "mean"), low=("low", "min"), n=("lb", "count")))
    wk = wk.reindex(pd.date_range(wk.index.min(), wk.index.max(), freq="W-MON"))
    wk.index.name = "week"
    return wk.reset_index()


# =============================================================================
# Body composition: DEXA and the Garmin scale, never mixed
# =============================================================================

def dexa_scans() -> pd.DataFrame:
    """Every DEXA scan, oldest first, masses in lb. Columns: date, weight, body_fat_pct,
    lean, fat, bone, visceral, note."""
    d = read("SELECT timestamp, weight_kg, body_fat_pct, lean_mass_kg, fat_mass_kg, bone_mass_kg, "
             "visceral_fat_mass_kg, note FROM body_composition WHERE source = 'dexa' ORDER BY timestamp")
    if d.empty:
        return pd.DataFrame(columns=["date", "weight", "body_fat_pct", "lean", "fat", "bone", "visceral", "note"])
    out = pd.DataFrame({"date": pd.to_datetime(d["timestamp"]).dt.normalize(),
                        "body_fat_pct": d["body_fat_pct"], "note": d["note"]})
    for src, dst in (("weight_kg", "weight"), ("lean_mass_kg", "lean"), ("fat_mass_kg", "fat"),
                     ("bone_mass_kg", "bone"), ("visceral_fat_mass_kg", "visceral")):
        out[dst] = d[src] * T.KG_TO_LB
    return out


def weight_goals() -> list[dict]:
    """The weight goals (targets.BODY_FAT_GOALS) worked out from the latest DEXA
    scan: [{label, pct, lb, fat_lb, scan_date}], or [] with no scan on file."""
    scans = dexa_scans()
    scans = scans[scans["lean"].notna()] if not scans.empty else scans
    if scans.empty:
        return []
    s = scans.iloc[-1]
    bone = s["bone"] if pd.notna(s["bone"]) else 0.0
    out = []
    for label, pct in T.BODY_FAT_GOALS:
        lb = T.weight_at_body_fat(s["lean"], bone, pct)
        out.append({"label": label, "pct": pct, "lb": lb, "fat_lb": lb - s["lean"] - bone,
                    "scan_date": s["date"]})
    return out


def scale_composition(start: str = "") -> pd.DataFrame:
    """Garmin scale (bioimpedance) readings, one row per day (mean of that day's
    weigh-ins). Days with a DEXA scan are left out: that day's Garmin entry is
    the scan typed into Garmin, not a scale reading. Columns: date,
    body_fat_pct, muscle (lb), water_pct, bone (lb), visceral_rating."""
    d = read("SELECT date(timestamp) AS date, AVG(body_fat_pct) AS body_fat_pct, AVG(muscle_mass_kg) AS muscle_kg, "
             "AVG(body_water_pct) AS water_pct, AVG(bone_mass_kg) AS bone_kg, AVG(visceral_fat_rating) AS visceral_rating "
             "FROM body_composition WHERE source = 'garmin' AND timestamp >= ? "
             "AND date(timestamp) NOT IN (SELECT date(timestamp) FROM body_composition WHERE source = 'dexa') "
             "GROUP BY date(timestamp) ORDER BY date", (start,))
    if d.empty:
        return pd.DataFrame(columns=["date", "body_fat_pct", "muscle", "water_pct", "bone", "visceral_rating"])
    d = _dated(d)
    return pd.DataFrame({"date": d["date"], "body_fat_pct": d["body_fat_pct"],
                         "muscle": d["muscle_kg"] * T.KG_TO_LB, "water_pct": d["water_pct"],
                         "bone": d["bone_kg"] * T.KG_TO_LB, "visceral_rating": d["visceral_rating"]})


# =============================================================================
# Blood pressure
# =============================================================================

def bp_category(systolic: float, diastolic: float) -> tuple[str, str]:
    """AHA category of a reading or a mean; the worse of the two numbers governs."""
    from dashboard import lib
    if systolic > 180 or diastolic > 120:
        return "Hypertensive crisis", lib.BAD
    if systolic >= T.BP_STAGE2[0] or diastolic >= T.BP_STAGE2[1]:
        return "Stage 2", lib.BAD
    if systolic >= 130 or diastolic >= 80:
        return "Stage 1", lib.WARN
    if systolic >= 120:
        return "Elevated", lib.WARN
    return "Normal", lib.GOOD


def bp_readings() -> pd.DataFrame:
    return _dated(read("SELECT timestamp, systolic, diastolic, pulse FROM blood_pressure "
                       "ORDER BY timestamp"), "timestamp")


def bp_window(days: int = 14, now: datetime | None = None) -> dict | None:
    """Mean of the readings in the last `days` days (by timestamp) and its AHA
    category; falls back to the latest single reading when the window is empty."""
    bp = bp_readings()
    if bp.empty:
        return None
    now = pd.Timestamp(now or datetime.now())
    win = bp[bp["timestamp"] >= now - pd.Timedelta(days=days)]
    if win.empty:
        r = bp.iloc[-1]
        sys_, dia, n, basis = r["systolic"], r["diastolic"], 1, f"latest ({r['timestamp']:%b %-d})"
    else:
        sys_, dia, n, basis = win["systolic"].mean(), win["diastolic"].mean(), len(win), f"{days}-day mean"
    cat, color = bp_category(sys_, dia)
    return {"sys": sys_, "dia": dia, "n": n, "basis": basis, "category": cat, "color": color}


def bp_weekly(bp: pd.DataFrame | None = None) -> pd.DataFrame:
    """Weekly mean systolic/diastolic with n; weeks with no readings kept as gaps."""
    bp = bp_readings() if bp is None else bp
    if bp.empty:
        return pd.DataFrame(columns=["week", "sys", "dia", "n"])
    b = bp.assign(week=week_start(bp["timestamp"]))
    w = b.groupby("week").agg(sys=("systolic", "mean"), dia=("diastolic", "mean"),
                              n=("systolic", "count"))
    full = pd.date_range(w.index.min(), w.index.max(), freq="W-MON")
    w = w.reindex(full)
    w.index.name = "week"
    return w.reset_index()


# =============================================================================
# Alcohol and nutrition
# =============================================================================

def alcohol_log() -> pd.DataFrame:
    return _dated(read("SELECT date, subtype, amount FROM lifestyle_log "
                       "WHERE name = 'Alcohol' AND source = 'garmin' ORDER BY date"))


def alcohol_weekly() -> pd.Series:
    """Drinks per Monday-week (Garmin serving counts), indexed by week start."""
    df = alcohol_log()
    if df.empty:
        return pd.Series(dtype=float)
    return df.groupby(week_start(df["date"]))["amount"].sum()


def alcohol_summary(today: date | None = None) -> dict | None:
    """This calendar week's drinks and the prior 4 weeks' average."""
    weekly = alcohol_weekly()
    if weekly.empty:
        return None
    wk = this_monday(today)
    prior = weekly[weekly.index < wk]
    # weeks with no logged drinks are zero-drink weeks, not missing
    prior4 = prior.reindex(pd.date_range(wk - pd.Timedelta(weeks=4), periods=4, freq="W-MON"),
                           fill_value=0)
    return {"this_week": float(weekly.get(wk, 0)), "prior4_avg": float(prior4.mean()),
            "weekly": weekly}


def nutrition_daily(start: str = "") -> pd.DataFrame:
    """Logged intake per day (MyFitnessPal; Garmin for July 2026). sodium_mg is
    net of ride sweat sodium."""
    return _dated(read("SELECT * FROM nutrition_daily WHERE date >= ? ORDER BY date", (start,)))


def sodium_week(today: date | None = None) -> dict | None:
    """Mean daily sodium over the 7 days ending today, and how many were logged."""
    start = since(6, today)
    n = nutrition_daily(start)
    logged = n[n["sodium_mg"].notna()] if not n.empty else n
    if logged.empty:
        return None
    return {"mean": logged["sodium_mg"].mean(), "days": len(logged),
            "series": nutrition_daily(since(13, today))["sodium_mg"].dropna().tolist()}
