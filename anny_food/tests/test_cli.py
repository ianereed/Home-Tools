import io
import json
from pathlib import Path

import pytest

from anny_food import cli
from anny_food import db as adb
from meal_planner.db import insert_ingredient, insert_recipe


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "recipes.db"
    adb.init_db(p)
    rid = insert_recipe(title="Eggs + spinach", base_servings=1, path=p)
    insert_ingredient(recipe_id=rid, name="eggs", qty_per_serving=2, path=p)
    return p


def _export(db_path, capsys) -> dict:
    assert cli.main(["--db", str(db_path), "export-pending"]) == 0
    return json.loads(capsys.readouterr().out)


def _breakdown(exported: dict) -> dict:
    r = exported["recipes"][0]
    values = {k: 0 for k in exported["value_keys"]}
    values.update(protein_meat_eggs=2, choline=300)
    return {"model": "test", "recipes": [{
        "recipe_id": r["recipe_id"], "ingredients_hash": r["ingredients_hash"],
        "values": values, "confidence": "high",
        "breakdown": [{"ingredient": "eggs", "amount": "2 large", "key": "protein_meat_eggs", "eq": 2}],
        "safety_flags": ["raw_egg"], "notes": "soft scrambled",
    }]}


def test_export_import_show_roundtrip(db_path, tmp_path, capsys, monkeypatch):
    exported = _export(db_path, capsys)
    assert exported["recipes"][0]["reason"] == "missing"
    f = tmp_path / "b.json"
    f.write_text(json.dumps(_breakdown(exported)))

    assert cli.main(["--db", str(db_path), "import", str(f), "--dry-run"]) == 0
    assert "dry run OK" in capsys.readouterr().out
    assert _export(db_path, capsys)["recipes"], "dry run must not write"

    monkeypatch.setattr("sys.stdin", io.StringIO(f.read_text()))
    assert cli.main(["--db", str(db_path), "import", "-"]) == 0
    assert "imported 1 recipe" in capsys.readouterr().out
    assert _export(db_path, capsys)["recipes"] == []

    assert cli.main(["--db", str(db_path), "show", str(exported["recipes"][0]["recipe_id"])]) == 0
    out = capsys.readouterr().out
    assert "current; confidence high" in out
    assert "eggs: 2 large → protein_meat_eggs 2" in out
    assert "Raw or undercooked egg" in out


def test_import_errors_exit_nonzero(db_path, tmp_path, capsys):
    exported = _export(db_path, capsys)
    payload = _breakdown(exported)
    payload["recipes"][0]["ingredients_hash"] = "stale"
    f = tmp_path / "b.json"
    f.write_text(json.dumps(payload))
    assert cli.main(["--db", str(db_path), "import", str(f)]) == 1
    assert "nothing imported" in capsys.readouterr().err


def test_show_unknown_recipe(db_path, capsys):
    assert cli.main(["--db", str(db_path), "show", "999"]) == 1
