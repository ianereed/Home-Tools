from pathlib import Path

import pytest

from anny_food.targets import load_targets


@pytest.fixture(scope="module")
def targets():
    return load_targets()


def test_shipped_config_loads(targets):
    assert "veg_dark_green" in targets.value_keys()
    # Totals and disabled rows are never stored per recipe.
    assert "veg_total" not in targets.value_keys()
    assert "protein_total" not in targets.value_keys()
    assert "sodium" not in targets.value_keys()


def test_weekly_scales_daily_rows_only(targets):
    assert targets.get("veg_total").weekly("t1") == pytest.approx(17.5)
    assert targets.get("veg_dark_green").weekly("t1") == pytest.approx(1.5)
    assert targets.get("veg_dark_green").daily("t1") is None
    assert targets.get("calcium").daily("t3") == 1000


def test_veg_subgroups_sum_to_total(targets):
    # Table 5-1 is internally consistent: weekly subgroups = daily total × 7
    # (within the workbook's 0.1 rounding).
    for tri in ("t1", "t2", "t3"):
        total = targets.get("veg_total")
        subs = sum(targets.get(k).weekly(tri) for k in total.derived_from)
        assert subs == pytest.approx(total.weekly(tri), abs=0.5)


def test_protein_subgroups_sum_to_total(targets):
    # Looser: Table 5-1's own subgroups (26+8+5 = 39 oz/wk) already exceed
    # 5.5 oz/day × 7 = 38.5, and rounding the scaled daily figure to 0.1
    # widens that to ~0.9 at T2.
    for tri in ("t1", "t2", "t3"):
        total = targets.get("protein_total")
        subs = sum(targets.get(k).weekly(tri) for k in total.derived_from)
        assert subs == pytest.approx(total.weekly(tri), abs=1.0)


def test_seafood_ceiling_and_refined_limit(targets):
    assert targets.get("protein_seafood").ceiling == 12.0
    assert targets.get("grain_refined").kind == "limit"
    assert targets.get("seafood_albacore").subset_of == "protein_seafood"
    for tri in ("t1", "t2", "t3"):
        assert targets.get("protein_seafood").weekly(tri) <= 12.0


def test_safety_flags_present(targets):
    assert {"raw_egg", "high_mercury_fish", "liver"} <= set(targets.safety_flags)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "t.toml"
    p.write_text(body)
    return p


def test_rejects_bad_period(tmp_path):
    p = _write(tmp_path, '[[target]]\nkey="x"\nperiod="month"\nkind="min"\nt1=1\nt2=1\nt3=1\n')
    with pytest.raises(ValueError, match="period"):
        load_targets(p)


def test_rejects_unknown_reference(tmp_path):
    p = _write(tmp_path, '[[target]]\nkey="x"\nperiod="day"\nkind="min"\nt1=1\nt2=1\nt3=1\n'
                         'derived_from=["nope"]\n')
    with pytest.raises(ValueError, match="unknown keys"):
        load_targets(p)


def test_rejects_missing_trimester(tmp_path):
    p = _write(tmp_path, '[[target]]\nkey="x"\nperiod="day"\nkind="min"\nt1=1\nt2=1\n')
    with pytest.raises(ValueError, match="missing"):
        load_targets(p)
