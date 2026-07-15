"""Build the cardiology dataset + visual report from health.db.

Outputs (into cardiology/out/):
  - quarterly.csv   one row per calendar quarter, 2020-Q1 .. current
  - weekly.csv      one row per ISO-ish week, Apr 2025 .. latest (the "statin era"
                    detailed timeline)
  - cardiology_report.html   self-contained Plotly report for human review
  - README.md       data dictionary + provenance + known gaps, so the downstream
                    Claude session reads the dataset correctly

Definitions are deliberately explicit and conservative; every metric carries its
source and a note when it is an estimate or pending a device export. See README.

Run:  cardiology/.venv/bin/python cardiology/build_report.py [--db PATH]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import clinical_data as CD

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

# --- Tunable definitions ---------------------------------------------------
HISTORY_START = date(2020, 1, 1)        # quarterly look-back floor
WEEKLY_START = date(2025, 4, 1)         # detailed weekly timeline floor (statin era)
HRMAX = 193                             # observed peak max_hr; empirical, refine w/ exports
RESTING_PCTL = 0.07                     # Apple intraday -> daily resting proxy (overnight low)
STREAM_GAP_CAP_S = 60                   # cap per-sample dt so stream gaps don't inflate a zone
# %HRmax zone lower bounds
ZONE_PCT = {"Z1": 0.50, "Z2": 0.60, "Z3": 0.70, "Z4": 0.80, "Z5": 0.90}
# Source preference when multiple devices cover the same day (best first)
RESTING_SRC_PREF = ["garmin", "suunto", "apple-official", "apple"]
SLEEP_SRC_PREF = ["garmin", "apple", "apple-export", "suunto"]

# Apple Health full-export sidecar DB (built by import_apple_export.py; optional)
APPLE_DB = os.path.join(HERE, "apple_export.db")
MIN_HR_SAMPLES_FOR_PROXY = 60   # sparse-sampling days make the p07 proxy unreliable
MIN_APPLE_ASLEEP_MIN = 120      # pre-mid-2024 Apple "sleep" is InBed schedule, asleep≈0


def apple_con():
    return sqlite3.connect(APPLE_DB) if os.path.exists(APPLE_DB) else None


def zone_of(bpm: float) -> str:
    pct = bpm / HRMAX
    z = "Z1"
    for name, lo in ZONE_PCT.items():
        if pct >= lo:
            z = name
    return z


def week_start(d: date) -> date:
    """Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def quarter_label(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


# --- Loaders ---------------------------------------------------------------

def load(con: sqlite3.Connection, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params)


def daily_resting_hr(con: sqlite3.Connection, acon=None) -> pd.DataFrame:
    """One resting-HR value per day, best source available.

    garmin/suunto store a true daily resting value; the Apple full export has
    the official RestingHeartRate metric (2021-06 onward); for Apple days
    before that we fall back to a low percentile of intraday samples
    (overnight-low proxy), skipping sparse-sampling days where the proxy
    reads high.
    """
    rows = []
    # true daily resting from garmin/suunto
    true_rhr = load(con,
        "SELECT substr(timestamp,1,10) d, source, ROUND(AVG(bpm)) rhr "
        "FROM heart_rate WHERE source IN ('garmin','suunto') AND context='resting' "
        "GROUP BY d, source")
    for _, r in true_rhr.iterrows():
        rows.append((r["d"], r["source"], r["rhr"]))
    if acon is not None:
        # Apple's official daily RestingHeartRate (full export)
        for d, v in acon.execute("SELECT date, bpm FROM resting_hr"):
            rows.append((d, "apple-official", round(v)))
        # full-history p07 proxy — only days with samples spread across the day;
        # workout-only days (dense 1-2h bursts from Strava-era apps) read 95-140
        for d, v in acon.execute(
                "SELECT date, p07 FROM daily_hr WHERE n >= ? AND n_hours >= 12",
                (MIN_HR_SAMPLES_FOR_PROXY,)):
            rows.append((d, "apple~p7", v))
    # apple proxy from the live snapshot (covers days newer than the export)
    apple = load(con,
        "SELECT substr(timestamp,1,10) d, bpm FROM heart_rate "
        "WHERE source='apple'")
    if not apple.empty:
        counts = apple.groupby("d")["bpm"].count()
        prox = apple.groupby("d")["bpm"].quantile(RESTING_PCTL).round()
        for d, v in prox.items():
            if counts[d] >= MIN_HR_SAMPLES_FOR_PROXY:
                rows.append((d, "apple~p7", v))
    df = pd.DataFrame(rows, columns=["date", "source", "rhr"])
    if df.empty:
        return df
    # collapse to one per day by source preference
    pref = {s: i for i, s in enumerate(RESTING_SRC_PREF)}
    df["rank"] = df["source"].str.replace("~p7", "", regex=False).map(pref).fillna(99)
    df = df.sort_values("rank").drop_duplicates("date", keep="first")
    df["date"] = pd.to_datetime(df["date"])
    return df[["date", "source", "rhr"]].sort_values("date")


def daily_sleep(con: sqlite3.Connection, acon=None) -> pd.DataFrame:
    # Filter Garmin firmware artifacts: ~26 nights (2023-2026) logged the whole
    # wear period as sleep (often exactly 984 min = 16.4h, all of it as "deep",
    # deep == total — physiologically impossible). Exclude >14h nights and the
    # deep==total signature so they don't inflate quarterly sleep averages.
    df = load(con,
        "SELECT date, source, total_minutes, deep_minutes, rem_minutes, "
        "light_minutes, awake_minutes FROM sleep "
        "WHERE total_minutes > 0 AND total_minutes <= 840 "
        "AND NOT (deep_minutes > 0.6 * total_minutes AND total_minutes > 360)")
    if acon is not None:
        # full-export nights with real stage tracking; pre-mid-2024 "InBed"
        # schedule rows (asleep≈0) are excluded by the threshold
        exp = pd.read_sql_query(
            "SELECT date, asleep_min AS total_minutes, deep_min AS deep_minutes, "
            "rem_min AS rem_minutes, core_min AS light_minutes, "
            "awake_min AS awake_minutes FROM sleep WHERE asleep_min >= ?",
            acon, params=(MIN_APPLE_ASLEEP_MIN,))
        if not exp.empty:
            exp["source"] = "apple-export"
            df = pd.concat([df, exp], ignore_index=True)
    if df.empty:
        return df
    pref = {s: i for i, s in enumerate(SLEEP_SRC_PREF)}
    df["rank"] = df["source"].map(pref).fillna(99)
    df = df.sort_values("rank").drop_duplicates("date", keep="first")
    df["date"] = pd.to_datetime(df["date"])
    df["sleep_hours"] = df["total_minutes"] / 60.0
    return df.sort_values("date")


def activities(con: sqlite3.Connection) -> pd.DataFrame:
    # Apple workouts are sourced from the full export (apple_workouts(), which
    # carries their HR-zone minutes from workout_zones) — NOT from the live
    # `activities` table, where the backfilled Apple rows have no HR streams.
    # Excluding source='apple' here avoids both double-counting them and losing
    # their zone minutes. (No-op on snapshots that predate the Apple import.)
    df = load(con,
        "SELECT id, date, type, duration_minutes, distance_km, avg_hr, max_hr, "
        "calories, source, source_id FROM activities WHERE dup_of IS NULL "
        "AND duration_minutes > 0 AND source != 'apple'")
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df


def zone_minutes(con: sqlite3.Connection, acts: pd.DataFrame) -> pd.DataFrame:
    """Per-activity minutes in each HR zone, from activity_streams.

    Stream offsets are irregular seconds; we sum dt between consecutive samples,
    capping each dt at STREAM_GAP_CAP_S so dropouts don't inflate a zone.
    activity_streams keys on the source_id string, not the activities PK.
    """
    sid_to_date = {str(r["source_id"]): r["date"] for _, r in acts.iterrows()
                   if pd.notna(r["source_id"])}
    streams = load(con,
        "SELECT activity_id, timestamp_offset, bpm FROM activity_streams "
        "WHERE bpm IS NOT NULL ORDER BY activity_id, timestamp_offset")
    out = []
    if streams.empty:
        return pd.DataFrame(columns=["date", "Z1", "Z2", "Z3", "Z4", "Z5"])
    for aid, grp in streams.groupby("activity_id"):
        if str(aid) not in sid_to_date:
            continue
        offs = grp["timestamp_offset"].to_numpy()
        bpms = grp["bpm"].to_numpy()
        secs = {z: 0.0 for z in ZONE_PCT}
        for i in range(len(offs)):
            dt = (offs[i + 1] - offs[i]) if i + 1 < len(offs) else 1
            dt = min(max(dt, 0), STREAM_GAP_CAP_S)
            secs[zone_of(bpms[i])] += dt
        row = {"date": sid_to_date[str(aid)]}
        row.update({z: secs[z] / 60.0 for z in ZONE_PCT})
        out.append(row)
    return pd.DataFrame(out)


def vo2max(con: sqlite3.Connection, acon=None) -> pd.DataFrame:
    """VO2max series: Garmin backfill side table + Apple full-export values.
    One value per day, Garmin preferred."""
    frames = []
    try:
        g = load(con, "SELECT date, vo2max FROM vo2max WHERE vo2max IS NOT NULL")
        g["rank"] = 0
        frames.append(g)
    except Exception:
        pass
    if acon is not None:
        a = pd.read_sql_query(
            "SELECT date, CAST(value AS REAL) vo2max FROM misc WHERE kind='vo2max'", acon)
        a["rank"] = 1
        frames.append(a)
    if not frames:
        return pd.DataFrame(columns=["date", "vo2max"])
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return pd.DataFrame(columns=["date", "vo2max"])
    df = df.sort_values("rank").drop_duplicates("date", keep="first").drop(columns=["rank"])
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")


def apple_workouts(acon, acts: pd.DataFrame):
    """Apple-export workouts + their HR-zone minutes, EXCLUDING workouts that
    look like duplicates of an already-imported activity (same date, duration
    within 35%) — both devices recording the same session would double-count.

    Returns (day_act_apple, zday_apple) shaped like build_daily_frame's
    day_act / zday inputs.
    """
    empty = (pd.DataFrame(columns=["date", "exercise_min", "peak_hr", "n_act", "distance_km"]),
             pd.DataFrame(columns=["date", "Z1", "Z2", "Z3", "Z4", "Z5"]))
    if acon is None:
        return empty
    aw = pd.read_sql_query(
        "SELECT w.start_ts, w.date, w.type, w.duration_min, "
        "z.z1, z.z2, z.z3, z.z4, z.z5 FROM workouts w "
        "LEFT JOIN workout_zones z ON z.start_ts = w.start_ts "
        "WHERE w.duration_min > 0", acon)
    if aw.empty:
        return empty
    # date -> list of existing (non-apple) activity durations
    existing = defaultdict(list)
    for _, r in acts.iterrows():
        existing[r["date"].date().isoformat()].append(r["duration_minutes"])

    def is_dup(r):
        for dur in existing.get(r["date"], []):
            if abs(r["duration_min"] - dur) <= 0.35 * max(r["duration_min"], dur):
                return True
        return False

    aw = aw[~aw.apply(is_dup, axis=1)]
    if aw.empty:
        return empty
    aw["date"] = pd.to_datetime(aw["date"])
    day_act = aw.groupby("date").agg(
        exercise_min=("duration_min", "sum"),
        n_act=("start_ts", "count")).reset_index()
    day_act["peak_hr"] = None
    day_act["distance_km"] = None
    z = aw[["date", "z1", "z2", "z3", "z4", "z5"]].fillna(0)
    z.columns = ["date", "Z1", "Z2", "Z3", "Z4", "Z5"]
    zday = z.groupby("date")[["Z1", "Z2", "Z3", "Z4", "Z5"]].sum().reset_index()
    return day_act, zday


def apple_daily_misc(acon) -> pd.DataFrame:
    """Daily steps (complete 2016+), Apple exercise minutes (2022-11+),
    sparse weight and HR-recovery points."""
    if acon is None:
        return pd.DataFrame(columns=["date", "steps_apple", "apple_exercise_min",
                                     "weight", "hr_recovery"])
    steps = pd.read_sql_query("SELECT date, steps AS steps_apple FROM daily_steps", acon)
    summ = pd.read_sql_query(
        "SELECT date, exercise_min AS apple_exercise_min FROM daily_summary", acon)
    wt = pd.read_sql_query(
        "SELECT date, CAST(value AS REAL) weight FROM misc WHERE kind='weight_kg'", acon)
    hrr = pd.read_sql_query(
        "SELECT date, CAST(value AS REAL) hr_recovery FROM misc WHERE kind='hr_recovery'", acon)
    df = steps
    for f in (summ, wt, hrr):
        df = df.merge(f, on="date", how="outer")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")


def wellness(con: sqlite3.Connection) -> pd.DataFrame:
    df = load(con,
        "SELECT date, hrv, avg_sleeping_hr, steps, spo2, source FROM wellness")
    if df.empty:
        return df
    # one row/day, prefer garmin then apple then suunto, field-wise coalesce
    df["date"] = pd.to_datetime(df["date"])
    pref = {"garmin": 0, "apple": 1, "suunto": 2}
    df["rank"] = df["source"].map(pref).fillna(9)
    df = df.sort_values("rank")
    agg = df.groupby("date").agg(
        hrv=("hrv", "first"), overnight_hr=("avg_sleeping_hr", "first"),
        steps=("steps", "first"), spo2=("spo2", "first")).reset_index()
    return agg.sort_values("date")


# --- Aggregation -----------------------------------------------------------

def build_daily_frame(con):
    acon = apple_con()
    rhr = daily_resting_hr(con, acon).rename(columns={"rhr": "resting_hr", "source": "rhr_src"})
    slp = daily_sleep(con, acon)[["date", "sleep_hours", "deep_minutes", "rem_minutes", "source"]] \
        .rename(columns={"source": "sleep_src"})
    well = wellness(con)
    vo2 = vo2max(con, acon)
    acts = activities(con)
    zm = zone_minutes(con, acts)
    amisc = apple_daily_misc(acon)

    # exercise (structured-workout) minutes per day + peak HR + training hrs + type
    if not acts.empty:
        day_act = acts.groupby("date").agg(
            exercise_min=("duration_minutes", "sum"),
            peak_hr=("max_hr", "max"),
            n_act=("id", "count"),
            distance_km=("distance_km", "sum"),
        ).reset_index()
    else:
        day_act = pd.DataFrame(columns=["date", "exercise_min", "peak_hr", "n_act", "distance_km"])
    if not zm.empty:
        zday = zm.groupby("date")[["Z1", "Z2", "Z3", "Z4", "Z5"]].sum().reset_index()
    else:
        zday = pd.DataFrame(columns=["date", "Z1", "Z2", "Z3", "Z4", "Z5"])

    # fold in Apple-export workouts that aren't device-activity duplicates
    a_day, a_zday = apple_workouts(acon, acts)
    if not a_day.empty:
        day_act = (pd.concat([day_act, a_day], ignore_index=True)
                   .groupby("date").agg(exercise_min=("exercise_min", "sum"),
                                        peak_hr=("peak_hr", "max"),
                                        n_act=("n_act", "sum"),
                                        distance_km=("distance_km", "sum")).reset_index())
    if not a_zday.empty:
        zday = (pd.concat([zday, a_zday], ignore_index=True)
                .groupby("date")[["Z1", "Z2", "Z3", "Z4", "Z5"]].sum().reset_index())

    # full daily calendar from earliest data to latest
    frames = [f for f in [rhr, slp, well, vo2, day_act, zday, amisc] if not f.empty]
    if not frames:
        raise SystemExit("No data in DB.")
    lo = min(f["date"].min() for f in frames)
    hi = max(f["date"].max() for f in frames)
    cal = pd.DataFrame({"date": pd.date_range(lo, hi, freq="D")})
    for f in [rhr, slp, well, vo2, day_act, zday, amisc]:
        if not f.empty:
            cal = cal.merge(f, on="date", how="left")
    for z in ZONE_PCT:
        if z not in cal:
            cal[z] = 0.0
    cal[list(ZONE_PCT)] = cal[list(ZONE_PCT)].fillna(0)
    # steps: device wellness value where present, Apple-export daily total otherwise
    if "steps_apple" in cal:
        if "steps" in cal:
            cal["steps"] = cal["steps"].fillna(cal["steps_apple"])
        else:
            cal["steps"] = cal["steps_apple"]
    cal["vigorous_min"] = cal["Z4"] + cal["Z5"]
    cal["mod_vig_min"] = cal["Z3"] + cal["Z4"] + cal["Z5"]
    if acon is not None:
        acon.close()
    return cal


def summarize(cal: pd.DataFrame, by: str) -> pd.DataFrame:
    """Aggregate the daily calendar into quarter or week buckets."""
    c = cal.copy()
    if by == "quarter":
        c["bucket"] = c["date"].dt.to_period("Q").astype(str)
        c["bucket_start"] = c["date"].dt.to_period("Q").dt.start_time
    else:
        c["bucket_start"] = c["date"].apply(lambda d: week_start(d.date()))
        c["bucket"] = c["bucket_start"].astype(str)

    def agg_bucket(g: pd.DataFrame) -> pd.Series:
        weeks = max(g["date"].dt.to_period("W").nunique(), 1)
        ex_total = g["exercise_min"].fillna(0).sum()
        vig_total = g["vigorous_min"].fillna(0).sum()
        modvig_total = g["mod_vig_min"].fillna(0).sum()
        zsum = {z: g[z].fillna(0).sum() if z in g else 0 for z in ZONE_PCT}
        return pd.Series({
            "mod_vigorous_min_per_week": round(modvig_total / weeks, 1),
            "vigorous_min_per_week": round(vig_total / weeks, 1),
            "total_exercise_min_per_week": round(ex_total / weeks, 1),
            "resting_hr_bpm": round(g["resting_hr"].mean(), 1) if g["resting_hr"].notna().any() else None,
            "overnight_hr_bpm": round(g["overnight_hr"].mean(), 1) if "overnight_hr" in g and g["overnight_hr"].notna().any() else None,
            "sleep_hours": round(g["sleep_hours"].mean(), 2) if g["sleep_hours"].notna().any() else None,
            "deep_min_avg": round(g["deep_minutes"].mean(), 0) if "deep_minutes" in g and g["deep_minutes"].notna().any() else None,
            "rem_min_avg": round(g["rem_minutes"].mean(), 0) if "rem_minutes" in g and g["rem_minutes"].notna().any() else None,
            "hrv_ms": round(g["hrv"].mean(), 1) if "hrv" in g and g["hrv"].notna().any() else None,
            "vo2max": round(g["vo2max"].mean(), 1) if "vo2max" in g and g["vo2max"].notna().any() else None,
            "steps_per_day": round(g["steps"].mean(), 0) if "steps" in g and g["steps"].notna().any() else None,
            "peak_hr_max": int(g["peak_hr"].max()) if g["peak_hr"].notna().any() else None,
            "training_hours_per_week": round(ex_total / 60 / weeks, 2),
            "z1_min_per_week": round(zsum["Z1"] / weeks, 0),
            "z2_min_per_week": round(zsum["Z2"] / weeks, 0),
            "z3_min_per_week": round(zsum["Z3"] / weeks, 0),
            "z4_min_per_week": round(zsum["Z4"] / weeks, 0),
            "z5_min_per_week": round(zsum["Z5"] / weeks, 0),
            "days_with_any_data": int(g[["resting_hr", "sleep_hours", "exercise_min"]].notna().any(axis=1).sum()),
            # sparse Apple-export metrics (weight: 3 points; HR recovery: 2)
            "hr_recovery_bpm": round(g["hr_recovery"].mean(), 1) if "hr_recovery" in g and g["hr_recovery"].notna().any() else None,
            "weight_kg": round(g["weight"].mean(), 1) if "weight" in g and g["weight"].notna().any() else None,
            # Apple's own daily exercise-minutes metric (2022-11+), as corroboration
            "apple_exercise_min_per_week": round(g["apple_exercise_min"].mean() * 7, 0) if "apple_exercise_min" in g and g["apple_exercise_min"].notna().any() else None,
        })

    res = c.groupby(["bucket", "bucket_start"], as_index=False).apply(agg_bucket, include_groups=False)
    return res.sort_values("bucket_start").reset_index(drop=True)


def reindex_full(df: pd.DataFrame, by: str) -> pd.DataFrame:
    """Insert empty rows for missing quarters/weeks so the timeline + gaps are explicit."""
    today = date.today()
    if by == "quarter":
        idx = pd.period_range(HISTORY_START, today, freq="Q")
        full = pd.DataFrame({"bucket": idx.astype(str), "bucket_start": idx.start_time})
    else:
        starts = []
        d = week_start(WEEKLY_START)
        while d <= today:
            starts.append(d)
            d += timedelta(days=7)
        full = pd.DataFrame({"bucket": [str(s) for s in starts],
                             "bucket_start": pd.to_datetime(starts)})
    df = df.copy()
    df["bucket_start"] = pd.to_datetime(df["bucket_start"])
    merged = full.merge(df.drop(columns=["bucket"]), on="bucket_start", how="left")
    return merged.sort_values("bucket_start").reset_index(drop=True)


# --- Clinical (lipids / statin) --------------------------------------------

def lipids_df() -> pd.DataFrame:
    cols = ["date", "statin_dose_mg", "fasting", "total_chol", "trig", "hdl",
            "ldl", "apob", "lpa_nmol_l", "note"]
    df = pd.DataFrame(CD.LIPID_PANELS, columns=cols)
    df["date"] = pd.to_datetime(df["date"])
    return df


def statin_events_df() -> pd.DataFrame:
    df = pd.DataFrame(CD.STATIN_EVENTS, columns=["date", "dose_mg", "note"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def statin_dose_steps(end: pd.Timestamp):
    """Return (x, y) defining a step line of prescribed dose over time."""
    ev = statin_events_df().sort_values("date")
    xs, ys = [pd.Timestamp(CD.LIPID_PANELS[0][0])], [0]  # flat 0 before first event
    cur = 0
    for _, r in ev.iterrows():
        xs.append(r["date"]); ys.append(cur)      # hold previous level up to change
        xs.append(r["date"]); ys.append(r["dose_mg"])
        cur = r["dose_mg"]
    xs.append(end); ys.append(cur)
    return xs, ys


def risk_markers_df() -> pd.DataFrame:
    return pd.DataFrame(CD.RISK_MARKERS, columns=["date", "marker", "value", "note"])


# --- Report ----------------------------------------------------------------

def _dark(fig, height, title, ytitle, y2title=None):
    fig.update_layout(title=title, template="plotly_dark", height=height,
                      paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                      margin=dict(l=55, r=55, t=50, b=40),
                      legend=dict(orientation="h", y=-0.18))
    fig.update_yaxes(title_text=ytitle)
    return fig


# American Heart Association weekly aerobic-activity guidelines (min/week).
# https://www.heart.org/en/healthy-living/fitness/fitness-basics/aha-recs-for-physical-activity-in-adults
#   • >=150 min/wk moderate-intensity, OR >=75 min/wk vigorous, OR an equivalent combo
#   • >=300 min/wk for even greater benefit
# (date, label, color, which-charts-it-applies-to)
AHA_GUIDES = [
    (75,  "AHA min: 75 min/wk vigorous",      "#a78bfa", "vig"),
    (150, "AHA min: 150 min/wk moderate",     "#4ade80", "mod"),
    (300, "AHA goal: 300 min/wk (more benefit)", "#22d3ee", "mod"),
]


def add_aha_lines(fig, which=("vig", "mod"), row=None, col=None, position="top left"):
    """Draw the AHA weekly-minutes guideline lines on any min/week chart.

    `which` selects which thresholds apply: 'vig' (75), 'mod' (150 + 300 goal).
    Pass row/col for subplots; omit for a plain go.Figure.
    """
    for yv, lbl, color, applies in AHA_GUIDES:
        if applies not in which:
            continue
        kw = dict(y=yv, line_dash="dash", line_color=color, opacity=0.6,
                  annotation_text=lbl, annotation_position=position,
                  annotation_font_size=9, annotation_font_color=color)
        if row is not None:
            fig.add_hline(row=row, col=col, **kw)
        else:
            fig.add_hline(**kw)


def add_ldl_goal_lines(fig, row=None, col=None, secondary_y=False):
    """Dashed LDL target/stretch hlines, values from CD.CARDIO_GOALS (guarded —
    no clinical literals in committed code). No-op when CARDIO_GOALS is absent
    (an un-updated PHI file) or lacks an 'ldl' key."""
    goals = getattr(CD, "CARDIO_GOALS", None)
    if not goals or "ldl" not in goals:
        return
    ldl_goal = goals["ldl"]
    target, stretch = ldl_goal.get("target"), ldl_goal.get("stretch")
    kw = dict(line_dash="dash", opacity=0.6, annotation_font_size=9)
    if row is not None:
        kw.update(row=row, col=col)
    if secondary_y:
        kw["secondary_y"] = secondary_y
    if target is not None:
        fig.add_hline(y=target, line_color="#f87171",
                      annotation_text=f"LDL target {target}", annotation_position="bottom right",
                      annotation_font_color="#f87171", **kw)
    if stretch is not None:
        fig.add_hline(y=stretch, line_color="#4ade80",
                      annotation_text=f"LDL stretch {stretch}", annotation_position="bottom right",
                      annotation_font_color="#4ade80", **kw)


def lipid_overlay(weekly: pd.DataFrame, lip: pd.DataFrame, start, end, title, key="", aha=True):
    """Stacked 3-panel: lipids+statin dose / intensity minutes / resting HR.

    Shared x so a cardiologist can read LDL & ApoB against medication dose and
    against the activity + resting-HR trends underneath, on one time axis.
    aha=False suppresses the guideline lines (summary views keep them out as noise).
    """
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        row_heights=[0.46, 0.28, 0.26], specs=[[{"secondary_y": True}],
                        [{"secondary_y": False}], [{"secondary_y": False}]])
    lp = lip[(lip["date"] >= start) & (lip["date"] <= end)]
    w = weekly.copy()
    w["bucket_start"] = pd.to_datetime(w["bucket_start"])
    w = w[(w["bucket_start"] >= start) & (w["bucket_start"] <= end)]

    # Row 1: LDL + ApoB markers, target lines, statin dose step (secondary y)
    fig.add_trace(go.Scatter(x=lp["date"], y=lp["ldl"], name="LDL (calc)",
                  mode="lines+markers+text", text=lp["ldl"], textposition="top center",
                  line=dict(color="#f87171", width=2), marker=dict(size=9)), row=1, col=1)
    fig.add_trace(go.Scatter(x=lp["date"], y=lp["apob"], name="ApoB",
                  mode="lines+markers", line=dict(color="#fbbf24", width=2),
                  marker=dict(size=8)), row=1, col=1)
    sx, sy = statin_dose_steps(end)
    sx = [x for x in sx]
    fig.add_trace(go.Scatter(x=sx, y=sy, name="Rosuvastatin dose (mg)",
                  line=dict(color="#6ea8fe", width=1.5, shape="hv"), opacity=0.8,
                  fill="tozeroy", fillcolor="rgba(110,168,254,0.08)"),
                  row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="mg/dL", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="statin mg", row=1, col=1, secondary_y=True,
                     showgrid=False, range=[0, 45])
    add_ldl_goal_lines(fig, row=1, col=1)

    # Row 2: weekly intensity minutes
    if not w.empty:
        fig.add_trace(go.Bar(x=w["bucket_start"], y=w["mod_vigorous_min_per_week"],
                      name="Mod–vig min/wk (Z3–5)", marker_color="#6ea8fe"), row=2, col=1)
        fig.add_trace(go.Bar(x=w["bucket_start"], y=w["vigorous_min_per_week"],
                      name="Vigorous min/wk (Z4–5)", marker_color="#a78bfa"), row=2, col=1)
    if aha and not w.empty:
        # Inline (not add_aha_lines) so labels use explicit coords: plotly's
        # auto annotation positioning averages the datetime x-range, which fails
        # on pandas>=3 (sum of Timestamps starts at int 0). Numeric y + Timestamp
        # x via add_annotation avoids that path entirely.
        xleft = w["bucket_start"].min()
        for yv, lbl, color, _applies in AHA_GUIDES:
            fig.add_hline(y=yv, line_dash="dash", line_color=color, opacity=0.6, row=2, col=1)
            fig.add_annotation(row=2, col=1, x=xleft, y=yv, text=lbl, showarrow=False,
                               xanchor="left", yanchor="bottom",
                               font=dict(size=9, color=color))
    fig.update_yaxes(title_text="min/week", row=2, col=1)

    # Row 3: weekly resting HR
    if not w.empty:
        fig.add_trace(go.Scatter(x=w["bucket_start"], y=w["resting_hr_bpm"],
                      name="Resting HR", line=dict(color="#4ade80", width=2),
                      connectgaps=False), row=3, col=1)
    fig.update_yaxes(title_text="bpm", row=3, col=1)

    # statin-change vertical guides across all rows
    for _, r in statin_events_df().iterrows():
        if start <= r["date"] <= end:
            fig.add_vline(x=r["date"], line_dash="dash", line_color="#6ea8fe", opacity=0.35)
    # clinical events (surgery, CAC) — labelled, distinct color. Label added via
    # add_annotation (explicit coords) rather than the vline's annotation_text,
    # which would average the datetime x-range and fail on pandas>=3.
    for ev_date, label, _note in CD.EVENTS:
        ed = pd.Timestamp(ev_date)
        if start <= ed <= end:
            fig.add_vline(x=ed, line_dash="dot", line_color="#f0abfc", opacity=0.6)
            fig.add_annotation(xref="x", x=ed, yref="paper", y=1.0, text=label,
                               showarrow=False, yanchor="bottom",
                               font=dict(size=9, color="#f0abfc"))
    fig.update_layout(title=title, template="plotly_dark", height=620, barmode="overlay",
                      paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                      margin=dict(l=55, r=55, t=50, b=40),
                      legend=dict(orientation="h", y=-0.12))
    return fig


def dose_in_effect(ts) -> int:
    """Prescribed rosuvastatin dose (mg) in effect at a given timestamp."""
    cur = 0
    for d, mg, _ in CD.STATIN_EVENTS:
        if pd.Timestamp(d) <= ts:
            cur = mg
    return cur


def stat_cards_html(lip: pd.DataFrame) -> str:
    """At-a-glance stat cards: the six numbers a cardiologist reads first."""
    on = lip[lip["statin_dose_mg"] > 0]
    latest = lip.iloc[-1]
    nadir = on.loc[on["ldl"].idxmin()]

    def delta_tag(cur, ref):
        if pd.isna(cur) or pd.isna(ref) or int(cur - ref) == 0:
            return ""
        d = int(cur - ref)
        arrow, cls = ("▲", "up") if d > 0 else ("▼", "dn")
        return f' <span class="delta {cls}">{arrow}{abs(d)}</span>'

    cards = [
        ("LDL-C (mg/dL)",
         f'{int(latest["ldl"])}{delta_tag(latest["ldl"], nadir["ldl"])}',
         f'{latest["date"]:%Y-%m-%d} · nadir {int(nadir["ldl"])} ({nadir["date"]:%b %Y})',
         latest["ldl"] > 99),
        ("ApoB (mg/dL)",
         f'{int(latest["apob"])}{delta_tag(latest["apob"], nadir["apob"])}',
         f'{latest["date"]:%Y-%m-%d} · nadir {int(nadir["apob"])} ({nadir["date"]:%b %Y})',
         latest["apob"] >= 90),
        ("Lp(a)", "218.5",
         "nmol/L (2025-04) · ref &lt;75 · genetic, statin-independent", True),
        ("CAC score", "1.41",
         "Agatston (2024-04, age 32) · all LAD · premature for age", True),
        ("Therapy", f"rosuvastatin {dose_in_effect(pd.Timestamp(date.today()))} mg",
         "daily · started 2024-04 (5 mg) · at 40 mg since 2025-03", False),
        ("hs-CRP / HbA1c", "1.59 / 5.2%",
         "2026-05-28 · average-risk CRP band · normoglycemic", False),
    ]
    out = []
    for label, val, sub, flag in cards:
        cls = " hi" if flag else ""
        out.append(f'<div class=card><div class=cardlabel>{label}</div>'
                   f'<div class="cardval{cls}">{val}</div>'
                   f'<div class=cardsub>{sub}</div></div>')
    return f'<div class=cards>{"".join(out)}</div>'


def quarterly_table_html(q: pd.DataFrame, lip: pd.DataFrame) -> str:
    """The headline quarterly summary: statin dose + labs + lifestyle, one row
    per quarter. Lab values are the last draw inside each quarter."""
    def fmt(v, nd=0):
        if v is None or pd.isna(v):
            return "—"
        return f"{v:.{nd}f}" if nd else f"{int(round(v))}"

    heads = ["Quarter", "Statin", "LDL", "ApoB", "Mod–vig<br>min/wk", "Vigorous<br>min/wk",
             "Resting HR<br>bpm", "Sleep<br>h/night", "VO₂max", "Steps<br>/day"]
    thead = "".join(f"<th>{h}</th>" for h in heads)
    rows, prev_dose = [], None
    for _, r in q.iterrows():
        qs = pd.Timestamp(r["bucket_start"])
        qe = qs + pd.offsets.QuarterEnd(0)
        draws = lip[(lip["date"] >= qs) & (lip["date"] <= qe)]
        ldl = draws["ldl"].dropna().iloc[-1] if draws["ldl"].notna().any() else None
        apob = draws["apob"].dropna().iloc[-1] if draws["apob"].notna().any() else None
        dose = dose_in_effect(qe)
        dose_txt = f"{dose} mg" if dose else "—"
        if prev_dose is not None and dose != prev_dose:
            dose_txt = f"<b>{dose_txt} ↑</b>"   # dose changed inside this quarter
        prev_dose = dose
        m = [r.get(c) for c in ("mod_vigorous_min_per_week", "vigorous_min_per_week",
                                "resting_hr_bpm", "sleep_hours", "vo2max", "steps_per_day")]
        has_data = any(pd.notna(x) for x in m) or ldl is not None
        ldl_td = f"<td class=hi>{fmt(ldl)}</td>" if (ldl is not None and ldl > 99) else f"<td>{fmt(ldl)}</td>"
        apob_td = f"<td class=hi>{fmt(apob)}</td>" if (apob is not None and apob >= 90) else f"<td>{fmt(apob)}</td>"
        cells = (f"<td>{quarter_short(r['bucket'])}</td><td>{dose_txt}</td>{ldl_td}{apob_td}"
                 f"<td>{fmt(m[0])}</td><td>{fmt(m[1])}</td><td>{fmt(m[2])}</td>"
                 f"<td>{fmt(m[3], 1)}</td><td>{fmt(m[4], 1)}</td><td>{fmt(m[5])}</td>")
        rows.append(f'<tr{"" if has_data else " class=empty"}>{cells}</tr>')
    return f'<table class="lipid qtab"><thead><tr>{thead}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def life_events_html() -> str:
    """Psychosocial/occupational context as a reference table — kept off the lipid
    chart (no direct cholesterol correlation) but useful when reading the activity,
    resting-HR, and sleep trends."""
    body = "".join(
        f"<tr><td>{pd.Timestamp(d).strftime('%b %Y')}</td><td>{label}</td>"
        f"<td class=meta>{note}</td></tr>" for d, label, note in CD.LIFE_EVENTS)
    return (f'<table class=lipid><thead><tr><th>When</th><th>Event</th>'
            f'<th>Note</th></tr></thead><tbody>{body}</tbody></table>')


def quarter_short(bucket: str) -> str:
    """'2024Q1' -> \"24'Q1\" (the x-axis format used on quarterly charts)."""
    return f"{bucket[2:4]}'{bucket[-2:]}"


def exec_overlay(q: pd.DataFrame, lip: pd.DataFrame, end):
    """Executive chart: ONE panel. Quarterly exercise min/week as bars (left axis),
    labeled LDL/ApoB draws riding the same left axis (mg/dL and min/week share the
    0-200 numeric range), rosuvastatin dose step on the right axis."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    qq = q.copy()
    qq["label"] = qq["bucket"].map(quarter_short)
    order = list(qq["label"])

    # exercise bars — left axis
    fig.add_trace(go.Bar(x=qq["label"], y=qq["mod_vigorous_min_per_week"],
                  name="Mod–vig exercise min/wk (Z3–5)", marker_color="#6ea8fe",
                  opacity=0.55, width=0.72))
    fig.add_trace(go.Bar(x=qq["label"], y=qq["vigorous_min_per_week"],
                  name="Vigorous min/wk (Z4–5)", marker_color="#a78bfa",
                  opacity=0.9, width=0.45))

    # lipid draws — labeled points on the same left axis
    lipq = lip.copy()
    lipq["label"] = lipq["date"].dt.to_period("Q").astype(str).map(quarter_short)
    fig.add_trace(go.Scatter(x=lipq["label"], y=lipq["ldl"], name="LDL-C (mg/dL)",
                  mode="lines+markers+text", text=lipq["ldl"].astype(int),
                  textposition="top center", textfont=dict(size=11, color="#f87171"),
                  line=dict(color="#f87171", width=2.5), marker=dict(size=10)))
    apo = lipq[lipq["apob"].notna()]
    fig.add_trace(go.Scatter(x=apo["label"], y=apo["apob"], name="ApoB (mg/dL)",
                  mode="lines+markers+text", text=apo["apob"].astype(int),
                  textposition="bottom center", textfont=dict(size=10, color="#fbbf24"),
                  line=dict(color="#fbbf24", width=2, dash="dot"), marker=dict(size=8)))
    # statin dose — right axis, dose in effect at each quarter's end
    doses = [dose_in_effect(pd.Timestamp(bs) + pd.offsets.QuarterEnd(0))
             for bs in qq["bucket_start"]]
    fig.add_trace(go.Scatter(x=qq["label"], y=doses, name="Rosuvastatin (mg)",
                  line=dict(color="#38bdf8", width=2, shape="hv"), opacity=0.9,
                  fill="tozeroy", fillcolor="rgba(56,189,248,0.07)"),
                  secondary_y=True)

    # clinical events at their quarter (numeric category position — plotly's
    # vline annotations can't handle category-string x values)
    for ev_date, label, _note in CD.EVENTS:
        ql = quarter_short(str(pd.Timestamp(ev_date).to_period("Q")))
        if ql in order:
            fig.add_vline(x=order.index(ql), line_dash="dot", line_color="#f0abfc",
                          opacity=0.5, annotation_text=label, annotation_position="top",
                          annotation_font_size=9, annotation_font_color="#f0abfc")

    fig.update_yaxes(title_text="exercise min/week  ·  lipids mg/dL",
                     secondary_y=False, range=[0, 218])  # headroom for the 194 label
    fig.update_yaxes(title_text="rosuvastatin mg/day", secondary_y=True,
                     showgrid=False, range=[0, 45])
    add_ldl_goal_lines(fig)
    fig.update_xaxes(categoryorder="array", categoryarray=order, tickangle=-45)
    fig.update_layout(template="plotly_dark", height=520, barmode="overlay",
                      title="Quarterly exercise intensity × LDL/ApoB × statin dose",
                      paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                      margin=dict(l=55, r=55, t=50, b=40),
                      legend=dict(orientation="h", y=-0.22))
    return fig


def detail_figures(quarterly: pd.DataFrame, weekly: pd.DataFrame, end_ts):
    """Weekly/quarterly detail figures shown below the summary. Returns a list
    of (section_title, go.Figure). Shared by the HTML report (build_html) and
    the Streamlit cardiology dashboard page so the two never drift apart."""
    lip = lipids_df()

    def line(df, x, ycols, names, title, yaxis):
        fig = go.Figure()
        for y, nm in zip(ycols, names):
            if y in df and df[y].notna().any():
                fig.add_trace(go.Scatter(x=df[x], y=df[y], name=nm, mode="lines+markers",
                                         connectgaps=False))
        fig.update_layout(title=title, template="plotly_dark", height=340,
                          yaxis_title=yaxis, margin=dict(l=50, r=20, t=50, b=40),
                          paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                          legend=dict(orientation="h", y=-0.18))
        return fig

    detail = []
    detail.append(("Weekly detail — lipids × statin × activity (2024-04→now)",
                   lipid_overlay(weekly, lip, pd.Timestamp("2024-04-01"), end_ts,
                                 "Statin era: LDL/ApoB vs dose, weekly activity & resting HR")))

    w = weekly
    # weekly stacked zones
    zfig = go.Figure()
    for z, col in [("Z1", "z1_min_per_week"), ("Z2", "z2_min_per_week"), ("Z3", "z3_min_per_week"),
                   ("Z4", "z4_min_per_week"), ("Z5", "z5_min_per_week")]:
        if col in w:
            zfig.add_trace(go.Bar(x=w["bucket"], y=w[col], name=z))
    zfig.update_layout(barmode="stack", title="Weekly exercise minutes by HR zone (statin era)",
                       template="plotly_dark", height=360, paper_bgcolor="#0e1117",
                       plot_bgcolor="#0e1117", margin=dict(l=50, r=20, t=50, b=40),
                       legend=dict(orientation="h", y=-0.18), yaxis_title="min/week")
    # AHA moderate guideline (150 floor / 300 goal) against total weekly volume
    add_aha_lines(zfig, which=("mod",))
    detail.append(("Weekly — HR-zone minutes", zfig))
    detail.append(("Weekly — Resting & overnight HR",
                   line(w, "bucket", ["resting_hr_bpm", "overnight_hr_bpm"],
                        ["Resting HR", "Overnight HR"], "Weekly resting heart rate (statin era)", "bpm")))
    detail.append(("Weekly — Sleep duration",
                   line(w, "bucket", ["sleep_hours"], ["Sleep"], "Weekly sleep duration (statin era)", "hours")))

    q = quarterly.copy()
    q["bucket"] = q["bucket"].map(quarter_short)
    qint = line(q, "bucket", ["mod_vigorous_min_per_week", "vigorous_min_per_week"],
                ["Moderate–vigorous (Z3–Z5)", "Vigorous (Z4–Z5)"],
                "Avg weekly moderate/intense exercise minutes by quarter", "min/week")
    add_aha_lines(qint, which=("vig", "mod"))
    detail.append(("Quarterly — Exercise intensity (avg min/week)", qint))
    detail.append(("Quarterly — HRV & steps",
                   line(q, "bucket", ["hrv_ms", "steps_per_day"], ["HRV (ms)", "Steps/day"],
                        "HRV and daily steps by quarter", "value")))
    return detail


def build_html(quarterly: pd.DataFrame, weekly: pd.DataFrame, meta: dict) -> str:
    import plotly.io as pio
    lip = lipids_df()
    end_ts = pd.Timestamp(meta["data_max"])
    rendered = [0]  # plotly.js is embedded with the first figure only

    def render(fig):
        html = pio.to_html(fig, include_plotlyjs=(rendered[0] == 0), full_html=False)
        rendered[0] += 1
        return html

    # ===================== SUMMARY (first screen) =====================
    cards = stat_cards_html(lip)
    exec_html = render(exec_overlay(quarterly, lip, end_ts))
    qtable = quarterly_table_html(quarterly, lip)

    # ===================== DETAIL figures =====================
    detail = detail_figures(quarterly, weekly, end_ts)

    detail_parts = []
    for label, fig in detail:
        detail_parts.append(f"<h2>{label}</h2>")
        detail_parts.append(render(fig))

    # full lipid table (detail section)
    lip_disp = lip.copy()
    lip_disp["date"] = lip_disp["date"].dt.strftime("%Y-%m-%d")
    lip_disp["statin"] = lip_disp["statin_dose_mg"].apply(lambda d: f"{int(d)} mg" if d else "—")
    tcols = [("date", "Date"), ("statin", "Statin"), ("total_chol", "TC"), ("trig", "Trig"),
             ("hdl", "HDL"), ("ldl", "LDL"), ("apob", "ApoB"), ("lpa_nmol_l", "Lp(a)"), ("note", "Context")]
    thead = "".join(f"<th>{lbl}</th>" for _, lbl in tcols)
    trows = ""
    for _, r in lip_disp.iterrows():
        cells = ""
        for col, _ in tcols:
            v = r[col]
            v = "" if pd.isna(v) else (str(int(v)) if col in ("total_chol", "trig", "hdl", "ldl", "apob") and pd.notna(v) else v)
            flag = ""
            if col == "ldl" and r["ldl"] and r["ldl"] > 99: flag = " class=hi"
            if col == "apob" and pd.notna(r["apob"]) and r["apob"] >= 90: flag = " class=hi"
            cells += f"<td{flag}>{v}</td>"
        trows += f"<tr>{cells}</tr>"
    lip_table = f"<table class=lipid><thead><tr>{thead}</tr></thead><tbody>{trows}</tbody></table>"

    rm = risk_markers_df()
    rm_rows = "".join(f"<li><b>{r['marker']}</b> — {r['value']} <span class=meta>({r['date']}: {r['note']})</span></li>"
                      for _, r in rm.iterrows())
    life_table = life_events_html()

    pending = ", ".join(meta["pending"])
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>Cardiology visit — Ian Reed</title>
<style>body{{background:#0e1117;color:#e6e9ef;font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:24px}}
h1{{font-size:24px;margin-bottom:2px}} h2{{font-size:16px;margin-top:36px;border-bottom:1px solid #2a2f3a;padding-bottom:6px}}
h2.divider{{font-size:14px;letter-spacing:2px;text-transform:uppercase;color:#8b93a7;margin-top:56px;
border-bottom:2px solid #2a2f3a}}
.note{{background:#1a1f2b;border-left:3px solid #fbbf24;padding:12px 16px;border-radius:6px;font-size:14px;line-height:1.5}}
.summary{{background:#10261a;border-left:3px solid #4ade80;padding:12px 16px;border-radius:6px;font-size:14px;line-height:1.55}}
.qbox{{background:#1d1a2b;border-left:3px solid #a78bfa;padding:12px 16px;border-radius:6px;font-size:14px;line-height:1.55;margin:14px 0}}
.qbox ul{{margin:8px 0}}
.meta{{color:#8b93a7;font-size:12px}}
nav.toc{{position:sticky;top:0;background:#0e1117ee;padding:8px 0;font-size:13px;z-index:5;border-bottom:1px solid #2a2f3a}}
nav.toc a{{color:#6ea8fe;text-decoration:none;margin-right:4px}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0}}
.card{{background:#161b26;border:1px solid #2a2f3a;border-radius:8px;padding:10px 14px}}
.cardlabel{{font-size:11px;color:#8b93a7;text-transform:uppercase;letter-spacing:1px}}
.cardval{{font-size:24px;font-weight:650;margin:2px 0}} .cardval.hi{{color:#f87171}}
.cardsub{{font-size:11px;color:#8b93a7;line-height:1.4}}
.delta{{font-size:13px;font-weight:600}} .delta.up{{color:#f87171}} .delta.dn{{color:#4ade80}}
table.lipid{{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0}}
table.lipid th,table.lipid td{{border:1px solid #2a2f3a;padding:5px 8px;text-align:left}}
table.lipid th{{background:#1a1f2b}} table.lipid td.hi{{color:#f87171;font-weight:600}}
table.qtab td,table.qtab th{{text-align:right}} table.qtab td:first-child,table.qtab th:first-child{{text-align:left}}
table.qtab tr.empty td{{color:#4a5163}}
ul.rm{{font-size:13px;line-height:1.5}}</style></head><body>

<h1>Cardiology visit — data summary</h1>
<p class=meta>Ian Reed · M · DOB 1991-07-13 (34) · suspected FH · generated {meta['generated']} ·
activity data through {meta['data_max']}</p>
<nav class=toc><a href="#summary">Summary</a> · <a href="#qtable">Quarterly table</a> ·
<a href="#detail">Detailed charts</a> · <a href="#labs">Lab panels</a> ·
<a href="#life">Life context</a> · <a href="#methods">Methods &amp; caveats</a></nav>

<div class=summary id=summary><b>Clinical picture.</b> {CD.CLINICAL_SUMMARY}</div>
{cards}

<h2>Lipid response to therapy × exercise</h2>
<p class=meta>One timeline: quarterly exercise intensity (bars, left axis), labeled LDL-C/ApoB
draws (left axis, mg/dL), rosuvastatin dose step (right axis). Pink dotted verticals =
clinical events. Lipid points sit at the quarter of the draw.</p>
{exec_html}

<h2 id=qtable>Quarterly summary — therapy, labs &amp; lifestyle</h2>
<p class=meta>One row per quarter. Lab values are the last draw inside the quarter; red = above
target. <b>Bold dose</b> = changed during that quarter. Grayed rows = no wearable data yet
(Garmin backfill in progress will fill 2020→2025-Q1).</p>
{qtable}

<h2 class=divider id=detail>Detailed data</h2>
<p class=meta>Weekly-resolution overlays and per-metric trends. Dashed horizontal lines on
min/week charts mark AHA guidelines: <span style="color:#a78bfa">75 vigorous</span> /
<span style="color:#4ade80">150 moderate</span> minimum, <span style="color:#22d3ee">300
goal</span>.</p>
{''.join(detail_parts)}

<h2 id=labs>Complete lab panels by draw date</h2>
<p class=meta>Per-draw detail the quarterly table omits: TC, triglycerides, HDL, Lp(a),
fasting context, and exact dates.</p>
{lip_table}
<p class=meta>Values mg/dL except Lp(a) (nmol/L). Red = above LabCorp reference. LDL calculated (NIH).</p>
<h2>Other cardiovascular-risk markers</h2>
<ul class=rm>{rm_rows}</ul>

<h2 id=life>Life-context timeline</h2>
<p class=meta>Psychosocial / occupational events. Kept off the lipid chart (no direct
cholesterol link) but useful when reading the activity, resting-HR, and sleep trends —
e.g. the 2024 intense-work stretch and the 2026 surgery/job-exit period.</p>
{life_table}

<h2 id=methods>Methods &amp; caveats</h2>
<div class=note><b>Read before trusting numbers.</b><br>
• <b>Source coverage varies by era.</b> Apple full-export: steps complete 2016→now; official
  resting HR 2021-06+; workouts 2015+ but HR-zone minutes only 2021+ (no Apple HR samples
  2018-2020); real Apple sleep tracking only from mid-2024 (earlier "sleep" was a bedtime
  schedule and is excluded). Garmin API backfill fills 2020+ resting HR/sleep/HRV.<br>
• <b>Resting HR</b> uses Garmin's true daily value where present, then Apple's official
  RestingHeartRate (export), then a daily {int(RESTING_PCTL*100)}th-percentile of intraday samples
  (overnight-low proxy, dense-sampling days only).<br>
• <b>"Moderate–vigorous" / "vigorous" minutes</b> are HR-zone minutes (Z3–Z5 / Z4–Z5), %HRmax bands
  off an empirical peak of {HRMAX} bpm (age-predicted ~186), computed only for the
  {meta['acts_with_streams']} activities with HR streams. This is deliberately NOT raw workout
  duration — long easy hikes/skis (e.g. a 9.9 h hike at ~105 bpm) are training volume, not
  intensity. Total volume is in the CSV separately.<br>
• <b>Activity vs lipids alignment.</b> Lipid draws run 2020→2026 but quantified activity only exists from
  {meta['data_min']}. So the 2020–2024 statin response has NO activity overlay yet, and only the
  2025-04→now draws (the nadir and the regression) can be read against exercise/resting-HR.<br>
• <b>AHA activity guideline lines</b> (detailed charts only) mark the American Heart Association
  targets: <span style="color:#a78bfa">75 min/wk vigorous</span> (Z4–Z5) or
  <span style="color:#4ade80">150 min/wk moderate</span> minimum, and the
  <span style="color:#22d3ee">300 min/wk</span> "even greater benefit" goal. AHA also recommends
  muscle-strengthening on ≥2 days/wk — <i>not</i> captured here (no strength-session intensity data).<br>
• <b>Pending device exports</b> (currently empty columns): {pending}.</div>
</body></html>"""


def write_readme(quarterly, weekly, meta):
    path = os.path.join(OUT, "README.md")
    txt = f"""# Cardiology dataset — data dictionary & provenance

Generated {meta['generated']}. Data present from **{meta['data_min']} to {meta['data_max']}**.
Source DB: `health-dashboard/data/health.db` (Apple Health + Garmin + Strava + Suunto).

This folder is meant to be handed to a separate Claude session that holds the
cardiology context. Read this file first.

## Clinical picture
{CD.CLINICAL_SUMMARY}

## Files
- `lipids.csv` — LabCorp lipid/apolipoprotein series 2020–2026 with the rosuvastatin
  dose in effect at each draw. The clinical spine of this dataset.
- `statin_events.csv` — rosuvastatin start + dose-change dates.
- `risk_markers.csv` — standalone CV-risk markers (CAC score, Lp(a), hs-CRP, A1c,
  statin-safety LFTs, eGFR, suspected FH).
- `quarterly.csv` — one row per calendar quarter from 2020-Q1 to the current quarter.
  Rows before {meta['data_min']} are intentionally present but empty (explicit gaps).
- `weekly.csv` — one row per week (Mon-start) from {WEEKLY_START} to latest. This is the
  detailed "statin era" activity timeline that overlays the lipid draws.
- `cardiology_report.html` — visual report (open in a browser).

## Critical caveats
1. **History gap.** Nothing in the source DB predates {meta['data_min']}. The quarterly
   look-back to 2020 and the priority April-2024 start are EMPTY until older device
   exports are imported. Do not read empty quarters as zero activity.
2. **Resting HR provenance.** `resting_hr_bpm` = Garmin's true daily resting where
   available; otherwise the daily {int(RESTING_PCTL*100)}th-percentile of Apple intraday
   samples (overnight-low proxy). It is NOT Apple's official RestingHeartRate metric yet.
3. **HR zones** are %HRmax bands (Z1 50–60 / Z2 60–70 / Z3 70–80 / Z4 80–90 / Z5 ≥90)
   off an empirical HRmax of {HRMAX} bpm, and exist only for activities with HR streams
   ({meta['acts_with_streams']} of {meta['n_activities']} activities).
4. **Exercise intensity is zone-based, not duration.** `mod_vigorous_min_per_week` = Z3–Z5
   stream minutes; `vigorous_min_per_week` = Z4–Z5. Raw workout duration
   (`total_exercise_min_per_week`) is kept separately as training VOLUME — it is dominated
   by long easy hikes/skis (a 9.9 h hike at ~105 bpm is volume, not intensity).
5. **Activity↔lipid alignment.** Lipid draws span 2020→2026 but quantified activity begins
   only {meta['data_min']}. Only the 2025-11 nadir and 2026-05 regression draws have an
   activity/resting-HR overlay; the 2020–2024 statin response does not (yet).

## Pending device-export columns (currently empty)
{chr(10).join('- ' + p for p in meta['pending'])}

## Column reference (quarterly.csv)
{', '.join(quarterly.columns)}

## Column reference (weekly.csv)
{', '.join(weekly.columns)}
"""
    with open(path, "w") as fh:
        fh.write(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "health_snapshot.db"))
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    con = sqlite3.connect(args.db)
    cal = build_daily_frame(con)

    q = reindex_full(summarize(cal, "quarter"), "quarter")
    w_all = summarize(cal, "week")
    w = reindex_full(w_all, "week")
    w = w[pd.to_datetime(w["bucket_start"]) >= pd.Timestamp(WEEKLY_START)].reset_index(drop=True)

    n_acts = load(con, "SELECT COUNT(*) n FROM activities WHERE dup_of IS NULL").iloc[0]["n"]
    n_streams = load(con, "SELECT COUNT(DISTINCT activity_id) n FROM activity_streams").iloc[0]["n"]
    data_min = cal["date"].min().date().isoformat()
    data_max = cal["date"].max().date().isoformat()
    con.close()

    meta = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data_min": data_min, "data_max": data_max,
        "n_activities": int(n_acts), "acts_with_streams": int(n_streams),
        "pending": ["weight_kg (only 3 entries, 2022-2023)",
                    "hr_recovery_bpm (only 2 points, 2024-10 & 2025-03)"],
    }

    q.to_csv(os.path.join(OUT, "quarterly.csv"), index=False)
    w.to_csv(os.path.join(OUT, "weekly.csv"), index=False)
    lipids_df().to_csv(os.path.join(OUT, "lipids.csv"), index=False)
    statin_events_df().to_csv(os.path.join(OUT, "statin_events.csv"), index=False)
    risk_markers_df().to_csv(os.path.join(OUT, "risk_markers.csv"), index=False)
    with open(os.path.join(OUT, "cardiology_report.html"), "w") as fh:
        fh.write(build_html(q, w, meta))
    write_readme(q, w, meta)

    print(f"data {data_min}..{data_max}  |  {int(n_acts)} activities, {int(n_streams)} with streams")
    print(f"quarterly rows: {len(q)}  ({q['resting_hr_bpm'].notna().sum()} with resting HR)")
    print(f"weekly rows: {len(w)}  ({w['mod_vigorous_min_per_week'].notna().sum()} with intensity data)")
    print("wrote: quarterly.csv, weekly.csv, lipids.csv, statin_events.csv, risk_markers.csv,")
    print("       cardiology_report.html, README.md -> cardiology/out/")


if __name__ == "__main__":
    main()
