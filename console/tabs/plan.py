"""Recipes tab — multi-recipe grid with Send-to-Todoist (Phase 14.9).

Renders an editable grid of all recipes. Each row has a Send checkbox and a
Servings input. Clicking "Send checked recipes to Todoist" consolidates the
selected recipes via Gemini and creates one Todoist task per grocery line.

Phase 18 A2 adds inline recipe editing: check exactly one row and click
"Edit selected" to expand a form with fields, editable ingredient sub-grid,
and tag selector. "+ New recipe" creates a blank recipe row then redirects
to edit-mode. "Delete this recipe" uses a two-click confirm (10 s TTL).
"""
from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from console import jobs_client as _jobs_client
from console.tabs._recipe_form import (
    clean_optional_str,
    diff_ingredients,
    format_view_block,
    ingredients_to_rows,
    nan_to_none,
    normalize_tags,
    validate_recipe_form,
)
from meal_planner import db as _db
from meal_planner import queries
from meal_planner.tag_categories import CATEGORY_MAP, _partition_tags_by_category

from console.tabs._job_status import (
    _format_status,
    _read_result_or_synthesize_error,
)

_CONFIRM_CLEAR_TTL = 10  # seconds before the confirm state resets
_CONFIRM_DELETE_TTL = 10  # seconds before the delete confirm state resets

# Tracks recipe IDs created by "+ New recipe" that have not yet been saved.
# Cancel on a pending recipe deletes the stub row instead of leaving an orphan.
_PENDING_KEY = "_new_recipe_pending_ids"


def _pending_ids() -> set[int]:
    if _PENDING_KEY not in st.session_state:
        st.session_state[_PENDING_KEY] = set()
    return st.session_state[_PENDING_KEY]  # type: ignore[return-value]

# Widget key prefixes for the edit panel. Streamlit ignores `value=` /
# `default=` on re-render when `key=` is already in session_state, so leaving
# these populated would silently overwrite the DB on the next save (and hide
# concurrent writes from another browser session). Pop all of them on every
# panel-close path.
_EDIT_WIDGET_KEY_PREFIXES = (
    "edit_title",
    "edit_servings",
    "edit_instructions",
    "edit_cook_time",
    "edit_source",
    "edit_recipe_book",
    "edit_tags",
    "edit_new_tag",
    "edit_ingr",
)

__all__ = ["render", "_format_status", "_read_result_or_synthesize_error"]


def render() -> None:
    try:
        _render_inner()
    except Exception as exc:
        st.error("Plan tab error — see traceback below")
        st.exception(exc)


@st.fragment(run_every="2s")
def _render_job_status(state_key: str, label: str) -> None:
    """Poll jobs_client for the job stored at session_state[state_key].

    Renders a spinner while pending; renders terminal status (success/warning/
    error) with the result-dict summary when complete. Clears state_key on
    terminal render so the fragment stops re-running.
    """
    state = st.session_state.get(state_key)
    if not state:
        return
    task_id = state["task_id"]
    started_at = state["started_at"]
    result = _read_result_or_synthesize_error(_jobs_client.result, task_id)
    if result is None:
        elapsed = int(time.monotonic() - started_at)
        st.info(f"{label}… ({elapsed}s)", icon="⏳")
        return
    # terminal — render and clear
    del st.session_state[state_key]
    level, message = _format_status(result)
    if level == "success":
        st.success(f"{label}: {message}")
    elif level == "warning":
        st.warning(f"{label}: {message}")
    else:
        st.error(f"{label}: {message}")


def _render_inner() -> None:
    if not _db.DB_PATH.exists():
        st.info(
            "**No recipes seeded yet.**\n\n"
            "Run `python -m meal_planner.seed_from_sheet` on the mini to populate "
            "the recipe database, then refresh."
        )
        return

    # If we just created a new recipe, jump straight into edit mode for it
    if st.session_state.get("_new_recipe_id"):
        rid = st.session_state.pop("_new_recipe_id", None)
        if rid is not None:
            st.session_state["_edit_recipe_id"] = rid

    # -----------------------------------------------------------------------
    # Tag filter pills
    # -----------------------------------------------------------------------
    all_tags = queries.list_all_tags()
    if all_tags:
        grouped = _partition_tags_by_category(all_tags, CATEGORY_MAP)
        selected: list[str] = []
        if grouped["cuisine"]:
            selected += st.pills(
                "Cuisine", options=grouped["cuisine"], selection_mode="multi",
                key="tag_pills_cuisine",
            ) or []
        if grouped["meat_or_diet"]:
            selected += st.pills(
                "Meat / diet", options=grouped["meat_or_diet"],
                selection_mode="multi", key="tag_pills_meat",
            ) or []
        if grouped["other"]:
            selected += st.pills(
                "Other", options=grouped["other"], selection_mode="multi",
                key="tag_pills_other",
            ) or []
        selected_tags = selected
        tag_logic = st.radio(
            "Match", ["AND", "OR"], horizontal=True, index=0
        )
    else:
        selected_tags = []
        tag_logic = "AND"

    # -----------------------------------------------------------------------
    # Recipe book filter pills (Phase 19.5) — own section; OR semantics within
    # selection, AND-combined with the tag filter above
    # -----------------------------------------------------------------------
    all_books = queries.list_all_recipe_books()
    if all_books:
        selected_books: list[str] = st.pills(
            "Recipe book", options=all_books, selection_mode="multi",
            key="book_pills",
        ) or []
    else:
        selected_books = []

    sort_alpha = st.toggle(
        "Alphabetical", value=True, key="recipes_sort_alpha",
        help="When off, recipes are listed most-recently-added first.",
    )
    recipes = queries.search_recipes(
        tags=tuple(selected_tags),
        tag_logic=tag_logic.lower(),
        recipe_books=tuple(selected_books),
        sort="alpha" if sort_alpha else "recent",
    )

    # -----------------------------------------------------------------------
    # "+ New recipe" button (above grid)
    # -----------------------------------------------------------------------
    if st.button("+ New recipe", type="secondary"):
        try:
            new_id = queries.create_recipe(title="New Recipe")
            _pending_ids().add(new_id)
            st.session_state["_new_recipe_id"] = new_id
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to create recipe: {exc}")
        return

    if not recipes:
        if selected_tags:
            st.info(
                "No recipes match the current tag filter. "
                "Adjust selection above."
            )
        else:
            st.info("Recipe database exists but contains no recipes yet.")
        return

    recipe_ids = [r.id for r in recipes]
    df = pd.DataFrame({
        "Send": [False] * len(recipes),
        "Recipe": [r.title for r in recipes],
        "Servings": [r.base_servings for r in recipes],
    })

    edited_df = st.data_editor(
        df,
        column_config={
            "Send": st.column_config.CheckboxColumn("Send"),
            "Recipe": st.column_config.TextColumn("Recipe"),
            "Servings": st.column_config.NumberColumn(
                "Servings", min_value=1, max_value=20, step=1
            ),
        },
        disabled=["Recipe"],
        num_rows="fixed",
        use_container_width=True,
        hide_index=True,
    )

    st.caption("Check exactly one recipe row, then click **View** or **Edit selected**.")

    # -----------------------------------------------------------------------
    # Row-selection state
    # -----------------------------------------------------------------------
    checked_indices = [i for i, row in edited_df.iterrows() if row["Send"]]
    exactly_one = len(checked_indices) == 1

    col_send, col_view, col_edit = st.columns([3, 1, 1])
    with col_send:
        if st.button(
            "Send checked recipes to Todoist", type="primary", use_container_width=True
        ):
            checked = [
                [recipe_ids[i], int(row["Servings"])]
                for i, row in edited_df.iterrows()
                if row["Send"]
            ]
            if not checked:
                st.warning("No recipes selected. Check at least one box.")
            else:
                try:
                    task_id = _jobs_client.enqueue(
                        "meal_planner_send_to_todoist", {"recipe_scales": checked}
                    )
                    st.session_state["_send_job"] = {
                        "task_id": task_id,
                        "started_at": time.monotonic(),
                    }
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to enqueue: {exc}")

    with col_view:
        view_clicked = st.button(
            "View",
            type="secondary",
            use_container_width=True,
            disabled=not exactly_one,
            key="view_button",
        )
        if view_clicked and exactly_one:
            rid = recipe_ids[checked_indices[0]]
            st.session_state["_view_recipe_id"] = rid
            st.rerun()

    with col_edit:
        edit_clicked = st.button(
            "Edit selected",
            type="secondary",
            use_container_width=True,
            disabled=not exactly_one,
        )
        if edit_clicked and exactly_one:
            rid = recipe_ids[checked_indices[0]]
            st.session_state["_edit_recipe_id"] = rid
            st.rerun()

    _render_job_status("_send_job", "Send to Todoist")

    # -----------------------------------------------------------------------
    # View dialog (Phase 19) — opens modal with read-only recipe details.
    # Triggered by the View button above; dialog clears _view_recipe_id on
    # Close, otherwise it would re-open on every rerun.
    # -----------------------------------------------------------------------
    view_id = st.session_state.get("_view_recipe_id")
    if view_id is not None:
        _render_view_dialog(view_id)

    # -----------------------------------------------------------------------
    # Edit panel
    # -----------------------------------------------------------------------
    edit_id = st.session_state.get("_edit_recipe_id")
    if edit_id is not None:
        st.divider()
        _render_edit_panel(edit_id)

    st.divider()
    _render_clear_button()
    _render_job_status("_clear_job", "Clear Todoist")


def _close_edit_panel(recipe_id: int) -> None:
    """Close the edit panel and pop all per-recipe widget state.

    Streamlit ignores `value=` / `default=` once a widget's `key=` exists in
    session_state, so re-opening the panel without this cleanup would show
    the user's previous (possibly cancelled) edits — and a follow-up Save
    would silently overwrite the DB with stale values. Always call this on
    any close path (Cancel, Save success, Delete success, recipe-vanished).
    """
    st.session_state.pop("_edit_recipe_id", None)
    st.session_state.pop(f"_confirm_delete_at_{recipe_id}", None)
    for prefix in _EDIT_WIDGET_KEY_PREFIXES:
        st.session_state.pop(f"{prefix}_{recipe_id}", None)


@st.dialog("Recipe details", width="large")
def _render_view_dialog(recipe_id: int) -> None:
    """Render a read-only view of one recipe as a modal dialog.

    Closing the dialog (Close button or the system × control) pops
    _view_recipe_id from session_state so re-rendering doesn't re-open it.
    """
    try:
        recipe = queries.get_recipe(recipe_id)
    except KeyError:
        st.error(f"Recipe id {recipe_id} not found — it may have been deleted.")
        st.session_state.pop("_view_recipe_id", None)
        if st.button("Close", key=f"view_close_missing_{recipe_id}"):
            st.rerun()
        return

    tags = queries.get_recipe_tags(recipe_id)
    ingredients = queries.list_ingredients(recipe_id)
    st.markdown(format_view_block(recipe, tags, ingredients))
    if st.button("Close", key=f"view_close_{recipe_id}", type="primary"):
        st.session_state.pop("_view_recipe_id", None)
        st.rerun()


def _render_edit_panel(recipe_id: int) -> None:
    """Render the inline edit form for a single recipe."""
    try:
        recipe = queries.get_recipe(recipe_id)
    except KeyError:
        st.error(f"Recipe id {recipe_id} not found — it may have been deleted.")
        _close_edit_panel(recipe_id)
        return

    current_tags = queries.get_recipe_tags(recipe_id)
    current_ingredients = queries.list_ingredients(recipe_id)
    before_rows = ingredients_to_rows(current_ingredients)

    st.subheader(f"Editing: {recipe.title}")

    # -----------------------------------------------------------------------
    # Form fields
    # -----------------------------------------------------------------------
    new_title = st.text_input("Title", value=recipe.title, key=f"edit_title_{recipe_id}")
    new_servings = st.number_input(
        "Base servings", min_value=1, max_value=max(9999, recipe.base_servings), value=recipe.base_servings,
        step=1, key=f"edit_servings_{recipe_id}",
    )
    new_instructions = st.text_area(
        "Instructions", value=recipe.instructions or "", key=f"edit_instructions_{recipe_id}",
    )
    col_cook, col_source = st.columns(2)
    with col_cook:
        new_cook_time = st.number_input(
            "Cook time (min)", min_value=0, max_value=max(9999, recipe.cook_time_min or 0),
            value=recipe.cook_time_min or 0,
            step=5, key=f"edit_cook_time_{recipe_id}",
        )
    with col_source:
        new_source = st.text_input(
            "Source", value=recipe.source or "", key=f"edit_source_{recipe_id}",
        )
    new_recipe_book = st.text_input(
        "Recipe book", value=recipe.recipe_book or "",
        key=f"edit_recipe_book_{recipe_id}",
        placeholder="e.g. Serious Eats, NYT Cooking, Family",
        help="Where this recipe came from — cookbook, website, family member, etc.",
    )

    # -----------------------------------------------------------------------
    # Tag selector — uses _edit_tags_ key prefix, NOT tag_pills_* (filter pills)
    # -----------------------------------------------------------------------
    all_known_tags = queries.list_all_tags()
    tag_options = sorted(set(all_known_tags) | set(current_tags))
    selected_edit_tags: list[str] = st.pills(
        "Tags",
        options=tag_options,
        selection_mode="multi",
        default=current_tags,
        key=f"edit_tags_{recipe_id}",
    ) or []
    new_free_tag = st.text_input(
        "Add new tag", value="", key=f"edit_new_tag_{recipe_id}",
        placeholder="type a tag and press Enter",
    )
    if new_free_tag.strip():
        merged = normalize_tags(selected_edit_tags + [new_free_tag])
    else:
        merged = normalize_tags(selected_edit_tags)

    # -----------------------------------------------------------------------
    # Ingredients sub-grid
    # -----------------------------------------------------------------------
    st.markdown("**Ingredients**")
    ingr_df = pd.DataFrame(before_rows)
    edited_ingr = st.data_editor(
        ingr_df,
        column_config={
            "id": st.column_config.NumberColumn("id", disabled=True),
            "name": st.column_config.TextColumn("Name"),
            "qty_per_serving": st.column_config.NumberColumn("Qty/serving", format="%.2f"),
            "unit": st.column_config.TextColumn("Unit"),
            "notes": st.column_config.TextColumn("Notes"),
            "todoist_section": st.column_config.TextColumn("Todoist section"),
            "sort_order": st.column_config.NumberColumn("Sort", step=1),
        },
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key=f"edit_ingr_{recipe_id}",
    )

    # -----------------------------------------------------------------------
    # Save / Cancel / Delete
    # -----------------------------------------------------------------------
    col_save, col_cancel, col_delete = st.columns([2, 1, 1])

    with col_save:
        if st.button("Save changes", type="primary", use_container_width=True, key=f"save_{recipe_id}"):
            payload = {
                "title": new_title,
                "base_servings": new_servings,
                "instructions": new_instructions,
                "cook_time_min": int(new_cook_time),
                "source": new_source,
                "recipe_book": new_recipe_book,
            }
            ok, errs = validate_recipe_form(payload)
            if not ok:
                for e in errs:
                    st.error(e)
            else:
                _save_recipe(recipe_id, payload, merged, before_rows, edited_ingr)

    with col_cancel:
        if st.button("Cancel", use_container_width=True, key=f"cancel_{recipe_id}"):
            if recipe_id in _pending_ids():
                try:
                    queries.delete_recipe(recipe_id)
                except Exception:
                    pass
                _pending_ids().discard(recipe_id)
            _close_edit_panel(recipe_id)
            st.rerun()

    with col_delete:
        _render_delete_button(recipe_id)


def _save_recipe(
    recipe_id: int,
    payload: dict,
    tags: list[str],
    before_rows: list[dict],
    edited_ingr,
) -> None:
    """Write recipe + tags + ingredient diff in a single transaction."""
    after_rows = edited_ingr.to_dict("records") if hasattr(edited_ingr, "to_dict") else []
    # Normalize NaN/None ids to 0 so diff_ingredients treats those rows as adds.
    for row in after_rows:
        if nan_to_none(row.get("id")) is None:
            row["id"] = 0

    diff = diff_ingredients(before_rows, after_rows)

    try:
        with _db._get_conn(_db.DB_PATH) as conn:
            queries.update_recipe(
                recipe_id,
                title=payload["title"].strip(),
                base_servings=int(payload["base_servings"]),
                instructions=payload["instructions"] or None,  # "" → NULL
                cook_time_min=payload["cook_time_min"],
                source=payload["source"] or None,              # "" → NULL
                recipe_book=(payload.get("recipe_book") or "").strip() or None,
                conn=conn,
            )
            queries.set_recipe_tags(recipe_id, tags, conn=conn)
            for row in diff["deletes"]:
                queries.delete_ingredient(row["id"], conn=conn)
            for row in diff["updates"]:
                queries.update_ingredient(
                    row["id"],
                    name=row.get("name") or None,
                    qty_per_serving=nan_to_none(row.get("qty_per_serving")),
                    unit=clean_optional_str(row.get("unit")),
                    notes=clean_optional_str(row.get("notes")),
                    todoist_section=clean_optional_str(row.get("todoist_section")),
                    sort_order=int(nan_to_none(row.get("sort_order")) or 0),
                    conn=conn,
                )
            for row in diff["adds"]:
                if row.get("name", "").strip():
                    queries.add_ingredient(
                        recipe_id,
                        name=row["name"].strip(),
                        qty_per_serving=nan_to_none(row.get("qty_per_serving")),
                        unit=clean_optional_str(row.get("unit")),
                        notes=clean_optional_str(row.get("notes")),
                        todoist_section=clean_optional_str(row.get("todoist_section")),
                        sort_order=int(nan_to_none(row.get("sort_order")) or 0),
                        conn=conn,
                    )
        _pending_ids().discard(recipe_id)
        st.success("Recipe saved.")
        _close_edit_panel(recipe_id)
        st.rerun()
    except KeyError:
        st.error("Recipe was deleted by another session — closing editor.")
        _close_edit_panel(recipe_id)
        st.rerun()
    except Exception as exc:
        st.error(f"Save failed: {exc}")


def _render_delete_button(recipe_id: int) -> None:
    """Two-click delete confirm. 10 s TTL resets the confirm window."""
    _key = f"_confirm_delete_at_{recipe_id}"
    confirm_at = st.session_state.get(_key)
    now = time.monotonic()

    if confirm_at is not None and now - confirm_at > _CONFIRM_DELETE_TTL:
        del st.session_state[_key]
        confirm_at = None

    if confirm_at is None:
        if st.button(
            "Delete", type="secondary", use_container_width=True,
            key=f"delete_btn_{recipe_id}",
        ):
            st.session_state[_key] = time.monotonic()
            st.rerun()
    else:
        remaining = int(_CONFIRM_DELETE_TTL - (now - confirm_at))
        st.warning(f"Delete in {remaining}s?")
        if st.button(
            "Confirm delete", type="primary", use_container_width=True,
            key=f"confirm_delete_{recipe_id}",
        ):
            try:
                queries.delete_recipe(recipe_id)
                _close_edit_panel(recipe_id)
                st.success("Recipe deleted.")
                st.rerun()
            except Exception as exc:
                # Reset the confirm window so a retry starts from a clean slate
                st.session_state.pop(_key, None)
                st.error(f"Delete failed: {exc}")


def _render_clear_button() -> None:
    """Two-click confirm button that enqueues meal_planner_clear_todoist.

    First click sets a timestamp in session_state. Second click within
    _CONFIRM_CLEAR_TTL seconds fires the job. Expired state resets and
    the user must click again.
    """
    st.caption(
        "This deletes only items labeled `meal-planner`. "
        "Event-aggregator and finance-monitor tasks are untouched."
    )

    confirm_at = st.session_state.get("_confirm_clear_at")
    now = time.monotonic()

    if confirm_at is not None and now - confirm_at > _CONFIRM_CLEAR_TTL:
        # Expired — reset and treat as first click
        del st.session_state["_confirm_clear_at"]
        confirm_at = None

    if confirm_at is None:
        if st.button("Clear all meal-planner items from Todoist", type="secondary"):
            st.session_state["_confirm_clear_at"] = time.monotonic()
            st.rerun()
    else:
        remaining = int(_CONFIRM_CLEAR_TTL - (now - confirm_at))
        st.warning(f"Are you sure? Click again within {remaining}s to confirm.")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("Yes, clear all meal-planner tasks", type="primary"):
                del st.session_state["_confirm_clear_at"]
                try:
                    task_id = _jobs_client.enqueue("meal_planner_clear_todoist")
                    st.session_state["_clear_job"] = {
                        "task_id": task_id,
                        "started_at": time.monotonic(),
                    }
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to enqueue: {exc}")
        with col_no:
            if st.button("Cancel"):
                del st.session_state["_confirm_clear_at"]
                st.rerun()
