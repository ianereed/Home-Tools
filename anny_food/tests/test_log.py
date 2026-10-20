from datetime import date
from pathlib import Path

import pytest

from anny_food import breakdown as bd
from anny_food import db as adb
from anny_food import log as lg
from anny_food.targets import load_targets
from meal_planner.db import insert_ingredient, insert_recipe
from meal_planner.queries import update_recipe


@pytest.fixture(scope="module")
def targets():
    return load_targets()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "recipes.db"
    adb.init_db(p)
    return p


@pytest.fixture
def conn(db_path):
    c = adb.connect(db_path)
    yield c
    c.close()


def _vals(targets, **kw):
    v = {k: 0.0 for k in targets.value_keys()}
    v.update(kw)
    return v


@pytest.fixture
def recipe(conn, db_path, targets):
    rid = insert_recipe(title="Eggs", base_servings=1, path=db_path)
    insert_ingredient(recipe_id=rid, name="eggs", qty_per_serving=2, path=db_path)
    item = {"recipe_id": rid, "ingredients_hash": bd.current_hash(conn, rid),
            "values": _vals(targets, protein_meat_eggs=2.0), "confidence": "high"}
    assert bd.import_payload(conn, {"recipes": [item]}, targets).ok
    conn.commit()
    return rid


@pytest.fixture
def yogurt(conn, targets):
    s = {"name": "Greek yogurt", "serving_label": "1 cup",
         "values": _vals(targets, dairy=1.0, calcium=250), "confidence": "high"}
    assert bd.import_payload(conn, {"staples": [s]}, targets).ok
    conn.commit()
    return conn.execute("SELECT id FROM anny_staples").fetchone()["id"]


D = date(2026, 10, 20)


def test_entry_values_scale_with_servings(conn, targets, recipe, yogurt):
    lg.add_entry(conn, eaten_on=D, kind="recipe", label="Eggs", recipe_id=recipe, servings=1.5)
    lg.add_entry(conn, eaten_on=D, kind="staple", label="Greek yogurt", staple_id=yogurt)
    lg.add_entry(conn, eaten_on=D, kind="custom", label="smoothie", values={"fruit": 1.0})
    lg.add_entry(conn, eaten_on=D, kind="pending", label="pho at a restaurant")
    entries = lg.entries_between(conn, D, D)
    r = lg.ValueResolver(conn, targets)
    vals = [r.values(e) for e in entries]
    assert vals[0]["protein_meat_eggs"] == pytest.approx(3.0)
    assert vals[1]["calcium"] == 250
    assert vals[2] == {"fruit": 1.0}
    assert vals[3] == {} and r.per_serving(entries[3])[1] == "breakdown pending"


def test_stale_recipe_still_counts_but_warns(conn, db_path, targets, recipe):
    conn.commit()
    update_recipe(recipe, base_servings=2, path=db_path)
    lg.add_entry(conn, eaten_on=D, kind="recipe", label="Eggs", recipe_id=recipe)
    e = lg.entries_between(conn, D, D)[0]
    vals, problem = lg.ValueResolver(conn, targets).per_serving(e)
    assert vals["protein_meat_eggs"] == 2.0 and "changed" in problem


def test_recipe_without_breakdown_is_pending(conn, db_path, targets):
    rid = insert_recipe(title="New", base_servings=1, path=db_path)
    lg.add_entry(conn, eaten_on=D, kind="recipe", label="New", recipe_id=rid)
    e = lg.entries_between(conn, D, D)[0]
    assert lg.ValueResolver(conn, targets).per_serving(e) == ({}, "breakdown pending")


def test_update_and_delete(conn):
    eid = lg.add_entry(conn, eaten_on=D, kind="custom", label="x", values={"fruit": 1})
    lg.update_entry(conn, eid, servings=2, eaten_on=date(2026, 10, 21))
    assert lg.entries_between(conn, D, D) == []
    e = lg.entries_between(conn, date(2026, 10, 21), date(2026, 10, 21))[0]
    assert e["servings"] == 2
    lg.delete_entry(conn, eid)
    assert lg.first_entry_date(conn) is None


def test_list_foods_marks_breakdown_status(conn, db_path, recipe, yogurt):
    insert_recipe(title="Another", base_servings=1, path=db_path)
    foods = lg.list_foods(conn)
    assert [(f["kind"], f["label"], f["has_breakdown"]) for f in foods] == [
        ("recipe", "Another", False),
        ("recipe", "Eggs", True),
        ("staple", "Greek yogurt (1 cup)", True),
    ]
