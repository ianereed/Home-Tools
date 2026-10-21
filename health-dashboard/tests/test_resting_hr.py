"""Resting HR: intraday Apple samples never count as resting, every reader
gets one source-preferred value per day, and the one-shot migration retags
the intraday rows the old receiver stored as 'resting'."""
import sqlite3

import pytest

import collectors.db as db
from collectors.apple_health_server import HealthDataHandler
from recovery import engine
from recovery.resting_hr import daily_resting_hr


@pytest.fixture
def conn(empty_db):
    c = sqlite3.connect(empty_db)
    yield c
    c.close()


def _hr(conn, ts, bpm, context, source):
    conn.execute("INSERT INTO heart_rate (timestamp, bpm, context, source) VALUES (?,?,?,?)",
                 (ts, bpm, context, source))
    conn.commit()


def _contexts(conn):
    return dict(conn.execute("SELECT timestamp, context FROM heart_rate WHERE source='apple'"))


class TestDailyRestingHr:
    def test_garmin_beats_apple_on_the_same_day(self, conn):
        _hr(conn, "2026-10-17T00:00:00", 54, "resting", "garmin")
        _hr(conn, "2026-10-17T00:00:00", 61, "resting", "apple")
        assert daily_resting_hr(conn) == {"2026-10-17": 54}

    def test_falls_back_to_apple_when_garmin_is_missing(self, conn):
        _hr(conn, "2026-10-18T00:00:00", 61, "resting", "apple")
        _hr(conn, "2026-10-17T00:00:00", 54, "resting", "garmin")
        assert daily_resting_hr(conn) == {"2026-10-17": 54, "2026-10-18": 61}

    def test_intraday_samples_never_count(self, conn):
        _hr(conn, "2026-10-17T00:00:00", 54, "resting", "garmin")
        _hr(conn, "2026-10-17T14:02:00", 170, "intraday", "apple")
        _hr(conn, "2026-10-18T14:02:00", 170, "intraday", "apple")
        assert daily_resting_hr(conn) == {"2026-10-17": 54}

    def test_since_bounds_the_range(self, conn):
        _hr(conn, "2026-10-16T00:00:00", 58, "resting", "garmin")
        _hr(conn, "2026-10-17T00:00:00", 54, "resting", "garmin")
        assert daily_resting_hr(conn, "2026-10-17") == {"2026-10-17": 54}

    def test_engine_resting_hr_ignores_ride_samples(self, conn, monkeypatch):
        today = engine.date.today().isoformat()
        _hr(conn, f"{today}T00:00:00", 55, "resting", "garmin")
        _hr(conn, f"{today}T10:00:00", 175, "intraday", "apple")
        assert engine.get_resting_hr(conn) == 55


class TestReceiver:
    @staticmethod
    def _post(metrics):
        HealthDataHandler._process_health_data(None, {"data": {"metrics": metrics}})

    def test_heart_rate_samples_are_intraday(self, conn):
        self._post([{"name": "heart_rate", "data": [
            {"date": "2026-10-18 00:02:00 -0700", "Avg": 80}]}])
        assert _contexts(conn) == {"2026-10-18T00:02:00": "intraday"}

    def test_resting_value_wins_the_shared_midnight_slot(self, conn):
        # intraday first, then the daily resting value at the same timestamp
        self._post([{"name": "heart_rate", "data": [{"date": "2026-10-18 00:00:00 -0700", "Avg": 80}]},
                    {"name": "resting_heart_rate", "data": [{"date": "2026-10-18 00:00:00 -0700", "qty": 59}]}])
        # and a later re-post of intraday data must not clobber it
        self._post([{"name": "heart_rate", "data": [{"date": "2026-10-18 00:00:00 -0700", "Avg": 80}]}])
        assert conn.execute("SELECT bpm, context FROM heart_rate").fetchall() == [(59, "resting")]


class TestMigration:
    def test_retags_non_midnight_apple_rows_once(self, conn):
        conn.execute("PRAGMA user_version = 0")
        _hr(conn, "2026-10-18T00:00:00", 59, "resting", "apple")    # daily value
        _hr(conn, "2026-10-18T00:02:00", 80, "resting", "apple")    # old intraday bug
        _hr(conn, "2026-10-18T00:00:00", 59, "resting", "garmin")
        db.init_db()
        assert _contexts(conn) == {"2026-10-18T00:00:00": "resting",
                                   "2026-10-18T00:02:00": "intraday"}
        assert conn.execute("SELECT context FROM heart_rate WHERE source='garmin'").fetchone() == ("resting",)

        # one-shot: a later init_db leaves rows written after the migration alone
        _hr(conn, "2026-10-19T09:15:00", 62, "resting", "apple")
        db.init_db()
        assert _contexts(conn)["2026-10-19T09:15:00"] == "resting"
