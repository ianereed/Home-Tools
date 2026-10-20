from datetime import date, timedelta

import pytest

from anny_food import track as tr
from anny_food.targets import load_targets


@pytest.fixture(scope="module")
def targets():
    return load_targets()


DUE = date(2027, 5, 1)


def _on_week(weeks: int, days: int = 0) -> date:
    """The calendar date at a given gestational age for DUE."""
    return DUE - timedelta(days=280 - (weeks * 7 + days))


@pytest.mark.parametrize("weeks, days, expected", [
    (8, 0, "t1"), (13, 6, "t1"), (14, 0, "t2"), (27, 6, "t2"), (28, 0, "t3"), (41, 0, "t3"),
])
def test_trimester_boundaries(weeks, days, expected):
    today = _on_week(weeks, days)
    assert tr.gestational_age(DUE, today) == (weeks, days)
    assert tr.trimester(DUE, today) == expected


def test_trimester_override_and_default():
    assert tr.trimester(DUE, _on_week(30), override="t2") == "t2"
    assert tr.trimester(None, date(2026, 10, 20)) == "t1"


def test_week_bounds_monday_and_sunday():
    tue = date(2026, 10, 20)
    assert tr.week_bounds(tue) == (date(2026, 10, 19), date(2026, 10, 25))
    assert tr.week_bounds(tue, "sunday") == (date(2026, 10, 18), date(2026, 10, 24))
    mon = date(2026, 10, 19)
    assert tr.week_bounds(mon)[0] == mon


def test_pace():
    start = date(2026, 10, 19)
    assert tr.pace(start + timedelta(days=2), start) == pytest.approx(3 / 7)
    assert tr.pace(start + timedelta(days=30), start) == 1.0
    assert tr.pace(start - timedelta(days=1), start) == 0.0


def test_legumes_fill_veg_then_overflow_to_protein(targets):
    # T1 veg_legumes target is 1.5 cup eq/week.
    totals = tr.weekly_totals({"veg_legumes": 2.0, "protein_meat_eggs": 10.0}, targets, "t1")
    assert totals["veg_legumes"] == pytest.approx(1.5)
    assert totals["legume_protein"] == pytest.approx(2.0)      # 0.5 cup × 4
    assert totals["protein_total"] == pytest.approx(12.0)
    assert totals["veg_total"] == pytest.approx(1.5)


def test_legumes_under_target_stay_veg(targets):
    totals = tr.weekly_totals({"veg_legumes": 1.0, "veg_other": 2.0}, targets, "t1")
    assert totals["legume_protein"] == 0
    assert totals["veg_total"] == pytest.approx(3.0)


def test_albacore_not_double_counted_in_protein_total(targets):
    totals = tr.weekly_totals({"protein_seafood": 6.0, "seafood_albacore": 4.0}, targets, "t1")
    assert totals["protein_total"] == pytest.approx(6.0)


def test_status_min_rows(targets):
    fruit = targets.get("fruit")                         # 14 cup eq/week at T1
    assert tr.status(fruit, 14, "t1", 0.5) == tr.MET
    assert tr.status(fruit, 6.5, "t1", 0.5) == tr.ON_PACE   # 7 × 0.85 = 5.95
    assert tr.status(fruit, 5, "t1", 0.5) == tr.UNDER


def test_status_seafood_ceiling(targets):
    sea = targets.get("protein_seafood")
    assert tr.status(sea, 10, "t1", 1.0) == tr.MET
    assert tr.status(sea, 12.5, "t1", 1.0) == tr.OVER


def test_status_limit_rows(targets):
    refined = targets.get("grain_refined")               # max 21 oz eq/week
    assert tr.status(refined, 5, "t1", 3 / 7) == tr.OK
    assert tr.status(refined, 12, "t1", 3 / 7) == tr.AHEAD
    assert tr.status(refined, 22, "t1", 1.0) == tr.OVER


def test_percent(targets):
    assert tr.percent(targets.get("dairy"), 10.5, "t1") == pytest.approx(50.0)
