"""Regression tests: two people editing one recipe at once (review, 2026-10-20).

1. Autosave reverted the other session's edits. The edit widgets are keyed, so
   they keep the values loaded when the panel opened; _edit_is_dirty compared
   them against a fresh DB read, so any change from another session made this
   one "dirty" and _persist_recipe wrote every field and the whole tag set
   back. app.py uses st.tabs, so any click in any tab triggered it.
2. A second new tag deleted the first. The "Add new tag" text was saved but
   never moved into the pills state, so the next tag typed in the box
   replaced it in the saved set.

_BrowserSt plays the browser too: each run starts from the widget values the
browser shows, and the browser only takes a value from the server when the
app assigned the key that run. That's the Streamlit behavior the fix depends
on — popping a scalar widget's key does not reload it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from console.tests.test_plan_new_recipe_ingredients import _GridSt
from meal_planner import db as _db
from meal_planner import queries
from meal_planner.db import init_db, insert_ingredient, insert_recipe

_NO_EDITS = {"edited_rows": {}, "added_rows": [], "deleted_rows": []}


class _ScriptRerun(BaseException):
    """st.rerun(). A BaseException like Streamlit's RerunException, so the
    app's ``except Exception`` blocks let it through."""


class _AppState(dict):
    """session_state that records which keys the app assigned this run."""

    def __init__(self):
        super().__init__()
        self.assigned: set = set()

    def __setitem__(self, k, v):
        self.assigned.add(k)
        super().__setitem__(k, v)


class _BrowserSt(_GridSt):
    """Fake st plus the browser on the other end of it.

    ``frontend`` is what each keyed widget shows. A test edits a field by
    changing it, then calls run().
    """

    def __init__(self):
        super().__init__({}, _NO_EDITS)
        self.session_state = _AppState()
        self.frontend: dict = {}
        self.warnings: list[str] = []
        self.buttons_clicked: set[str] = set()

    def run(self, fn, *args, delta: dict | None = None) -> bool:
        """One script run. Returns True if it ended in st.rerun()."""
        for k, v in self.frontend.items():  # widget states sent with the rerun
            dict.__setitem__(self.session_state, k, v)
        self.session_state.assigned.clear()
        self.delta = delta or _NO_EDITS
        self.rerun_called = False
        self.warnings = []
        try:
            fn(*args)
        except _ScriptRerun:
            return True
        return False

    def _widget(self, key, default):
        if key in self.session_state:
            value = self.session_state[key]
        else:
            value = default
            dict.__setitem__(self.session_state, key, value)
        if key in self.session_state.assigned or key not in self.frontend:
            self.frontend[key] = value
        return value

    def text_input(self, label, value="", key=None, **k):
        return self._widget(key, value)

    def number_input(self, label, *, value=0, key=None, **k):
        return self._widget(key, value)

    def text_area(self, label, value="", key=None, **k):
        return self._widget(key, value)

    def pills(self, label, *, options=None, default=None, key=None, **k):
        return self._widget(key, list(default or []))

    def warning(self, msg, *a, **k):
        self.warnings.append(msg)

    def button(self, label, *a, key=None, **k):
        return (key or label) in self.buttons_clicked

    def rerun(self, *a, **k):
        self.rerun_called = True
        raise _ScriptRerun


@pytest.fixture
def soup(tmp_path: Path, monkeypatch) -> int:
    p = tmp_path / "recipes.db"
    init_db(p)
    monkeypatch.setattr(_db, "DB_PATH", p)
    rid = insert_recipe(title="Soup", base_servings=4, instructions="Boil it", path=p)
    insert_ingredient(recipe_id=rid, name="apple", qty_per_serving=1.0, sort_order=0, path=p)
    insert_ingredient(recipe_id=rid, name="banana", qty_per_serving=1.0, sort_order=0, path=p)
    return rid


@pytest.fixture
def me(monkeypatch) -> _BrowserSt:
    from console.tabs import plan

    fake = _BrowserSt()
    monkeypatch.setattr(plan, "st", fake)
    return fake


def _panel(me: _BrowserSt, rid: int, **kw) -> bool:
    from console.tabs import plan

    return me.run(plan._render_edit_panel, rid, **kw)


# ---------------------------------------------------------------------------
# 1. Cross-session autosave revert
# ---------------------------------------------------------------------------

def test_other_sessions_edit_is_not_reverted(soup, me):
    _panel(me, soup)  # this session opens the panel
    queries.update_recipe(soup, instructions="Simmer gently")  # the other one saves

    _panel(me, soup)  # any rerun here, e.g. a click in another tab

    assert queries.get_recipe(soup).instructions == "Simmer gently"
    _panel(me, soup)
    assert me.frontend[f"edit_instructions_{soup}"] == "Simmer gently"


def test_other_sessions_tags_are_not_reverted(soup, me):
    _panel(me, soup)
    queries.set_recipe_tags(soup, ["dinner"])

    _panel(me, soup)

    assert queries.get_recipe_tags(soup) == ["dinner"]


def test_edits_to_different_fields_both_survive(soup, me):
    _panel(me, soup)
    queries.update_recipe(soup, instructions="Simmer gently")
    me.frontend[f"edit_title_{soup}"] = "Soup Deluxe"

    _panel(me, soup)

    r = queries.get_recipe(soup)
    assert (r.title, r.instructions) == ("Soup Deluxe", "Simmer gently")
    _panel(me, soup)  # reloaded: the form shows both
    assert me.frontend[f"edit_title_{soup}"] == "Soup Deluxe"
    assert me.frontend[f"edit_instructions_{soup}"] == "Simmer gently"
    # Only the field the other session changed is pushed to the browser, so
    # a field this user is typing in right now isn't overwritten.
    assert f"edit_instructions_{soup}" in me.session_state.assigned
    assert f"edit_title_{soup}" not in me.session_state.assigned


def test_same_field_conflict_keeps_first_write_and_warns(soup, me):
    _panel(me, soup)
    queries.update_recipe(soup, title="Their Soup")
    me.frontend[f"edit_title_{soup}"] = "My Soup"
    me.frontend[f"edit_source_{soup}"] = "NYT"  # no conflict: still saved

    assert _panel(me, soup) is True  # reload

    r = queries.get_recipe(soup)
    assert (r.title, r.source) == ("Their Soup", "NYT")
    _panel(me, soup)
    assert me.frontend[f"edit_title_{soup}"] == "Their Soup"
    assert any("another session" in w for w in me.warnings)


def test_write_between_read_and_save_is_rejected(soup, me, monkeypatch):
    """The updated_at guard: a save landing mid-run must not be overwritten."""
    _panel(me, soup)
    me.frontend[f"edit_instructions_{soup}"] = "Mine"

    real = queries.list_ingredients

    def list_then_other_session_saves(rid, **kw):
        rows = real(rid, **kw)
        monkeypatch.setattr(queries, "list_ingredients", real)
        queries.update_recipe(rid, instructions="Theirs")
        return rows

    monkeypatch.setattr(queries, "list_ingredients", list_then_other_session_saves)
    assert _panel(me, soup) is True

    assert queries.get_recipe(soup).instructions == "Theirs"
    _panel(me, soup)
    assert me.frontend[f"edit_instructions_{soup}"] == "Theirs"
    assert any("another session" in w for w in me.warnings)


def test_vanished_ingredient_reloads_instead_of_closing(soup, me, monkeypatch):
    """A KeyError for an ingredient used to be reported as "recipe deleted"."""
    me.session_state["_edit_recipe_id"] = soup
    _panel(me, soup)
    apple = queries.list_ingredients(soup)[0]
    real = queries.list_ingredients

    def list_then_row_vanishes(rid, **kw):
        rows = real(rid, **kw)
        monkeypatch.setattr(queries, "list_ingredients", real)
        # A writer that doesn't bump recipes.updated_at, so only the
        # ingredient update itself can notice.
        with sqlite3.connect(_db.DB_PATH) as c:
            c.execute("DELETE FROM ingredients WHERE id = ?", (apple.id,))
        return rows

    monkeypatch.setattr(queries, "list_ingredients", list_then_row_vanishes)
    edit = {"edited_rows": {0: {"qty_per_serving": 5.0}}, "added_rows": [], "deleted_rows": []}

    assert _panel(me, soup, delta=edit) is True

    assert me.session_state.get("_edit_recipe_id") == soup  # panel still open
    assert me.session_state.get(f"_edit_reload_{soup}")  # grid + form reload next run
    assert queries.get_recipe(soup).title == "Soup"


def test_unchanged_rerun_writes_nothing(soup, me):
    _panel(me, soup)
    before = queries.get_recipe(soup).updated_at

    assert _panel(me, soup) is False
    assert _panel(me, soup) is False

    assert queries.get_recipe(soup).updated_at == before


# ---------------------------------------------------------------------------
# 2. Second new tag deletes the first
# ---------------------------------------------------------------------------

def test_second_new_tag_keeps_the_first(soup, me):
    _panel(me, soup)
    me.frontend[f"edit_new_tag_{soup}"] = "spicy"
    _panel(me, soup)
    assert queries.get_recipe_tags(soup) == ["spicy"]

    _panel(me, soup)
    assert me.frontend[f"edit_tags_{soup}"] == ["spicy"]
    assert me.frontend[f"edit_new_tag_{soup}"] == ""

    me.frontend[f"edit_new_tag_{soup}"] = "vegan"
    _panel(me, soup)

    assert sorted(queries.get_recipe_tags(soup)) == ["spicy", "vegan"]
