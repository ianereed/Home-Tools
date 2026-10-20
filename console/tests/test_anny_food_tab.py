"""Anny Food tab — driven end to end with Streamlit's AppTest."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from anny_food import breakdown as bd
from anny_food import db as adb
from anny_food.targets import load_targets
from meal_planner import db as mp_db
from meal_planner.db import insert_ingredient, insert_recipe


def _app():
    from console.tabs import anny_food
    anny_food.render()


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "recipes.db"
    adb.init_db(p)
    monkeypatch.setattr(mp_db, "DB_PATH", p)
    return p


@pytest.fixture
def seeded(db_path):
    targets = load_targets()
    vals = {k: 0.0 for k in targets.value_keys()}
    rid = insert_recipe(title="Eggs + spinach", base_servings=1, path=db_path)
    insert_ingredient(recipe_id=rid, name="eggs", qty_per_serving=2, path=db_path)
    conn = adb.connect(db_path)
    rep = bd.import_payload(conn, {
        "model": "test",
        "recipes": [{"recipe_id": rid, "ingredients_hash": bd.current_hash(conn, rid),
                     "values": {**vals, "protein_meat_eggs": 2.0, "choline": 300},
                     "breakdown": [{"ingredient": "eggs", "amount": "2", "key": "protein_meat_eggs", "eq": 2}],
                     "safety_flags": ["raw_egg"], "confidence": "high"}],
        "staples": [{"name": "Apple", "serving_label": "1 medium",
                     "values": {**vals, "fruit": 1.0}, "confidence": "high"}],
    }, targets)
    assert rep.ok, rep.errors
    conn.commit()
    conn.close()
    return rid


def _run() -> AppTest:
    at = AppTest.from_function(_app, default_timeout=10)
    at.run()
    assert not at.exception, at.exception
    return at


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _log_rows(db_path):
    conn = adb.connect(db_path)
    rows = [dict(r) for r in conn.execute("SELECT * FROM anny_log ORDER BY id")]
    conn.close()
    return rows


def _bars(at) -> str:
    return "".join(m.value for m in at.markdown if "anny-bars" in m.value)


def test_empty_db_renders(db_path):
    at = _run()
    assert any("not medical advice" in c.value for c in at.caption)
    assert any("Trimester 1" in m.value and "assumed" in m.value for m in at.markdown)
    assert "Vegetables (total)" in _bars(at)
    assert any(e.label.startswith("Today") for e in at.expander)


def test_add_staple_updates_bars(db_path, seeded):
    at = _run()
    at.selectbox(key="anny_add_food").set_value("staple:1")
    _button(at, "Add").click()
    at.run()
    assert not at.exception, at.exception
    rows = _log_rows(db_path)
    assert [(r["kind"], r["label"], r["servings"]) for r in rows] == [("staple", "Apple (1 medium)", 1.0)]
    assert "1.0 / 14.0 cup eq" in _bars(at)
    assert any("Added 1× Apple" in s.value for s in at.success)


def test_add_recipe_with_servings(db_path, seeded):
    at = _run()
    at.selectbox(key="anny_add_food").set_value(f"recipe:{seeded}")
    at.number_input(key="anny_add_servings").set_value(1.5)
    _button(at, "Add").click()
    at.run()
    assert "3.0 / 26.0 oz eq" in _bars(at)       # meat & eggs: 2 × 1.5 servings vs 26 oz/wk
    assert _log_rows(db_path)[0]["recipe_id"] == seeded


def test_add_without_pick_warns(db_path, seeded):
    at = _run()
    _button(at, "Add").click()
    at.run()
    assert _log_rows(db_path) == []
    assert any("Pick a food" in w.value for w in at.warning)


def test_pending_custom_entry(db_path):
    at = _run()
    at.text_input(key="anny_custom_label").set_value("bowl of pho")
    _button(at, "Figure it out later").click()
    at.run()
    rows = _log_rows(db_path)
    assert [(r["kind"], r["label"], r["values_json"]) for r in rows] == [("pending", "bowl of pho", None)]
    assert any("not counted yet" in c.value for c in at.caption)


def test_custom_entry_with_amounts(db_path):
    at = _run()
    at.text_input(key="anny_custom_label").set_value("latte")
    at.number_input(key="anny_custom_dairy").set_value(1.0)
    _button(at, "Save with these amounts").click()
    at.run()
    row = _log_rows(db_path)[0]
    assert row["kind"] == "custom" and '"dairy": 1.0' in row["values_json"]


def test_edit_and_delete_entry(db_path, seeded):
    at = _run()
    at.selectbox(key="anny_add_food").set_value("staple:1")
    _button(at, "Add").click()
    at.run()
    eid = _log_rows(db_path)[0]["id"]
    at.number_input(key=f"anny_srv_{eid}").set_value(2.0)
    at.button(key=f"anny_save_{eid}").click()
    at.run()
    assert _log_rows(db_path)[0]["servings"] == 2.0
    at.button(key=f"anny_del_{eid}").click()
    at.run()
    assert _log_rows(db_path) == []


def test_breakdown_view_and_override(db_path, seeded):
    at = _run()
    at.selectbox(key="anny_bd_pick").set_value(f"recipe:{seeded}")
    at.run()
    assert any("Raw or undercooked egg" in e.value for e in at.error)
    at.number_input(key=f"anny_ovr_{seeded}_dairy").set_value(0.5)
    _button(at, "Save edits").click()
    at.run()
    conn = adb.connect(db_path)
    n = bd.recipe_nutrition(conn, seeded, load_targets())
    conn.close()
    assert n.overrides == {"dairy": 0.5} and n.values["protein_meat_eggs"] == 2.0


def test_settings_due_date_sets_trimester(db_path):
    at = _run()
    due = date.today() + timedelta(days=280 - 20 * 7)   # 20w0d today
    at.date_input(key="anny_set_due").set_value(due)
    _button(at, "Save settings").click()
    at.run()
    assert any("Trimester 2" in m.value and "20 weeks 0 days" in m.value for m in at.markdown)


def test_history_lists_past_week(db_path, seeded):
    conn = adb.connect(db_path)
    conn.execute("INSERT INTO anny_log (eaten_on, logged_at, kind, staple_id, servings, label) "
                 "VALUES (?, 'x', 'staple', 1, 7, 'Apple')",
                 ((date.today() - timedelta(days=8)).isoformat(),))
    conn.commit()
    conn.close()
    at = _run()
    df = next(d.value for d in at.dataframe if "Week" in d.value.columns)
    assert df.iloc[0]["Fruits"] == "50%"
