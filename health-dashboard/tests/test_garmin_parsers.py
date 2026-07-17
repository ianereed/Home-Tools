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


# --- Nutrition mapper (shape verified live 2026-07-16, journal-224 probe) ---

from collectors.garmin_collector import _nutrition_row


def _meal_content(**overrides):
    mc = {
        "calories": 500, "carbs": 60.0, "protein": 25.0, "fat": 18.0,
        "fiber": 6.0, "sugar": 12.0, "addedSugars": 4.0,
        "saturatedFat": 5.0, "monounsaturatedFat": 7.0,
        "polyunsaturatedFat": 3.0, "transFat": 0.0, "cholesterol": 40.0,
        "sodium": 800.0, "potassium": 600.0,
        "vitaminD": 2.0, "calcium": 200.0, "iron": 3.0,
    }
    mc.update(overrides)
    return mc


def _meal_detail(content, n_foods=1):
    return {
        "meal": {"mealId": 1, "mealName": "Breakfast"},
        "mealNutritionContent": content,
        "mealNutritionGoals": {"calories": 700},
        "loggedFoods": [{"id": f"f{i}"} for i in range(n_foods)],
    }


def _nutrition_payload(meal_details, meal_date="2026-07-16"):
    return {
        "mealDate": meal_date,
        "dayStartTime": "00:00:00",
        "dayEndTime": "23:59:59",
        "dailyViewType": "SINGLE_COLUMN",
        "dailyNutritionGoals": {"calories": 2600, "adjustedCalories": 2800},
        # NOTE: daily rollup only ever carries calories/carbs/fat/protein —
        # never sodium — hence the mapper sums mealNutritionContent instead.
        "dailyNutritionContent": {"calories": 1000, "carbs": 120.0,
                                   "fat": 36.0, "protein": 50.0,
                                   "caloriesPercentage": 38.0},
        "mealDetails": meal_details,
        "loggedFoodsWithServingSizes": [],
    }


def test_nutrition_row_sums_meals():
    payload = _nutrition_payload([
        _meal_detail(_meal_content()),
        _meal_detail(_meal_content(sodium=450.0, potassium=300.0, calories=700)),
    ])
    row = _nutrition_row(payload)
    assert row is not None
    (date, calories, protein, carbs, fat, satfat, fiber, sugar,
     sodium, potassium, source) = row
    assert date == "2026-07-16"
    assert source == "garmin"
    assert calories == 1200          # 500 + 700
    assert sodium == 1250.0          # 800 + 450
    assert potassium == 900.0        # 600 + 300
    assert satfat == 10.0            # 5 + 5
    assert fiber == 12.0


def test_nutrition_row_none_aware_sum():
    # potassium absent from BOTH meals -> stays NULL (unknown), never 0;
    # sodium present in only one meal -> that meal's value, not halved/zeroed.
    payload = _nutrition_payload([
        _meal_detail(_meal_content(potassium=None, sodium=None)),
        _meal_detail(_meal_content(potassium=None, sodium=650.0)),
    ])
    row = _nutrition_row(payload)
    sodium, potassium = row[8], row[9]
    assert sodium == 650.0
    assert potassium is None


def test_nutrition_row_empty_day_returns_none():
    # Real empty-day shell (probed): mealDetails [] — feature on, nothing logged.
    assert _nutrition_row(_nutrition_payload([])) is None
    assert _nutrition_row(None) is None
    assert _nutrition_row({}) is None


def test_nutrition_row_meal_shells_without_foods_do_not_count():
    # Enabled meals with a zeroed rollup but no loggedFoods = an unlogged day,
    # not a zero-intake day — writing zeros would fake a perfect-sodium day.
    shell = _meal_detail(_meal_content(), n_foods=0)
    assert _nutrition_row(_nutrition_payload([shell])) is None
