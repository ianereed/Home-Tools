"""The shared metrics layer: one definition per number, so tabs can't disagree."""
import sqlite3
from datetime import date, datetime

import pandas as pd
import pytest
import streamlit as st

from dashboard import metrics as M
from dashboard import targets as T

TODAY = date(2026, 10, 21)   # a Wednesday


@pytest.fixture
def conn(empty_db):
    st.cache_data.clear()
    c = sqlite3.connect(empty_db)
    yield c
    c.close()


def _rows(conn, sql, rows):
    conn.executemany(sql, rows)
    conn.commit()
    st.cache_data.clear()


class TestWeight:
    def test_seven_day_mean_with_latest_as_subline(self, conn):
        _rows(conn, "INSERT INTO body_weight (timestamp, weight_kg, source) VALUES (?,?,'garmin')",
              [("2026-10-19T07:00:00", 80.0), ("2026-10-18T07:00:00", 82.0), ("2026-10-01T07:00:00", 90.0)])
        w = M.weight_now(TODAY)
        assert w["basis"] == "7-day mean" and not w["stale"]
        assert w["lb"] == pytest.approx(81.0 * T.KG_TO_LB)
        assert w["latest_lb"] == pytest.approx(80.0 * T.KG_TO_LB) and w["age_days"] == 2

    def test_low_is_the_lowest_single_reading_in_seven_days(self, conn):
        # two readings on the 19th (morning low, evening high): the mean uses the
        # day's average, the low uses the single lowest reading
        _rows(conn, "INSERT INTO body_weight (timestamp, weight_kg, source) VALUES (?,?,'garmin')",
              [("2026-10-19T07:00:00", 79.0), ("2026-10-19T21:00:00", 81.0),
               ("2026-10-17T07:00:00", 80.5), ("2026-10-10T07:00:00", 70.0)])   # 11 days ago: outside
        w = M.weight_now(TODAY)
        assert w["lb"] == pytest.approx((80.0 + 80.5) / 2 * T.KG_TO_LB)
        assert w["low_lb"] == pytest.approx(79.0 * T.KG_TO_LB)
        assert w["low_date"] == pd.Timestamp("2026-10-19")

    def test_weekly_mean_low_and_gap_weeks(self, conn):
        _rows(conn, "INSERT INTO body_weight (timestamp, weight_kg, source) VALUES (?,?,'garmin')",
              [("2026-10-05T07:00:00", 80.0), ("2026-10-07T07:00:00", 82.0),   # week of Oct 5
               ("2026-10-19T07:00:00", 79.0)])                                  # week of Oct 19
        w = M.weight_weekly()
        assert w["week"].dt.strftime("%m-%d").tolist() == ["10-05", "10-12", "10-19"]
        assert w["lb"].iloc[0] == pytest.approx(81.0 * T.KG_TO_LB)
        assert w["low"].iloc[0] == pytest.approx(80.0 * T.KG_TO_LB)
        assert pd.isna(w["lb"].iloc[1])            # no weigh-ins: a gap, not zero
        assert w["n"].iloc[2] == 1

    def test_rolling_low_is_a_seven_calendar_day_minimum(self):
        df = pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-10-03", "2026-10-09", "2026-10-30"]),
                           "low": [80.0, 79.0, 81.0, 78.0]})
        out = M.rolling_low(df, "low").tolist()
        assert out[:3] == [80.0, 79.0, 79.0]      # Oct 3 still inside Oct 9's 7-day window
        assert pd.isna(out[3])                     # a 21-day gap breaks the line

    def test_falls_back_to_latest_and_flags_stale(self, conn):
        _rows(conn, "INSERT INTO body_weight (timestamp, weight_kg, source) VALUES (?,?,'garmin')",
              [("2026-08-11T07:00:00", 85.0)])
        w = M.weight_now(TODAY)
        assert w["stale"] and w["basis"] == "latest" and w["age_days"] == 71
        assert w["low_lb"] == pytest.approx(85.0 * T.KG_TO_LB)


class TestBloodPressure:
    def test_window_by_timestamp_with_category_of_the_mean(self, conn):
        _rows(conn, "INSERT INTO blood_pressure (timestamp, systolic, diastolic, source) VALUES (?,?,?,'garmin')",
              [("2026-10-20T07:00:00", 118, 76), ("2026-10-10T07:00:00", 122, 78),
               ("2026-10-06T06:00:00", 150, 95)])   # 15 days before now: outside
        w = M.bp_window(14, now=datetime(2026, 10, 21, 8, 0))
        assert (w["sys"], w["dia"], w["n"]) == (120, 77, 2)
        assert w["category"] == "Elevated" and w["basis"] == "14-day mean"

    def test_falls_back_to_latest_reading(self, conn):
        _rows(conn, "INSERT INTO blood_pressure (timestamp, systolic, diastolic, source) VALUES (?,?,?,'garmin')",
              [("2026-09-01T07:00:00", 112, 70)])
        w = M.bp_window(14, now=datetime(2026, 10, 21))
        assert w["n"] == 1 and w["basis"].startswith("latest") and w["category"] == "Normal"


class TestAlcohol:
    def test_this_week_is_the_calendar_week_not_the_last_drinking_week(self, conn):
        _rows(conn, "INSERT INTO lifestyle_log (date, name, subtype, amount, source) VALUES (?,'Alcohol','beer',?,'garmin')",
              [("2026-10-14", 3), ("2026-09-30", 4)])
        a = M.alcohol_summary(TODAY)
        assert a["this_week"] == 0
        # prior 4 weeks: Sep 21 (0), Sep 28 (4), Oct 5 (0), Oct 12 (3) -> dry weeks count as zero
        assert a["prior4_avg"] == pytest.approx(7 / 4)


class TestActivities:
    def test_impossible_distance_left_out_of_totals(self, conn):
        _rows(conn, "INSERT INTO activities (source_id, date, type, duration_minutes, distance_km, source) "
                    "VALUES (?,?,?,?,?,'garmin')",
              [("a1", "2026-10-03", "para_sports", 0, 1287.5),
               ("a2", "2026-10-04", "road_biking", 60, 30.0),
               ("a3", "2026-10-05", "walking", 30, 300.0)])     # 600 km/h
        acts = M.activities("2026-10-01")
        assert M.distance_km(acts) == 30.0
        assert len(acts) == 3     # still listed in the log

    def test_zone_boundaries(self):
        z = M.zone_of([T.HRMAX * 0.3, T.HRMAX * 0.65, T.HRMAX * 0.71, T.HRMAX * 0.95]).astype(str).tolist()
        assert z == ["Z1", "Z2", "Z3", "Z5"]


class TestSleep:
    def test_garmin_first_and_artifacts_dropped(self, conn):
        _rows(conn, "INSERT INTO sleep (date, total_minutes, deep_minutes, rem_minutes, light_minutes, "
                    "awake_minutes, source) VALUES (?,?,?,?,?,?,?)",
              [("2026-10-19", 480, 60, 90, 320, 10, "garmin"), ("2026-10-19", 500, 60, 90, 340, 10, "apple"),
               ("2026-10-18", 984, 984, 0, 0, 0, "garmin"), ("2026-10-17", 420, 60, 90, 260, 10, "apple")])
        s = M.sleep_nightly()
        assert s["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-10-17", "2026-10-19"]
        assert s["hours"].tolist() == [7.0, 8.0]


def test_avg_window_is_inclusive_seven_days():
    df = pd.DataFrame({"date": pd.date_range("2026-10-08", "2026-10-21"), "v": range(14)})
    assert M.avg_window(df, "v", TODAY) == pytest.approx(sum(range(7, 14)) / 7)


def test_fiber_target_scales_with_calories_with_a_floor():
    assert T.fiber_target_g(1200) == T.FIBER_MIN_G
    assert T.fiber_target_g(3000) == pytest.approx(42)


def test_physician_goal_helpers_default_safely():
    assert T.bp_goal(None) == T.BP_NORMAL
    assert T.bp_goal({"bp": {"systolic": 130, "diastolic": 85}}) == (130, 85)
    assert T.weight_band_lb(None) is None
    assert T.ldl_goal({"ldl": {"target": 70, "stretch": 50}}) == 50


def test_training_load_is_seeded_from_earlier_history(fake_db):
    from recovery import engine
    import collectors.db as db
    c = db.get_connection()
    try:
        seeded = engine.calculate_ctl_atl_tsb(c, days=20)
        cold = engine.calculate_ctl_atl_tsb(c, days=20, warmup_days=0)
    finally:
        c.close()
    assert len(seeded) == 21
    # a cold start reads fitness low; warm-up history lifts the first day
    assert seeded[0]["ctl"] > cold[0]["ctl"]
