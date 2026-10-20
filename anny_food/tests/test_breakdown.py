import json
from pathlib import Path

import pytest

from anny_food import breakdown as bd
from anny_food import db as adb
from anny_food.targets import load_targets
from meal_planner.db import insert_ingredient, insert_recipe
from meal_planner.queries import update_ingredient, update_recipe


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


def _eggs_and_spinach(db_path: Path) -> int:
    rid = insert_recipe(title="Eggs + spinach", base_servings=1, path=db_path)
    insert_ingredient(recipe_id=rid, name="eggs", qty_per_serving=2, path=db_path)
    insert_ingredient(recipe_id=rid, name="spinach", qty_per_serving=1, unit="cup",
                      notes="raw", path=db_path)
    return rid


def _values(targets, **kw) -> dict:
    v = {k: 0.0 for k in targets.value_keys()}
    v.update(kw)
    return v


def _item(conn, targets, rid, **kw) -> dict:
    return {
        "recipe_id": rid,
        "ingredients_hash": bd.current_hash(conn, rid),
        "values": _values(targets, protein_meat_eggs=2.0, veg_dark_green=0.5,
                          choline=335.0, calcium=110.0),
        "breakdown": [{"ingredient": "eggs", "amount": "2", "key": "protein_meat_eggs", "eq": 2}],
        "safety_flags": [],
        "confidence": "high",
        "notes": "",
        **kw,
    }


# --- hashing ---------------------------------------------------------------

def test_hash_ignores_row_order_and_grocery_fields():
    a = [{"name": "eggs", "qty_per_serving": 2, "qty_raw": None, "unit": None, "notes": None},
         {"name": "spinach", "qty_per_serving": 1, "qty_raw": None, "unit": "cup", "notes": "raw"}]
    assert bd.ingredients_hash(1, a) == bd.ingredients_hash(1, list(reversed(a)))


def test_hash_changes_with_servings_and_qty():
    a = [{"name": "eggs", "qty_per_serving": 2, "qty_raw": None, "unit": None, "notes": None}]
    b = [{"name": "eggs", "qty_per_serving": 3, "qty_raw": None, "unit": None, "notes": None}]
    assert bd.ingredients_hash(1, a) != bd.ingredients_hash(2, a)
    assert bd.ingredients_hash(1, a) != bd.ingredients_hash(1, b)


# --- pending / staleness ---------------------------------------------------

def test_new_recipe_is_pending_missing(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    out = bd.pending(conn, targets)
    assert [(r["recipe_id"], r["reason"]) for r in out["recipes"]] == [(rid, "missing")]
    assert {i["name"] for i in out["recipes"][0]["ingredients"]} == {"eggs", "spinach"}


def test_imported_recipe_not_pending_until_edited(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    rep = bd.import_payload(conn, {"model": "m", "recipes": [_item(conn, targets, rid)]}, targets)
    assert rep.ok, rep.errors
    conn.commit()
    assert bd.pending(conn, targets)["recipes"] == []
    assert [r["reason"] for r in bd.pending(conn, targets, force=True)["recipes"]] == ["forced"]

    update_recipe(rid, base_servings=2, path=db_path)
    assert [r["reason"] for r in bd.pending(conn, targets)["recipes"]] == ["stale"]
    assert bd.recipe_nutrition(conn, rid, targets).stale


def test_ingredient_edit_makes_stale(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    bd.import_payload(conn, {"recipes": [_item(conn, targets, rid)]}, targets)
    conn.commit()
    ing_id = conn.execute("SELECT id FROM ingredients WHERE name='eggs'").fetchone()["id"]
    update_ingredient(ing_id, qty_per_serving=3, path=db_path)
    assert bd.recipe_nutrition(conn, rid, targets).stale


def test_missing_new_key_reported(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    item = _item(conn, targets, rid)
    bd.import_payload(conn, {"recipes": [item]}, targets)
    vals = dict(item["values"])
    del vals["choline"]
    conn.execute("UPDATE anny_recipe_nutrition SET values_json=? WHERE recipe_id=?",
                 (json.dumps(vals), rid))
    assert [r["reason"] for r in bd.pending(conn, targets)["recipes"]] == ["missing_keys"]


def test_ids_filter(conn, db_path, targets):
    a = _eggs_and_spinach(db_path)
    _eggs_and_spinach(db_path)
    assert [r["recipe_id"] for r in bd.pending(conn, targets, ids=[a])["recipes"]] == [a]


# --- import validation -----------------------------------------------------

def test_import_refuses_hash_mismatch(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    rep = bd.import_payload(conn, {"recipes": [_item(conn, targets, rid, ingredients_hash="x")]},
                            targets)
    assert not rep.ok
    assert "re-export" in rep.errors[0]
    assert conn.execute("SELECT count(*) FROM anny_recipe_nutrition").fetchone()[0] == 0


@pytest.mark.parametrize("mutate, msg", [
    (lambda v: v.pop("choline"), "missing keys"),
    (lambda v: v.update(veg_total=1), "unknown keys"),
    (lambda v: v.update(dairy=-1), "≥ 0"),
    (lambda v: v.update(dairy="1"), "≥ 0"),
    (lambda v: v.update(dairy=float("nan")), "≥ 0"),
])
def test_import_rejects_bad_values(conn, db_path, targets, mutate, msg):
    rid = _eggs_and_spinach(db_path)
    item = _item(conn, targets, rid)
    mutate(item["values"])
    rep = bd.import_payload(conn, {"recipes": [item]}, targets)
    assert any(msg in e for e in rep.errors), rep.errors


def test_import_rejects_bad_flag_and_confidence(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    item = _item(conn, targets, rid, safety_flags=["spicy"], confidence="certain")
    errs = bd.import_payload(conn, {"recipes": [item]}, targets).errors
    assert any("unknown codes" in e for e in errs)
    assert any("confidence" in e for e in errs)


def test_one_bad_item_blocks_whole_import(conn, db_path, targets):
    a = _eggs_and_spinach(db_path)
    good = _item(conn, targets, a)
    payload = {"recipes": [good, {**good, "recipe_id": 9999}]}
    rep = bd.import_payload(conn, payload, targets)
    assert not rep.ok and "no such recipe" in rep.errors[0]
    assert conn.execute("SELECT count(*) FROM anny_recipe_nutrition").fetchone()[0] == 0


def test_dry_run_writes_nothing(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    rep = bd.import_payload(conn, {"recipes": [_item(conn, targets, rid)]}, targets, dry_run=True)
    assert rep.ok and rep.recipes == 1
    assert conn.execute("SELECT count(*) FROM anny_recipe_nutrition").fetchone()[0] == 0


def test_reimport_replaces(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    bd.import_payload(conn, {"recipes": [_item(conn, targets, rid)]}, targets)
    item = _item(conn, targets, rid)
    item["values"]["dairy"] = 1.0
    bd.import_payload(conn, {"model": "m2", "recipes": [item]}, targets)
    n = bd.recipe_nutrition(conn, rid, targets)
    assert n.values["dairy"] == 1.0 and n.model == "m2"


# --- overrides -------------------------------------------------------------

def test_override_wins_and_survives_reimport(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    bd.import_payload(conn, {"recipes": [_item(conn, targets, rid)]}, targets)
    bd.set_override(conn, rid, "veg_dark_green", 1.0)
    bd.import_payload(conn, {"recipes": [_item(conn, targets, rid)]}, targets)
    n = bd.recipe_nutrition(conn, rid, targets)
    assert n.values["veg_dark_green"] == 1.0
    assert n.computed["veg_dark_green"] == 0.5
    assert n.stale_overrides == []


def test_override_flagged_after_recipe_edit(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    bd.set_override(conn, rid, "dairy", 0.5)
    conn.commit()
    update_recipe(rid, base_servings=3, path=db_path)
    n = bd.recipe_nutrition(conn, rid, targets)
    assert n.stale_overrides == ["dairy"]
    assert not n.has_breakdown and n.values["dairy"] == 0.5


def test_clear_override(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    bd.set_override(conn, rid, "dairy", 0.5)
    bd.set_override(conn, rid, "fruit", 0.5)
    bd.clear_override(conn, rid, "dairy")
    assert bd.recipe_nutrition(conn, rid, targets).overrides == {"fruit": 0.5}
    bd.clear_override(conn, rid)
    assert bd.recipe_nutrition(conn, rid, targets).overrides == {}


def test_no_breakdown_reads_as_zeros(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    n = bd.recipe_nutrition(conn, rid, targets)
    assert not n.has_breakdown and not n.stale
    assert set(n.values) == set(targets.value_keys()) and not any(n.values.values())


# --- staples + pending log -------------------------------------------------

def test_staples_upsert_by_name(conn, targets):
    s = {"name": "Greek yogurt, plain", "serving_label": "1 cup",
         "values": _values(targets, dairy=1.0, calcium=250), "confidence": "high"}
    assert bd.import_payload(conn, {"staples": [s]}, targets).ok
    s2 = {**s, "values": _values(targets, dairy=1.0, calcium=230)}
    assert bd.import_payload(conn, {"staples": [s2]}, targets).ok
    rows = conn.execute("SELECT name, values_json FROM anny_staples").fetchall()
    assert len(rows) == 1 and json.loads(rows[0]["values_json"])["calcium"] == 230


def test_staple_requires_serving_label(conn, targets):
    s = {"name": "Apple", "values": _values(targets, fruit=1.0), "confidence": "high"}
    assert "serving_label" in bd.import_payload(conn, {"staples": [s]}, targets).errors[0]


def _pending_log(conn, label="1 cup Greek yogurt") -> int:
    return conn.execute(
        "INSERT INTO anny_log (eaten_on, logged_at, kind, label) VALUES ('2026-10-20', 'now', 'pending', ?)",
        (label,),
    ).lastrowid


def test_pending_log_exported_and_filled(conn, targets):
    lid = _pending_log(conn)
    out = bd.pending(conn, targets)
    assert [(l["log_id"], l["label"]) for l in out["log"]] == [(lid, "1 cup Greek yogurt")]
    fill = {"log_id": lid, "values": _values(targets, dairy=1.0), "confidence": "med"}
    assert bd.import_payload(conn, {"log": [fill]}, targets).ok
    row = conn.execute("SELECT kind, values_json FROM anny_log WHERE id=?", (lid,)).fetchone()
    assert row["kind"] == "custom" and json.loads(row["values_json"])["dairy"] == 1.0
    assert bd.pending(conn, targets)["log"] == []


def test_log_fill_refuses_non_pending(conn, targets):
    lid = _pending_log(conn)
    conn.execute("UPDATE anny_log SET kind='custom' WHERE id=?", (lid,))
    fill = {"log_id": lid, "values": _values(targets), "confidence": "med"}
    assert "not pending" in bd.import_payload(conn, {"log": [fill]}, targets).errors[0]


def test_deleting_recipe_cascades(conn, db_path, targets):
    rid = _eggs_and_spinach(db_path)
    bd.import_payload(conn, {"recipes": [_item(conn, targets, rid)]}, targets)
    bd.set_override(conn, rid, "dairy", 1)
    conn.execute("DELETE FROM recipes WHERE id=?", (rid,))
    assert conn.execute("SELECT count(*) FROM anny_recipe_nutrition").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM anny_nutrition_override").fetchone()[0] == 0


def test_settings_roundtrip(conn):
    assert adb.get_setting(conn, "due_date") is None
    adb.set_setting(conn, "due_date", "2027-05-01")
    adb.set_setting(conn, "due_date", "2027-05-02")
    assert adb.get_setting(conn, "due_date") == "2027-05-02"
