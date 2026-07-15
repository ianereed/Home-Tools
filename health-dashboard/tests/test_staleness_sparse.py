"""Armed / stale / dormant matrix for the sparse-metric staleness model
(collectors.staleness_check) added in Phase 2.

Uses a fresh empty-schema tmp-path DB (not the `fake_db` fixture's seeded ~90
days) so each test controls exactly what blood_pressure/body_weight rows
exist and their timestamps relative to an injected `now` clock.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

import collectors.db as db
import collectors.staleness_check as staleness_check
from collectors.staleness_check import (
    BP_DORMANT_DAYS,
    BP_STALE_DAYS,
    WEIGHT_DORMANT_DAYS,
    WEIGHT_STALE_DAYS,
    _sparse_metric_alert,
    check_staleness,
)

NOW = datetime(2026, 7, 15, 8, 0, 0)


@pytest.fixture
def cardio_db(tmp_path, monkeypatch):
    db_path = tmp_path / "health.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    monkeypatch.setattr(staleness_check, "DB_PATH", str(db_path))
    db.init_db()
    conn = sqlite3.connect(str(db_path))
    yield conn
    conn.close()


def _insert_bp(conn, ts):
    conn.execute(
        "INSERT INTO blood_pressure (timestamp, systolic, diastolic, source) "
        "VALUES (?, 118, 76, 'garmin')",
        (ts,),
    )
    conn.commit()


def _insert_weight(conn, ts, source):
    conn.execute(
        "INSERT INTO body_weight (timestamp, weight_kg, source) VALUES (?, 80.0, ?)",
        (ts, source),
    )
    conn.commit()


def _days_ago(n):
    return (NOW - timedelta(days=n)).isoformat()


class TestBloodPressure:
    def test_unarmed_when_never_recorded(self, cardio_db):
        assert _sparse_metric_alert(
            cardio_db, "blood_pressure", None, BP_STALE_DAYS, BP_DORMANT_DAYS, "Blood pressure", NOW
        ) is None

    def test_fresh_no_alert(self, cardio_db):
        _insert_bp(cardio_db, _days_ago(2))
        assert _sparse_metric_alert(
            cardio_db, "blood_pressure", None, BP_STALE_DAYS, BP_DORMANT_DAYS, "Blood pressure", NOW
        ) is None

    def test_stale_window_alerts(self, cardio_db):
        _insert_bp(cardio_db, _days_ago(BP_STALE_DAYS + 1))
        alert = _sparse_metric_alert(
            cardio_db, "blood_pressure", None, BP_STALE_DAYS, BP_DORMANT_DAYS, "Blood pressure", NOW
        )
        assert alert is not None
        assert "Blood pressure" in alert

    def test_dormant_silences_alarm(self, cardio_db):
        _insert_bp(cardio_db, _days_ago(BP_DORMANT_DAYS + 1))
        assert _sparse_metric_alert(
            cardio_db, "blood_pressure", None, BP_STALE_DAYS, BP_DORMANT_DAYS, "Blood pressure", NOW
        ) is None

    def test_any_source_arms(self, cardio_db):
        """Unlike weight, BP arms on any source (manual or garmin)."""
        _insert_bp(cardio_db, _days_ago(BP_STALE_DAYS + 3))
        alert = _sparse_metric_alert(
            cardio_db, "blood_pressure", None, BP_STALE_DAYS, BP_DORMANT_DAYS, "Blood pressure", NOW
        )
        assert alert is not None


class TestWeight:
    def test_unarmed_when_never_recorded(self, cardio_db):
        assert _sparse_metric_alert(
            cardio_db, "body_weight", "garmin", WEIGHT_STALE_DAYS, WEIGHT_DORMANT_DAYS, "Weight", NOW
        ) is None

    def test_apple_and_dexa_only_stays_unarmed(self, cardio_db):
        """Apple anchors + DEXA rows, however old, must never arm the alarm —
        the scale doesn't exist yet."""
        _insert_weight(cardio_db, _days_ago(3000), "apple")
        _insert_weight(cardio_db, _days_ago(200), "dexa")
        assert _sparse_metric_alert(
            cardio_db, "body_weight", "garmin", WEIGHT_STALE_DAYS, WEIGHT_DORMANT_DAYS, "Weight", NOW
        ) is None

    def test_garmin_fresh_no_alert(self, cardio_db):
        _insert_weight(cardio_db, _days_ago(1), "garmin")
        assert _sparse_metric_alert(
            cardio_db, "body_weight", "garmin", WEIGHT_STALE_DAYS, WEIGHT_DORMANT_DAYS, "Weight", NOW
        ) is None

    def test_garmin_stale_alerts(self, cardio_db):
        _insert_weight(cardio_db, _days_ago(WEIGHT_STALE_DAYS + 1), "garmin")
        alert = _sparse_metric_alert(
            cardio_db, "body_weight", "garmin", WEIGHT_STALE_DAYS, WEIGHT_DORMANT_DAYS, "Weight", NOW
        )
        assert alert is not None
        assert "Weight" in alert

    def test_garmin_dormant_silences(self, cardio_db):
        _insert_weight(cardio_db, _days_ago(WEIGHT_DORMANT_DAYS + 5), "garmin")
        assert _sparse_metric_alert(
            cardio_db, "body_weight", "garmin", WEIGHT_STALE_DAYS, WEIGHT_DORMANT_DAYS, "Weight", NOW
        ) is None

    def test_fresh_apple_row_does_not_mask_stale_garmin(self, cardio_db):
        """Arming/recency is measured on source='garmin' only — a fresh Apple
        row must not paper over a stale Garmin scale habit."""
        _insert_weight(cardio_db, _days_ago(1), "apple")
        _insert_weight(cardio_db, _days_ago(WEIGHT_STALE_DAYS + 3), "garmin")
        alert = _sparse_metric_alert(
            cardio_db, "body_weight", "garmin", WEIGHT_STALE_DAYS, WEIGHT_DORMANT_DAYS, "Weight", NOW
        )
        assert alert is not None


def test_operational_error_degrades_to_none_not_crash(cardio_db):
    """A pre-migration DB missing the cardio tables must degrade to 'unarmed',
    not raise sqlite3.OperationalError up through the caller."""
    cardio_db.execute("DROP TABLE blood_pressure")
    assert _sparse_metric_alert(
        cardio_db, "blood_pressure", None, BP_STALE_DAYS, BP_DORMANT_DAYS, "Blood pressure", NOW
    ) is None


def test_check_staleness_no_false_alarm_before_scale_exists(cardio_db):
    """The false-alarm regression from the phase's mini-validation step 5:
    zero BP/weight data must never surface a Blood pressure/Weight alert."""
    stale = check_staleness(now=NOW)
    assert not any("Blood pressure" in s for s in stale)
    assert not any("Weight" in s for s in stale)


def test_check_staleness_alerts_once_armed_and_stale(cardio_db):
    _insert_bp(cardio_db, _days_ago(BP_STALE_DAYS + 1))
    _insert_weight(cardio_db, _days_ago(WEIGHT_STALE_DAYS + 1), "garmin")
    stale = check_staleness(now=NOW)
    assert any("Blood pressure" in s for s in stale)
    assert any("Weight" in s for s in stale)
