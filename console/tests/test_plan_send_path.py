"""Regression tests: the Recipes tab's grid, Send button and job status (review, 2026-10-20).

- Send crashed on a cleared Servings cell: int(NaN), outside the try.
- Send could be clicked again while its job ran, enqueueing the list twice.
- The job's result message vanished on the status fragment's next 2 s tick.
- send_recipes_to_todoist_sync created Todoist tasks recipe by recipe, so a
  recipe deleted since it was checked left the earlier headers behind.
- "+ New recipe" stubs abandoned by a dropped session or a double-click
  stayed in the list forever.

_render_inner runs against a temp DB with the grid edited through
Streamlit's own _apply_dataframe_edits.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from console.tests.test_plan_concurrent_edits import _BrowserSt
from meal_planner import db as _db
from meal_planner import queries
from meal_planner.db import init_db, insert_ingredient, insert_recipe

SEND = "Send checked recipes to Todoist"


class _TabSt(_BrowserSt):
    """_BrowserSt plus the rest of what the Recipes tab draws."""

    def __init__(self):
        super().__init__()
        self.disabled: dict[str, bool] = {}
        self.shown: list[tuple[str, str]] = []

    def button(self, label, *a, key=None, disabled=False, **k):
        self.disabled[key or label] = disabled
        return (key or label) in self.buttons_clicked

    def toggle(self, label, value=False, key=None, **k):
        return self._widget(key, value)

    def radio(self, label, options, index=0, **k):
        return options[index]

    def divider(self):
        pass

    def info(self, msg, *a, **k):
        self.shown.append(("info", msg))

    def success(self, msg, *a, **k):
        self.shown.append(("success", msg))

    def error(self, msg, *a, **k):
        self.shown.append(("error", msg))

    def warning(self, msg, *a, **k):
        self.warnings.append(msg)
        self.shown.append(("warning", msg))


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "recipes.db"
    init_db(p)
    monkeypatch.setattr(_db, "DB_PATH", p)
    return p


@pytest.fixture
def tab(monkeypatch) -> _TabSt:
    from console.tabs import plan

    fake = _TabSt()
    fake.session_state["_stub_sweep_done"] = True
    monkeypatch.setattr(plan, "st", fake)
    return fake


@pytest.fixture
def enqueued(monkeypatch) -> list:
    from console.tabs import plan

    calls: list = []
    monkeypatch.setattr(
        plan._jobs_client, "enqueue", lambda kind, args=None: calls.append((kind, args)) or "task-1",
    )
    return calls


def _check_first_row(**cells) -> dict:
    return {"edited_rows": {0: {"Send": True, **cells}}, "added_rows": [], "deleted_rows": []}


def _render_tab(tab: _TabSt, delta: dict) -> bool:
    from console.tabs import plan

    return tab.run(plan._render_inner, delta=delta)


# ---------------------------------------------------------------------------
# Servings cleared in the grid
# ---------------------------------------------------------------------------

def test_send_with_cleared_servings_uses_base_servings(db, tab, enqueued):
    rid = insert_recipe(title="Soup", base_servings=6, path=db)
    tab.buttons_clicked = {SEND}

    assert _render_tab(tab, _check_first_row(Servings=None)) is True

    assert enqueued == [("meal_planner_send_to_todoist", {"recipe_scales": [[rid, 6]]})]
    assert "Servings was blank" in " ".join(tab.session_state["_send_job"]["notes"])


def test_send_keeps_edited_servings(db, tab, enqueued):
    rid = insert_recipe(title="Soup", base_servings=6, path=db)
    tab.buttons_clicked = {SEND}

    _render_tab(tab, _check_first_row(Servings=2))

    assert enqueued[0][1] == {"recipe_scales": [[rid, 2]]}
    assert tab.session_state["_send_job"]["notes"] == []


# ---------------------------------------------------------------------------
# Double send
# ---------------------------------------------------------------------------

def test_send_disabled_while_job_runs(db, tab, enqueued):
    insert_recipe(title="Soup", path=db)
    tab.session_state["_send_job"] = {"task_id": "task-0", "started_at": 0.0}
    tab.buttons_clicked = {SEND}  # a click that beat the button greying out

    _render_tab(tab, _check_first_row())

    assert tab.disabled[SEND] is True
    assert enqueued == []


# ---------------------------------------------------------------------------
# Job result stays until dismissed
# ---------------------------------------------------------------------------

def test_job_result_stays_until_dismissed(tab, monkeypatch):
    from console.tabs import plan

    tab.session_state["_send_job"] = {"task_id": "task-1", "started_at": 0.0, "notes": []}
    monkeypatch.setattr(plan._jobs_client, "result", lambda task_id: {
        "items_sent": 3, "items_attempted": 3, "consolidate_failed": None,
        "consolidate_dropped": 0, "error": None,
    })

    # Completion reruns the app so the Send button comes back.
    assert tab.run(plan._job_status_body, "_send_job", "Send to Todoist") is True
    assert "_send_job" not in tab.session_state

    for _tick in range(3):  # the fragment's 2 s reruns
        tab.shown.clear()
        tab.run(plan._job_status_body, "_send_job", "Send to Todoist")
        assert tab.shown == [("success", "Send to Todoist: 3/3 items")]

    tab.buttons_clicked = {"_send_job_dismiss"}
    assert tab.run(plan._job_status_body, "_send_job", "Send to Todoist") is True
    tab.buttons_clicked = set()
    tab.shown.clear()
    tab.run(plan._job_status_body, "_send_job", "Send to Todoist")
    assert tab.shown == []


# ---------------------------------------------------------------------------
# A job that never reports back must not lock Send for the whole session
# ---------------------------------------------------------------------------
# jobs_client.result() returns None ("pending") while the job runs, and also
# when jobs-http or the consumer is down or the job was lost.

_PAST_GIVE_UP = 301  # s; the console gives up on a job after 5 min


def _pending_job(tab, monkeypatch, age_s: float):
    import time

    from console.tabs import plan

    tab.session_state["_send_job"] = {
        "task_id": "task-1", "started_at": time.monotonic() - age_s, "notes": [],
    }
    monkeypatch.setattr(plan._jobs_client, "result", lambda task_id: None)
    return plan


def test_young_pending_job_offers_no_stop(tab, monkeypatch):
    plan = _pending_job(tab, monkeypatch, age_s=5)

    assert tab.run(plan._job_status_body, "_send_job", "Send to Todoist") is False

    assert "_send_job_stop" not in tab.disabled
    assert "_send_job" in tab.session_state


def test_stop_waiting_unlocks_send(tab, monkeypatch):
    plan = _pending_job(tab, monkeypatch, age_s=90)
    tab.buttons_clicked = {"_send_job_stop"}

    assert tab.run(plan._job_status_body, "_send_job", "Send to Todoist") is True

    assert "_send_job" not in tab.session_state
    level, message = tab.session_state["_send_job_done"]
    assert level == "warning" and "Check Todoist" in message


def test_pending_job_gives_up_after_timeout(tab, monkeypatch):
    plan = _pending_job(tab, monkeypatch, age_s=_PAST_GIVE_UP)

    assert tab.run(plan._job_status_body, "_send_job", "Send to Todoist") is True

    assert "_send_job" not in tab.session_state
    assert "no result after 5 min" in tab.session_state["_send_job_done"][1]


def test_send_enabled_again_after_giving_up(db, tab, monkeypatch, enqueued):
    insert_recipe(title="Soup", path=db)
    plan = _pending_job(tab, monkeypatch, age_s=_PAST_GIVE_UP)
    tab.run(plan._job_status_body, "_send_job", "Send to Todoist")
    tab.buttons_clicked = {SEND}

    _render_tab(tab, _check_first_row())

    assert tab.disabled[SEND] is False
    assert len(enqueued) == 1


# ---------------------------------------------------------------------------
# Runner: load every recipe before creating any task
# ---------------------------------------------------------------------------

def test_send_with_a_deleted_recipe_creates_no_tasks(db, monkeypatch):
    from meal_planner import runner

    rid = insert_recipe(title="Soup", base_servings=4, path=db)
    insert_ingredient(recipe_id=rid, name="carrot", qty_per_serving=1.0, path=db)
    gone = insert_recipe(title="Stew", path=db)
    queries.delete_recipe(gone)
    monkeypatch.setenv("TODOIST_SECTIONS", json.dumps({"Meals": "s1", "Shelf-stable": "s2"}))
    created: list = []
    monkeypatch.setattr(
        runner.todoist_adapter, "create_task",
        lambda **kw: created.append(kw) or {"created": True},
    )

    with pytest.raises(KeyError):
        runner.send_recipes_to_todoist_sync([[rid, 4], [gone, 4]])

    assert created == []


# ---------------------------------------------------------------------------
# Orphan "New Recipe" stubs
# ---------------------------------------------------------------------------

def test_stub_sweep_runs_once_per_session(db, tab, monkeypatch):
    calls: list = []
    monkeypatch.setattr(queries, "delete_untouched_stub_recipes", lambda **kw: calls.append(kw) or 0)
    insert_recipe(title="Soup", path=db)
    del tab.session_state["_stub_sweep_done"]

    _render_tab(tab, _check_first_row())
    _render_tab(tab, _check_first_row())

    assert len(calls) == 1


def test_new_recipe_sweeps_stale_stubs_first(db, tab):
    import sqlite3

    stale = queries.create_recipe(title="New Recipe")
    with sqlite3.connect(db) as c:
        c.execute(
            "UPDATE recipes SET created_at = ?, updated_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", stale),
        )
    tab.buttons_clicked = {"+ New recipe"}

    assert _render_tab(tab, _check_first_row()) is True

    # Only the new stub is left (SQLite may hand it the swept row's id).
    (left,) = queries.list_recipes()
    assert left.id == tab.session_state["_new_recipe_id"]
    assert not left.created_at.startswith("2026-01-01")
