"""Unit tests for cardiology/import_dexa.py.

All CSV fixtures use obviously-synthetic values — nothing here is Ian's real
DEXA data. Row/column shapes match CARDIO_PLAN.md Appendix C.
"""
import sqlite3

import pytest

import collectors.db as db
from cardiology.import_dexa import import_csv, lb_to_kg, parse_row, write_template

CSV_HEADER = (
    "date,weight_lb,body_fat_pct,lean_mass_lb,fat_mass_lb,bone_mass_lb,"
    "visceral_fat_lb,note\n"
)


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    """Freshly-initialized (unseeded) schema DB — isolates idempotency counts
    from any pre-existing body_composition/body_weight rows."""
    db_path = tmp_path / "scratch.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    return str(db_path)


def _write_csv(tmp_path, body):
    path = tmp_path / "dexa_scans.csv"
    path.write_text(CSV_HEADER + body)
    return str(path)


def test_lb_to_kg():
    assert lb_to_kg(186.2) == pytest.approx(84.46, abs=0.01)


def test_parse_row_converts_and_validates():
    row = {
        "date": "2026-09-15", "weight_lb": "186.2", "body_fat_pct": "24.1",
        "lean_mass_lb": "134.5", "fat_mass_lb": "44.9", "bone_mass_lb": "6.8",
        "visceral_fat_lb": "1.2", "note": "fake scan",
    }
    parsed = parse_row(row, line_no=2)
    assert parsed["date"] == "2026-09-15"
    assert parsed["weight_kg"] == pytest.approx(84.46, abs=0.01)
    assert parsed["body_fat_pct"] == 24.1
    assert parsed["lean_mass_kg"] == pytest.approx(61.0, abs=0.1)
    assert parsed["fat_mass_kg"] == pytest.approx(20.38, abs=0.1)
    assert parsed["bone_mass_kg"] == pytest.approx(3.08, abs=0.1)
    assert parsed["visceral_fat_mass_kg"] == pytest.approx(0.54, abs=0.05)
    assert parsed["note"] == "fake scan"
    assert parsed["warning"] is None  # 186.2 vs 134.5+44.9+6.8=186.2, exact match


def test_parse_row_rejects_bad_date():
    row = {
        "date": "not-a-date", "weight_lb": "180", "body_fat_pct": "20",
        "lean_mass_lb": "", "fat_mass_lb": "", "bone_mass_lb": "",
        "visceral_fat_lb": "", "note": "",
    }
    with pytest.raises(ValueError, match="unparseable date"):
        parse_row(row, line_no=5)


def test_parse_row_rejects_body_fat_pct_out_of_range():
    row = {
        "date": "2026-01-01", "weight_lb": "180", "body_fat_pct": "1",
        "lean_mass_lb": "", "fat_mass_lb": "", "bone_mass_lb": "",
        "visceral_fat_lb": "", "note": "",
    }
    with pytest.raises(ValueError, match="body_fat_pct"):
        parse_row(row, line_no=3)


def test_parse_row_rejects_weight_out_of_range():
    row = {
        "date": "2026-01-01", "weight_lb": "50", "body_fat_pct": "20",
        "lean_mass_lb": "", "fat_mass_lb": "", "bone_mass_lb": "",
        "visceral_fat_lb": "", "note": "",
    }
    with pytest.raises(ValueError, match="weight_kg"):
        parse_row(row, line_no=4)


def test_parse_row_warns_on_mass_sum_mismatch():
    row = {
        "date": "2026-01-01", "weight_lb": "180", "body_fat_pct": "20",
        "lean_mass_lb": "100", "fat_mass_lb": "40", "bone_mass_lb": "5",
        # 100+40+5=145 vs weight 180 -> way off -> warning, not a reject
        "visceral_fat_lb": "", "note": "",
    }
    parsed = parse_row(row, line_no=6)
    assert parsed["warning"] is not None
    assert "180.0" in parsed["warning"]


def test_import_csv_writes_both_tables(tmp_path, scratch_db):
    csv_path = _write_csv(
        tmp_path,
        "2026-09-15,186.2,24.1,134.5,44.9,6.8,1.2,fake scan one\n"
        "2026-06-15,190.0,25.0,133.0,50.0,7.0,1.3,fake scan two\n",
    )
    summary = import_csv(csv_path, scratch_db)
    assert summary["imported"] == 2
    assert summary["rejected"] == []

    conn = sqlite3.connect(scratch_db)
    comp_rows = conn.execute(
        "SELECT timestamp, weight_kg, body_fat_pct, source FROM body_composition "
        "ORDER BY timestamp").fetchall()
    weight_rows = conn.execute(
        "SELECT timestamp, weight_kg, source FROM body_weight "
        "ORDER BY timestamp").fetchall()
    conn.close()

    assert len(comp_rows) == 2
    assert len(weight_rows) == 2
    assert comp_rows[0][0] == "2026-06-15T00:00:00"
    assert comp_rows[0][3] == "dexa"
    assert comp_rows[0][1] == pytest.approx(86.18, abs=0.01)
    assert weight_rows[0][2] == "dexa"


def test_import_csv_skips_rejected_rows_but_imports_the_rest(tmp_path, scratch_db):
    csv_path = _write_csv(
        tmp_path,
        "2026-09-15,186.2,24.1,134.5,44.9,6.8,1.2,fake scan good\n"
        "bad-date,186.2,24.1,134.5,44.9,6.8,1.2,fake scan bad\n",
    )
    summary = import_csv(csv_path, scratch_db)
    assert summary["imported"] == 1
    assert len(summary["rejected"]) == 1
    assert "line 3" in summary["rejected"][0]


def test_import_csv_is_idempotent(tmp_path, scratch_db):
    csv_path = _write_csv(
        tmp_path,
        "2026-09-15,186.2,24.1,134.5,44.9,6.8,1.2,fake scan\n",
    )
    import_csv(csv_path, scratch_db)
    summary_second = import_csv(csv_path, scratch_db)
    assert summary_second["imported"] == 1

    conn = sqlite3.connect(scratch_db)
    comp_count = conn.execute("SELECT COUNT(*) FROM body_composition").fetchone()[0]
    weight_count = conn.execute("SELECT COUNT(*) FROM body_weight").fetchone()[0]
    conn.close()
    assert comp_count == 1
    assert weight_count == 1


def test_import_csv_reimport_after_correction_converges(tmp_path, scratch_db):
    """Re-running with a corrected value overwrites rather than duplicating."""
    csv_path = _write_csv(
        tmp_path,
        "2026-09-15,186.2,24.1,134.5,44.9,6.8,1.2,fake scan\n",
    )
    import_csv(csv_path, scratch_db)

    # Overwrite the same file with a corrected body_fat_pct at the same date.
    with open(csv_path, "w") as f:
        f.write(CSV_HEADER)
        f.write("2026-09-15,186.2,23.0,134.5,44.9,6.8,1.2,fake scan corrected\n")
    import_csv(csv_path, scratch_db)

    conn = sqlite3.connect(scratch_db)
    rows = conn.execute(
        "SELECT body_fat_pct, note FROM body_composition WHERE timestamp='2026-09-15T00:00:00'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == 23.0
    assert rows[0][1] == "fake scan corrected"


def test_write_template_refuses_to_overwrite(tmp_path):
    path = tmp_path / "dexa_scans.csv"
    write_template(str(path))
    assert path.read_text().startswith("date,weight_lb")
    with pytest.raises(SystemExit):
        write_template(str(path))
