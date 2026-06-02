"""Phase 21 — tests for meal_planner.runner.process_iphone_intake_sync.

Mocks Gemini + the Todoist sync helper so the test exercises every
intent branch and the shop_only rollback without touching the network.

In v2 (console upload), save_and_shop runs Todoist synchronously like
shop_only does — both paths block on the user-facing upload form."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from meal_planner import runner
from meal_planner.db import _SCHEMA, _add_column_if_missing, _get_conn
from meal_planner.vision.intake_db import get_by_sha, record_intake


_GOOD_PARSED = {
    "title": "Brown Butter Cookies",
    "ingredients": [
        {"qty": "2", "unit": "cup", "name": "flour"},
        {"qty": "1", "unit": "cup", "name": "butter"},
    ],
    "tags": ["dessert"],
}
_TEST_SHA = "abcd1234ef567890"


def _setup_db(tmp_path: Path) -> Path:
    db_p = tmp_path / "recipes.db"
    with _get_conn(db_p) as c:
        c.executescript(_SCHEMA)
        _add_column_if_missing(c, "photos_intake", "source", "TEXT")
    return db_p


def _setup_intake(intake_dir: Path, db_p: Path) -> Path:
    proc_dir = intake_dir / "_processing"
    proc_dir.mkdir(parents=True, exist_ok=True)
    (intake_dir / "_done").mkdir(parents=True, exist_ok=True)
    photo = proc_dir / f"{_TEST_SHA}.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
    record_intake(
        _TEST_SHA,
        source_path="capture-upload",
        nas_path=str(photo),
        source="iphone",
        path=db_p,
    )
    return photo


def _wire(monkeypatch, intake_dir: Path, db_p: Path, gemini_result):
    """Mock everything external: env, DB path, gemini call."""
    import meal_planner.db
    import meal_planner.vision.intake_db as idb

    monkeypatch.setenv("MEAL_PLANNER_IPHONE_INTAKE_DIR", str(intake_dir))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(meal_planner.db, "DB_PATH", db_p)
    monkeypatch.setattr(idb, "DB_PATH", db_p)
    monkeypatch.setattr(runner, "call_gemini_vision", lambda *a, **kw: gemini_result)


def _ok_metadata():
    return {"latency_s": 1.2, "http_status": 200, "raw_response": "ok", "eval_count": 50}


# ---------------------------------------------------------------------------
# Intent = save
# ---------------------------------------------------------------------------

def test_save_inserts_recipe_no_todoist(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    photo = _setup_intake(intake_dir, db_p)

    sends: list = []
    monkeypatch.setattr(
        runner, "send_recipes_to_todoist_sync",
        lambda scales: sends.append(scales) or {"items_sent": 99, "items_attempted": 99, "error": None},
    )
    _wire(monkeypatch, intake_dir, db_p, (_GOOD_PARSED, _ok_metadata()))

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "save")

    assert ret["status"] == "ok"
    assert ret["intent"] == "save"
    assert ret["recipe_id"] is not None
    assert sends == []  # no Todoist send for plain "save"

    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM recipes WHERE id=?", (ret["recipe_id"],)).fetchone()
    assert row["title"] == "Brown Butter Cookies"
    assert row["source"] == "iphone"
    assert row["photo_path"].endswith(f"_done/{_TEST_SHA}.jpg")

    ings = conn.execute("SELECT name FROM ingredients WHERE recipe_id=?", (row["id"],)).fetchall()
    assert {i["name"] for i in ings} == {"flour", "butter"}
    conn.close()

    assert not photo.exists()
    assert (intake_dir / "_done" / f"{_TEST_SHA}.jpg").exists()
    assert (intake_dir / "_done" / f"{_TEST_SHA}.json").exists()

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "ok"
    assert db_row.extraction_path == "gemini"


# ---------------------------------------------------------------------------
# Intent = save_and_shop (v2: synchronous Todoist push, NOT fire-and-forget)
# ---------------------------------------------------------------------------

def test_save_and_shop_inserts_and_sends_sync(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    _setup_intake(intake_dir, db_p)

    sends: list = []

    def _fake_sync_send(scales):
        sends.append(scales)
        return {"items_sent": 5, "items_attempted": 5, "error": None}

    monkeypatch.setattr(runner, "send_recipes_to_todoist_sync", _fake_sync_send)
    _wire(monkeypatch, intake_dir, db_p, (_GOOD_PARSED, _ok_metadata()))

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "save_and_shop", 6)
    assert ret["status"] == "ok"
    assert ret["intent"] == "save_and_shop"
    assert ret["recipe_id"] is not None
    assert ret["items_sent"] == 5
    assert sends == [[[ret["recipe_id"], 6]]]

    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM recipes WHERE id=?", (ret["recipe_id"],)).fetchone()
    assert row["source"] == "iphone"  # NOT iphone-shop-only; recipe stays
    conn.close()


def test_save_and_shop_keeps_recipe_when_todoist_fails(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    _setup_intake(intake_dir, db_p)

    monkeypatch.setattr(
        runner, "send_recipes_to_todoist_sync",
        lambda scales: {"items_sent": 0, "items_attempted": 5, "error": "todoist 500"},
    )
    _wire(monkeypatch, intake_dir, db_p, (_GOOD_PARSED, _ok_metadata()))

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "save_and_shop")
    assert ret["status"] == "todoist_failed"
    assert ret["recipe_id"] is not None  # recipe persisted

    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT source FROM recipes WHERE id=?", (ret["recipe_id"],)).fetchone()
    assert row["source"] == "iphone"
    conn.close()


# ---------------------------------------------------------------------------
# Intent = shop_only
# ---------------------------------------------------------------------------

def test_shop_only_sends_then_deletes(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    _setup_intake(intake_dir, db_p)

    sends: list = []

    def _fake_sync_send(scales):
        sends.append(scales)
        return {"items_sent": 3, "items_attempted": 3, "error": None}

    monkeypatch.setattr(runner, "send_recipes_to_todoist_sync", _fake_sync_send)
    _wire(monkeypatch, intake_dir, db_p, (_GOOD_PARSED, _ok_metadata()))

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "shop_only", 4)
    assert ret["status"] == "ok"
    assert ret["intent"] == "shop_only"
    assert ret["recipe_id"] is None  # cleared after delete
    assert ret["items_sent"] == 3
    assert len(sends) == 1
    sent_recipe_id = sends[0][0][0]

    conn = sqlite3.connect(str(db_p))
    row = conn.execute("SELECT id FROM recipes WHERE id=?", (sent_recipe_id,)).fetchone()
    assert row is None
    ings = conn.execute(
        "SELECT name FROM ingredients WHERE recipe_id=?", (sent_recipe_id,)
    ).fetchall()
    assert ings == []  # FK cascade cleaned up
    conn.close()

    assert (intake_dir / "_done" / f"{_TEST_SHA}.json").exists()


def test_shop_only_keeps_recipe_when_todoist_fails(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    _setup_intake(intake_dir, db_p)

    monkeypatch.setattr(
        runner, "send_recipes_to_todoist_sync",
        lambda scales: {"items_sent": 0, "items_attempted": 5, "error": "todoist 500"},
    )
    _wire(monkeypatch, intake_dir, db_p, (_GOOD_PARSED, _ok_metadata()))

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "shop_only")
    assert ret["status"] == "todoist_failed"
    assert ret["recipe_id"] is not None  # NOT deleted — user can retry

    conn = sqlite3.connect(str(db_p))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, source FROM recipes WHERE id=?", (ret["recipe_id"],)
    ).fetchone()
    assert row is not None
    assert row["source"] == "iphone-shop-only"
    conn.close()


def test_shop_only_keeps_recipe_when_todoist_crashes(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    _setup_intake(intake_dir, db_p)

    def _boom(scales):
        raise RuntimeError("todoist exploded")

    monkeypatch.setattr(runner, "send_recipes_to_todoist_sync", _boom)
    _wire(monkeypatch, intake_dir, db_p, (_GOOD_PARSED, _ok_metadata()))

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "shop_only")
    assert ret["status"] == "todoist_failed"
    assert "todoist exploded" in ret["error"]
    assert ret["recipe_id"] is not None

    conn = sqlite3.connect(str(db_p))
    row = conn.execute("SELECT id FROM recipes WHERE id=?", (ret["recipe_id"],)).fetchone()
    assert row is not None
    conn.close()


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_gemini_failure_marks_status_and_no_recipe(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    photo = _setup_intake(intake_dir, db_p)
    _wire(
        monkeypatch, intake_dir, db_p,
        (None, {"latency_s": 0.1, "http_status": 500, "raw_response": "HTTP 500: ...", "eval_count": None}),
    )

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "save")
    assert ret["status"] == "ollama_error"
    assert ret["recipe_id"] is None
    assert photo.exists()  # file stays in _processing/

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "ollama_error"


def test_gemini_timeout_marks_status_timeout(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    _setup_intake(intake_dir, db_p)
    _wire(
        monkeypatch, intake_dir, db_p,
        (None, {"latency_s": 60.0, "http_status": None, "raw_response": "Read timed out", "eval_count": None}),
    )

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "save")
    assert ret["status"] == "timeout"

    db_row = get_by_sha(_TEST_SHA, db_path=db_p)
    assert db_row.status == "timeout"


def test_missing_api_key(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    _setup_intake(intake_dir, db_p)
    monkeypatch.setenv("MEAL_PLANNER_IPHONE_INTAKE_DIR", str(intake_dir))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    import meal_planner.db
    import meal_planner.vision.intake_db as idb
    monkeypatch.setattr(meal_planner.db, "DB_PATH", db_p)
    monkeypatch.setattr(idb, "DB_PATH", db_p)

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "save")
    assert ret["status"] == "config_error"
    assert "GEMINI_API_KEY" in ret["error"]


def test_bad_intent_raises(tmp_path, monkeypatch):
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    _setup_intake(intake_dir, db_p)
    _wire(monkeypatch, intake_dir, db_p, (_GOOD_PARSED, _ok_metadata()))

    with pytest.raises(ValueError, match="bad intent"):
        runner.process_iphone_intake_sync(_TEST_SHA, "wrong")


def test_already_handled_is_skipped(tmp_path, monkeypatch):
    """Non-pending intake row → kind no-ops (idempotent re-run safety)."""
    db_p = _setup_db(tmp_path)
    intake_dir = tmp_path / "iphone-intake"
    _setup_intake(intake_dir, db_p)
    from meal_planner.vision.intake_db import mark_status
    mark_status(_TEST_SHA, "ok", db_path=db_p)

    _wire(monkeypatch, intake_dir, db_p, (_GOOD_PARSED, _ok_metadata()))

    ret = runner.process_iphone_intake_sync(_TEST_SHA, "save")
    assert ret["status"] == "skipped_already_handled"


# ---------------------------------------------------------------------------
# Skip section — household staples stay on the recipe but aren't sent to Todoist
# ---------------------------------------------------------------------------


def test_send_skips_staple_ingredients(tmp_path, monkeypatch):
    """Ingredients in the 'Skip' section are not sent; others still are."""
    import json
    import meal_planner.db
    from meal_planner.db import _get_conn, insert_recipe

    db_p = _setup_db(tmp_path)
    monkeypatch.setattr(meal_planner.db, "DB_PATH", db_p)

    with _get_conn(db_p) as c:
        rid = insert_recipe(title="Test Soup", source="t", conn=c)
        rows = [("chicken", "Meats"), ("salt", "Skip"), ("carrots", "Fruits + Veggies")]
        for i, (name, sec) in enumerate(rows):
            c.execute(
                "INSERT INTO ingredients (recipe_id, name, qty_per_serving, unit, "
                "notes, todoist_section, sort_order) VALUES (?,?,?,?,?,?,?)",
                (rid, name, 1.0, "", None, sec, i),
            )
        c.commit()

    monkeypatch.setenv(
        "TODOIST_SECTIONS",
        json.dumps({"Meals": "m", "Meats": "mt", "Fruits + Veggies": "fv"}),
    )
    monkeypatch.delenv("TODOIST_PROJECT_ID", raising=False)

    sent_titles: list[str] = []

    def _fake_create(output_config, payload):
        sent_titles.append(payload["title"])
        return {"created": True}

    monkeypatch.setattr(runner.todoist_adapter, "create_task", _fake_create)

    res = runner.send_recipes_to_todoist_sync([[rid, 4]])

    assert res["items_skipped"] == 1
    assert not any("salt" in t for t in sent_titles), "staple must not be sent"
    assert any("chicken" in t for t in sent_titles)
    assert any("carrots" in t for t in sent_titles)
    assert res["items_sent"] == 3       # header + chicken + carrots
    assert res["items_attempted"] == 3  # salt was skipped, never attempted
