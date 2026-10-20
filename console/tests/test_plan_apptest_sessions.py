"""Regression tests: two sessions on one recipe, in real Streamlit (PR #59 review).

These run the edit panel under streamlit.testing.v1.AppTest, because the
first bug lives in Streamlit itself, where the fake `st` can't reach it:

1. Grid edits were dropped after another session changed any ingredient.
   data_editor's widget id is built from its data, so a grid rebuilt from a
   fresh read that the other session had just changed got a new id, and this
   session's pending edit was thrown away with no warning.
2. Tag conflicts compared whole sets, so two people each adding a different
   tag at once "conflicted" and one tag was dropped.
3. A reload rerun fired from autosave before Done/Close/Delete rendered, so a
   click in the same commit (e.g. type a tag, click Done) was lost.

Sessions A and B are separate AppTests on one temp DB.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from meal_planner import db as _db
from meal_planner import queries
from meal_planner.db import init_db, insert_ingredient, insert_recipe


@pytest.fixture
def soup(tmp_path: Path, monkeypatch) -> int:
    p = tmp_path / "recipes.db"
    init_db(p)
    monkeypatch.setattr(_db, "DB_PATH", p)
    rid = insert_recipe(title="Soup", base_servings=4, instructions="boil", path=p)
    # Tied sort_order -> the grid lists them by name: pepper, salt.
    insert_ingredient(recipe_id=rid, name="salt", qty_per_serving=1.0, path=p)
    insert_ingredient(recipe_id=rid, name="pepper", qty_per_serving=1.0, path=p)
    return rid


@pytest.fixture
def session(tmp_path: Path, soup):
    """Factory: a browser session with the edit panel open on `soup`."""
    script = tmp_path / "edit_panel_app.py"
    script.write_text(textwrap.dedent(f"""
        import streamlit as st
        from console.tabs import plan
        if not st.session_state.get("_opened"):
            st.session_state["_opened"] = True
            st.session_state["_edit_recipe_id"] = {soup}
        if st.session_state.get("_edit_recipe_id") is not None:
            plan._render_edit_panel({soup})
    """))

    def open_session() -> AppTest:
        at = AppTest.from_file(str(script), default_timeout=30)
        at.run()
        assert not at.exception
        return at

    return open_session


def _edit_grid(at: AppTest, rid: int, edited_rows: dict) -> None:
    """Commit a grid edit the way the browser does.

    AppTest has no data_editor API in 1.57, so this sends the edit delta as
    the widget state under the grid's current id (private AppTest internals).
    """
    wid = at.session_state._state._key_id_mapper.get_id_from_key(f"edit_ingr_{rid}")
    states = at._tree.get_widget_states()
    delta = json.dumps({"edited_rows": edited_rows, "added_rows": [], "deleted_rows": []})
    for w in states.widgets:
        if w.id == wid:
            w.string_value = delta
            break
    else:
        w = states.widgets.add()
        w.id = wid
        w.string_value = delta
    at._run(widget_state=states)
    assert not at.exception


def _qty(rid: int) -> dict[str, float]:
    return {i.name: i.qty_per_serving for i in queries.list_ingredients(rid)}


# ---------------------------------------------------------------------------
# 1. Grid edits after another session's ingredient write
# ---------------------------------------------------------------------------

def test_grid_edit_survives_other_sessions_ingredient_write(soup, session):
    a = session()
    salt = next(i for i in queries.list_ingredients(soup) if i.name == "salt")
    queries.update_ingredient(salt.id, qty_per_serving=9.0)  # session B

    _edit_grid(a, soup, {"0": {"qty_per_serving": 5.0}})  # A: pepper, row 0

    assert _qty(soup) == {"pepper": 5.0, "salt": 9.0}
    a.run()
    shown = {r["name"]: r["qty_per_serving"] for r in a.session_state[f"_edit_ingr_rows_{soup}"]}
    assert shown == {"pepper": 5.0, "salt": 9.0}  # grid now shows both


def test_grid_same_ingredient_conflict_keeps_first_write_and_warns(soup, session):
    a = session()
    pepper = next(i for i in queries.list_ingredients(soup) if i.name == "pepper")
    queries.update_ingredient(pepper.id, qty_per_serving=9.0)  # session B

    _edit_grid(a, soup, {"0": {"qty_per_serving": 5.0}})

    assert _qty(soup) == {"pepper": 9.0, "salt": 1.0}
    assert any("ingredient pepper" in w.value for w in a.warning)


# ---------------------------------------------------------------------------
# 2. Tags added in both sessions at once
# ---------------------------------------------------------------------------

def test_both_sessions_add_different_tags(soup, session):
    a, b = session(), session()

    a.text_input(key=f"edit_new_tag_{soup}").set_value("spicy").run()
    b.text_input(key=f"edit_new_tag_{soup}").set_value("vegan").run()

    assert sorted(queries.get_recipe_tags(soup)) == ["spicy", "vegan"]
    assert not b.warning
    a.run()
    assert sorted(a.session_state[f"edit_tags_{soup}"]) == ["spicy", "vegan"]


# ---------------------------------------------------------------------------
# 3. Done in the same commit as an edit that reloads the panel
# ---------------------------------------------------------------------------

def test_done_after_typing_a_tag_closes_and_saves(soup, session):
    a = session()
    a.text_input(key=f"edit_new_tag_{soup}").set_value("spicy")
    a.button(key=f"save_{soup}").click().run()

    assert not a.exception
    assert "_edit_recipe_id" not in a.session_state
    assert queries.get_recipe_tags(soup) == ["spicy"]


def test_done_after_other_sessions_change_closes_and_saves(soup, session):
    a = session()
    queries.update_recipe(soup, title="Soup v2")  # session B
    a.text_area(key=f"edit_instructions_{soup}").set_value("simmer")
    a.button(key=f"save_{soup}").click().run()

    assert "_edit_recipe_id" not in a.session_state
    r = queries.get_recipe(soup)
    assert (r.title, r.instructions) == ("Soup v2", "simmer")


def test_done_with_a_conflict_stays_open_to_explain(soup, session):
    """Part of the edit wasn't saved: Done must not close over the notice."""
    a = session()
    queries.update_recipe(soup, title="Their Soup")  # session B
    a.text_input(key=f"edit_title_{soup}").set_value("My Soup")
    a.button(key=f"save_{soup}").click().run()

    assert a.session_state["_edit_recipe_id"] == soup
    assert any("another session" in w.value for w in a.warning)
    assert queries.get_recipe(soup).title == "Their Soup"
