"""Unit tests for the pure Garmin BP/weight mappers in collectors.garmin_collector.

Fixture payload shapes are built to match the REAL Garmin API responses probed
live in Phase 0 (CARDIO_PLAN.md Appendix D / journal-219.md), with synthetic
leaf values substituted for Ian's actual readings. In particular:
  - get_blood_pressure is day-summary-nested: real per-reading fields live at
    measurementSummaries[*].measurements[*], not the top level.
  - get_body_composition returns an all-BIA-None day-summary row for manual
    (non-scale) weigh-ins rather than omitting it or erroring.
  - Garmin reports weight/muscleMass/boneMass in grams.
"""
from collectors.garmin_collector import _bp_rows, _weight_rows


def _bp_measurement(**overrides):
    m = {
        "version": 1699999999,
        "systolic": 122,
        "diastolic": 78,
        "pulse": 62,
        "multiMeasurement": False,
        "notes": "",
        "sourceType": "MANUAL",
        "measurementTimestampLocal": "2026-03-01T08:15:00.0",
        "measurementTimestampGMT": "2026-03-01T15:15:00.0",
        "category": "NORMAL",
        "categoryName": "Normal",
    }
    m.update(overrides)
    return m


def _bp_day_summary(measurements):
    return {
        "startDate": "2026-03-01",
        "endDate": "2026-03-01",
        "highSystolic": 128,
        "highDiastolic": 82,
        "lowSystolic": 118,
        "lowDiastolic": 74,
        "numOfMeasurements": len(measurements),
        "category": "NORMAL",
        "categoryName": "Normal",
        "measurements": measurements,
    }


def _bp_payload(day_summaries):
    return {
        "from": "2025-07-15",
        "until": "2026-07-15",
        "measurementSummaries": day_summaries,
        "categoryStats": {
            "from": "2025-07-15", "until": "2026-07-15",
            "noOfDaysNormal": 1, "noOfDaysElevated": 0, "noOfDaysStage1": 0,
            "noOfDaysStage2": 0, "noOfDaysCritical": 0,
            "categoryCounts": {
                "HYPERTENSIVE_CRISIS": 0, "STAGE_2_HIGH": 0,
                "STAGE_1_HIGH": 0, "ELEVATED": 0, "NORMAL": 1,
            },
        },
    }


class TestBpRows:
    def test_basic_mapping(self):
        payload = _bp_payload([_bp_day_summary([_bp_measurement()])])
        rows = _bp_rows(payload)
        assert rows == [
            ("2026-03-01T08:15:00", 122, 78, 62, "garmin", "1699999999", None),
        ]

    def test_day_summary_fields_not_written_as_rows(self):
        """The day-level aggregates (highSystolic/lowSystolic/etc.) must never
        surface as a blood_pressure row — only per-reading measurements."""
        payload = _bp_payload([_bp_day_summary([_bp_measurement()])])
        rows = _bp_rows(payload)
        assert len(rows) == 1
        # the day summary's highSystolic (128) is not one of the returned values
        assert rows[0][1] == 122

    def test_empty_note_becomes_none(self):
        payload = _bp_payload([_bp_day_summary([_bp_measurement(notes="")])])
        rows = _bp_rows(payload)
        assert rows[0][6] is None

    def test_real_note_preserved(self):
        payload = _bp_payload([_bp_day_summary([_bp_measurement(notes="after run")])])
        rows = _bp_rows(payload)
        assert rows[0][6] == "after run"

    def test_multiple_days_and_measurements_flattened(self):
        day1 = _bp_day_summary([_bp_measurement(version=1), _bp_measurement(version=2)])
        day2 = _bp_day_summary([_bp_measurement(version=3)])
        payload = _bp_payload([day1, day2])
        rows = _bp_rows(payload)
        assert len(rows) == 3
        assert [r[4] for r in rows] == ["garmin", "garmin", "garmin"]

    def test_malformed_measurement_skipped_not_fatal(self):
        """A reading missing systolic/diastolic must be skipped, not raise."""
        good = _bp_measurement()
        bad = _bp_measurement(systolic=None)
        payload = _bp_payload([_bp_day_summary([bad, good])])
        rows = _bp_rows(payload)
        assert len(rows) == 1
        assert rows[0][1] == 122

    def test_empty_payload_returns_empty_list(self):
        assert _bp_rows({}) == []
        assert _bp_rows(None) == []

    def test_no_measurements_in_day_returns_empty_list(self):
        payload = _bp_payload([_bp_day_summary([])])
        assert _bp_rows(payload) == []

    def test_missing_version_yields_none_source_id(self):
        payload = _bp_payload([_bp_day_summary([_bp_measurement(version=None)])])
        rows = _bp_rows(payload)
        assert rows[0][5] is None


def _weigh_in(**overrides):
    row = {
        "samplePk": 111222333,
        "date": 1735689600000,
        "calendarDate": "2026-01-01",
        "weight": 81800.0,          # grams
        "bmi": None,
        "bodyFat": None,
        "bodyWater": None,
        "boneMass": None,
        "muscleMass": None,
        "physiqueRating": None,
        "visceralFat": None,
        "metabolicAge": None,
        "sourceType": "MANUAL_ENTRY",
        "timestampGMT": 1735693200000,
        "weightDelta": None,
    }
    row.update(overrides)
    return row


def _comp_payload(rows):
    return {
        "startDate": "2025-07-15",
        "endDate": "2026-07-15",
        "dateWeightList": rows,
        "totalAverage": {
            "from": 1721001600000, "until": 1752537600000,
            "weight": 81000.0, "bmi": None, "bodyFat": None, "bodyWater": None,
            "boneMass": None, "muscleMass": None, "physiqueRating": None,
            "visceralFat": None, "metabolicAge": None,
        },
    }


class TestWeightRows:
    def test_manual_entry_no_bia_writes_weight_only(self):
        """Real observed shape: manual (no-scale) weigh-ins return an
        all-BIA-None day-summary row rather than omitting it — must write
        body_weight but NOT body_composition for these."""
        payload = _comp_payload([_weigh_in()])
        weight_rows, comp_rows = _weight_rows(payload)
        assert weight_rows == [("2026-01-01T00:00:00", 81.8, None, "garmin", "111222333")]
        assert comp_rows == []

    def test_scale_weigh_in_with_bia_writes_both(self):
        row = _weigh_in(
            samplePk=111222444, calendarDate="2026-01-23", weight=80200.0,
            bmi=24.1, bodyFat=21.3, bodyWater=55.2, boneMass=3100.0,
            muscleMass=34500.0, physiqueRating=6, visceralFat=8.0, metabolicAge=33,
        )
        payload = _comp_payload([row])
        weight_rows, comp_rows = _weight_rows(payload)
        assert weight_rows == [("2026-01-23T00:00:00", 80.2, 24.1, "garmin", "111222444")]
        assert len(comp_rows) == 1
        (ts, weight_kg, body_fat_pct, lean_mass_kg, fat_mass_kg, bone_mass_kg,
         visceral_fat_rating, visceral_fat_mass_kg, body_water_pct, note, source) = comp_rows[0]
        assert ts == "2026-01-23T00:00:00"
        assert weight_kg == 80.2
        assert body_fat_pct == 21.3
        assert lean_mass_kg == 34.5   # muscleMass grams -> kg
        assert fat_mass_kg is None    # DEXA-direct only, never from Garmin
        assert bone_mass_kg == 3.1    # boneMass grams -> kg
        assert visceral_fat_rating == 8.0
        assert visceral_fat_mass_kg is None
        assert body_water_pct == 55.2
        assert note is None
        assert source == "garmin"

    def test_grams_to_kg_conversion(self):
        payload = _comp_payload([_weigh_in(weight=78400.0)])
        weight_rows, _ = _weight_rows(payload)
        assert weight_rows[0][1] == 78.4

    def test_missing_calendar_date_falls_back_to_gmt_epoch(self):
        row = _weigh_in(calendarDate=None, timestampGMT=1735693200000)  # 2025-01-01T01:00:00Z
        payload = _comp_payload([row])
        weight_rows, _ = _weight_rows(payload)
        assert weight_rows[0][0] == "2025-01-01T00:00:00"

    def test_missing_weight_row_skipped(self):
        payload = _comp_payload([_weigh_in(weight=None)])
        weight_rows, comp_rows = _weight_rows(payload)
        assert weight_rows == []
        assert comp_rows == []

    def test_missing_calendar_date_and_gmt_row_skipped(self):
        payload = _comp_payload([_weigh_in(calendarDate=None, timestampGMT=None)])
        weight_rows, comp_rows = _weight_rows(payload)
        assert weight_rows == []
        assert comp_rows == []

    def test_empty_payload_returns_empty_lists(self):
        assert _weight_rows({}) == ([], [])
        assert _weight_rows(None) == ([], [])

    def test_empty_date_weight_list_returns_empty_lists(self):
        payload = _comp_payload([])
        assert _weight_rows(payload) == ([], [])

    def test_single_bia_field_present_still_writes_composition(self):
        """Only one BIA field populated (e.g. visceralFat alone) should still
        count as 'has BIA data', not require all fields present."""
        row = _weigh_in(visceralFat=5.0)
        payload = _comp_payload([row])
        _, comp_rows = _weight_rows(payload)
        assert len(comp_rows) == 1
