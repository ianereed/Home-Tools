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
import traceback

import pandas as pd
import streamlit as st

from console import jobs_client as _jobs_client
from console.tabs._recipe_form import (
    INGREDIENT_COLUMNS,
    clean_optional_str,
    diff_ingredients,
    format_view_block,
    ingredients_to_rows,
    nan_to_none,
    normalize_tags,
    row_name,
    validate_recipe_form,
)
from meal_planner import db as _db
from meal_planner import export_xlsx, queries
from meal_planner.sections import EDITABLE_SECTIONS, classify
from meal_planner.tag_categories import CATEGORY_MAP, _partition_tags_by_category

from console.tabs._job_status import (
    _format_status,
    _read_result_or_synthesize_error,
)

_CONFIRM_CLEAR_TTL = 10  # seconds before the confirm state resets
_CONFIRM_DELETE_TTL = 10  # seconds before the delete confirm state resets
# A job with no result yet: jobs_client.result() says "pending" both while the
# job runs and when the consumer or jobs-http is down or lost the job, and
# Send stays disabled meanwhile. After _JOB_STOP_WAITING_AFTER the user may
# give up on it; after _JOB_GIVE_UP_AFTER the console gives up by itself.
_JOB_STOP_WAITING_AFTER = 60  # seconds
_JOB_GIVE_UP_AFTER = 300  # seconds

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

# Edit-panel widget key prefix for each recipe field (plus the tag widgets),
# for reloading single fields from the DB.
_EDIT_FIELD_WIDGETS = {
    "title": "edit_title",
    "base_servings": "edit_servings",
    "instructions": "edit_instructions",
    "cook_time_min": "edit_cook_time",
    "source": "edit_source",
    "recipe_book": "edit_recipe_book",
    "tags": "edit_tags",
    "new_tag": "edit_new_tag",
}

__all__ = ["render", "_format_status", "_read_result_or_synthesize_error"]


def render() -> None:
    try:
        _render_inner()
    except Exception as exc:
        # Also to stderr (logs/console.err.log): st.exception only reaches the
        # browser, so a failure Anny hits otherwise leaves no trace to debug.
        traceback.print_exc()
        st.error("Plan tab error — see traceback below")
        st.exception(exc)


@st.fragment(run_every="2s")
def _render_job_status(state_key: str, label: str) -> None:
    _job_status_body(state_key, label)


def _job_status_body(state_key: str, label: str) -> None:
    """Poll jobs_client for the job stored at session_state[state_key].

    Renders a spinner while pending. On completion it clears state_key, keeps
    the result (success/warning/error with the result-dict summary) under
    ``{state_key}_done`` and reruns the whole app, so a Send button disabled
    while the job ran comes back. The result stays up until Dismiss — it used
    to be drawn once and vanish on the fragment's next 2 s tick. A job with no
    result can be abandoned ("Stop waiting") and is abandoned automatically
    after _JOB_GIVE_UP_AFTER, the same way.
    """
    done_key = f"{state_key}_done"
    done = st.session_state.get(done_key)
    if done:
        level, message = done
        if level == "success":
            st.success(f"{label}: {message}")
        elif level == "warning":
            st.warning(f"{label}: {message}")
        else:
            st.error(f"{label}: {message}")
        if st.button("Dismiss", key=f"{state_key}_dismiss"):
            st.session_state.pop(done_key, None)
            st.rerun(scope="fragment")
        return
    state = st.session_state.get(state_key)
    if not state:
        return
    task_id = state["task_id"]
    started_at = state["started_at"]
    for note in state.get("notes", ()):
        st.warning(note)
    result = _read_result_or_synthesize_error(_jobs_client.result, task_id)
    notes = " ".join(state.get("notes", ()))
    if result is None:
        elapsed = int(time.monotonic() - started_at)
        gave_up = elapsed >= _JOB_GIVE_UP_AFTER
        if not gave_up:
            st.info(f"{label}… ({elapsed}s)", icon="⏳")
            if elapsed < _JOB_STOP_WAITING_AFTER or not st.button(
                "Stop waiting", key=f"{state_key}_stop",
                help="No result yet. The job service may be down, or the job lost.",
            ):
                return
        del st.session_state[state_key]
        why = f"no result after {elapsed // 60} min" if gave_up else "stopped waiting"
        st.session_state[done_key] = (
            "warning",
            f"{why} — the job may still run, or may never have. "
            f"Check Todoist before sending again. {notes}".strip(),
        )
        st.rerun()
    del st.session_state[state_key]
    level, message = _format_status(result)
    st.session_state[done_key] = (level, f"{message} {notes}".strip())
    st.rerun()


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

    _sweep_stub_recipes_once()

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
    # "+ New recipe" and "Export to Excel" buttons (above grid)
    # -----------------------------------------------------------------------
    col_new, col_export, _ = st.columns([1, 1, 3])
    with col_export:
        _render_export_button()
    with col_new:
        new_clicked = st.button("+ New recipe", type="secondary", use_container_width=True)
    if new_clicked:
        try:
            _sweep_stub_recipes()
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
        # One send at a time: a second click while the job runs used to enqueue
        # the same groceries twice. The button greys out after the rerun below;
        # the check on send_busy also drops a click that beat that rerun.
        send_busy = bool(st.session_state.get("_send_job"))
        if st.button(
            "Send checked recipes to Todoist", type="primary", use_container_width=True,
            disabled=send_busy,
        ) and not send_busy:
            checked, notes = _checked_recipe_scales(edited_df, recipes)
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
                        "notes": notes,
                    }
                    st.session_state.pop("_send_job_done", None)
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


def _checked_recipe_scales(edited_df, recipes) -> tuple[list[list[int]], list[str]]:
    """``[recipe_id, servings]`` for each checked row, plus notes for the user.

    A Servings cell cleared in the grid comes back as NaN, and int(NaN) raised
    outside any error handling. Such a row is sent at the recipe's own
    base_servings, with a note saying so.
    """
    checked: list[list[int]] = []
    notes: list[str] = []
    for i, row in edited_df.iterrows():
        if not row["Send"]:
            continue
        recipe = recipes[i]
        servings = row["Servings"]
        if pd.isna(servings):
            servings = recipe.base_servings
            notes.append(
                f"{recipe.title}: Servings was blank, sent at its base {servings}."
            )
        checked.append([recipe.id, int(servings)])
    return checked, notes


def _sweep_stub_recipes() -> None:
    """Delete stale, never-touched "New Recipe" stubs (see delete_untouched_stub_recipes)."""
    try:
        n = queries.delete_untouched_stub_recipes()
    except Exception:
        traceback.print_exc()
        return
    if n:
        print(f"plan: swept {n} untouched 'New Recipe' stub(s)", flush=True)


def _sweep_stub_recipes_once() -> None:
    """Run the stub sweep on a browser session's first render of the tab.

    "+ New recipe" inserts its row up front and only this session knows it is
    pending, so a dropped connection or a double-click leaves blank stubs in
    the list. Sweeping once per session (and on every "+ New recipe") clears
    them without a write on every rerun.
    """
    if st.session_state.get("_stub_sweep_done"):
        return
    st.session_state["_stub_sweep_done"] = True
    _sweep_stub_recipes()


def _close_edit_panel(recipe_id: int) -> None:
    """Close the edit panel and pop all per-recipe widget and sync state.

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
    for prefix in ("_edit_snapshot", "_edit_reload", "_edit_notice", "_edit_ingr_rows"):
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
    fresh_rows = ingredients_to_rows(current_ingredients)

    # The edit widgets are keyed, so they hold the recipe as this panel loaded
    # it plus this session's edits. Comparing them against a fresh DB read
    # made every change from another session (Ian and Anny edit at the same
    # time) look like a local edit, and autosave wrote the old values back.
    # Local edits are measured against this snapshot instead.
    snap_key = f"_edit_snapshot_{recipe_id}"
    reload = st.session_state.pop(f"_edit_reload_{recipe_id}", None)
    snap = st.session_state.get(snap_key)
    if reload is not None or snap is None or f"edit_title_{recipe_id}" not in st.session_state:
        snap = {"fields": _recipe_values(recipe), "tags": sorted(current_tags)}
        st.session_state[snap_key] = snap
        # A reload names the widgets to refresh; opening the panel fills all.
        _seed_edit_widgets(recipe_id, recipe, current_tags, reload)
    # The ingredient grid needs its own snapshot, for a Streamlit reason:
    # data_editor's widget id is built from its data, so a grid built from a
    # fresh read that another session had just changed got a new id and
    # silently dropped this session's pending edit. The grid keeps showing the
    # rows it was built from until this session saves or reloads it.
    rows_key = f"_edit_ingr_rows_{recipe_id}"
    shown_rows = st.session_state.get(rows_key)
    if shown_rows is None:
        shown_rows = fresh_rows
        st.session_state[rows_key] = shown_rows
    notice = st.session_state.pop(f"_edit_notice_{recipe_id}", None)
    if notice:
        st.warning(notice)

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
    ingr_df = _ingredients_frame(shown_rows)
    edited_ingr = st.data_editor(
        ingr_df,
        column_config={
            "id": st.column_config.NumberColumn("id", disabled=True),
            "name": st.column_config.TextColumn("Name"),
            "qty_per_serving": st.column_config.NumberColumn("Qty/serving", format="%.2f"),
            "unit": st.column_config.TextColumn("Unit"),
            "notes": st.column_config.TextColumn("Notes"),
            "todoist_section": st.column_config.SelectboxColumn(
                "Grocery section",
                help="Which Todoist grocery section this ingredient is sent to. "
                     "Pick 'Skip' for household staples you keep in stock — they "
                     "stay on the recipe but are never added to the grocery list. "
                     "Left blank, it's picked from the ingredient name.",
                options=EDITABLE_SECTIONS,
                required=False,
            ),
            "sort_order": st.column_config.NumberColumn("Sort", step=1),
        },
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key=f"edit_ingr_{recipe_id}",
    )

    # -----------------------------------------------------------------------
    # Autosave — persist edits to the live recipe row on every rerun (i.e.
    # whenever a field commits: text on blur/Enter, numbers/grid/pills on
    # change). Each commit triggers a rerun, and we save here BEFORE the
    # buttons render, so a dropped tailnet connection can't lose work.
    #
    # Only what this session changed (form vs snapshot) is written, under an
    # updated_at guard, so a concurrent edit elsewhere in the recipe survives.
    # If the other session changed the same field or ingredient, or wrote
    # between our read and our write, the panel reloads and says so. Any
    # rerun waits until the buttons below have handled their click, or
    # Done/Close/Delete pressed in the same commit would be lost.
    # -----------------------------------------------------------------------
    payload = {
        "title": new_title,
        "base_servings": new_servings,
        "instructions": new_instructions,
        "cook_time_min": int(new_cook_time),
        "source": new_source,
        "recipe_book": new_recipe_book,
    }
    ok, errs = validate_recipe_form(payload)
    after_rows = _rows_from_editor(edited_ingr)
    ingr = _plan_ingredient_sync(shown_rows, fresh_rows, after_rows)
    if ingr["blanked"]:
        st.warning(
            "An ingredient's name can't be blank, so that change isn't saved. "
            "To remove an ingredient, delete its row instead."
        )

    saved_now = False
    save_error: str | None = None
    rerun_after = False
    notice = None
    if ok:
        form = _form_values(payload)
        rec = _plan_recipe_sync(snap, recipe, current_tags, form, merged)
        clashes = rec["conflicts"] + [f"ingredient {row['name']}" for row in ingr["conflicts"]]
        if clashes:
            notice = (
                f"This recipe's {', '.join(clashes)} was changed in another "
                "session at the same time, so your edit there wasn't saved. It "
                "now shows the other version — redo your edit if you still want it."
            )
        # Widgets to reload from the DB: what the other session changed, and
        # the tag pills + "Add new tag" box once a typed tag is saved (left in
        # the box, the next tag typed there replaced it in the saved set). Only
        # these: reloading pushes the value to the browser, over anything
        # being typed in that field.
        refresh = set(rec["theirs"])
        if rec["theirs_tags"] or new_free_tag.strip():
            refresh |= {"tags", "new_tag"}
        ingr_changed = any(ingr["diff"].values())

        if rec["writes"] or rec["tags"] is not None or ingr_changed:
            status, perr = _persist_recipe(
                recipe_id, rec["writes"], rec["tags"], ingr["diff"],
                expected_updated_at=recipe.updated_at,
            )
            if status == "ok":
                saved_now = True
                # Once anything is autosaved the recipe is real: the button flips
                # from "Cancel" (hard-deletes the stub) to "Close".
                _pending_ids().discard(recipe_id)
                st.session_state[snap_key] = {"fields": form, "tags": rec["final_tags"]}
                # st.data_editor keeps its edits as a POSITION-keyed delta in
                # session_state that survives reruns. After an ingredient write
                # list_ingredients reorders the rows (ORDER BY sort_order, name)
                # and grows/shrinks the set, so the stale delta would re-apply at
                # the wrong row positions — reverting or duplicating the edit
                # (Anny: every edit after the first needs two tries). So the grid
                # rebuilds clean from the saved rows.
                rerun_after = _queue_reload(
                    recipe_id, refresh, notice, grid=ingr_changed or ingr["theirs_changed"],
                )
            elif status == "deleted":
                st.error("Recipe was deleted by another session — closing editor.")
                _close_edit_panel(recipe_id)
                st.rerun()
            elif status == "stale":
                notice = (
                    "This recipe was changed in another session while you were "
                    "editing, so your last edit wasn't saved. It now shows the "
                    "latest version — redo the edit if you still want it."
                )
                rerun_after = _queue_reload(recipe_id, set(_EDIT_FIELD_WIDGETS), notice, grid=True)
            else:
                save_error = perr
                st.warning(f"Autosave failed: {perr}")
        else:
            # Nothing of ours to save, but another session may have changed this
            # recipe: load its version so the form isn't showing stale values.
            rerun_after = _queue_reload(recipe_id, refresh, notice, grid=ingr["theirs_changed"])

    if saved_now:
        st.success("Saved ✓", icon="✅")
    elif not ok:
        st.caption("⚠️ Not saved yet — " + "; ".join(errs))
    else:
        st.caption("✓ Changes save automatically as you edit")

    # -----------------------------------------------------------------------
    # Done / Cancel / Delete
    # -----------------------------------------------------------------------
    col_done, col_cancel, col_delete = st.columns([2, 1, 1])

    with col_done:
        if st.button("Done", type="primary", use_container_width=True, key=f"save_{recipe_id}"):
            # Autosave above already wrote this run's edits; Done just closes —
            # unless part of the edit wasn't saved, which the rerun explains.
            if not ok:
                for e in errs:
                    st.error(e)
            elif save_error:
                st.error(f"Save failed: {save_error}")
            elif not notice:
                _pending_ids().discard(recipe_id)
                _close_edit_panel(recipe_id)
                st.rerun()

    with col_cancel:
        is_pending = recipe_id in _pending_ids()
        # New recipe: Cancel discards the unsaved stub. Existing recipe: edits
        # are already autosaved, so this just closes the panel (no revert).
        if st.button(
            "Cancel" if is_pending else "Close",
            use_container_width=True, key=f"cancel_{recipe_id}",
        ):
            if is_pending:
                try:
                    queries.delete_recipe(recipe_id)
                except Exception:
                    pass
                _pending_ids().discard(recipe_id)
            _close_edit_panel(recipe_id)
            st.rerun()

    with col_delete:
        _render_delete_button(recipe_id)

    if rerun_after:
        st.rerun()


def _recipe_values(recipe) -> dict:
    """The recipe's editable fields, normalized the way the form writes them."""
    return {
        "title": recipe.title,
        "base_servings": recipe.base_servings,
        "instructions": recipe.instructions or None,
        "cook_time_min": recipe.cook_time_min or 0,
        "source": recipe.source or None,
        "recipe_book": (recipe.recipe_book or "").strip() or None,
    }


def _form_values(payload: dict) -> dict:
    """A validated form payload as the DB values it saves to.

    Mirrors _recipe_values, so a form that matches the recipe compares equal
    and a rerun that changed nothing writes nothing.
    """
    return {
        "title": payload["title"].strip(),
        "base_servings": int(payload["base_servings"]),
        "instructions": payload["instructions"] or None,  # "" → NULL
        "cook_time_min": int(payload["cook_time_min"]),
        "source": payload["source"] or None,              # "" → NULL
        "recipe_book": (payload.get("recipe_book") or "").strip() or None,
    }


def _seed_edit_widgets(recipe_id: int, recipe, tags: list[str], only=None) -> None:
    """Load the recipe into the edit widgets through the Session State API.

    ``only`` names the _EDIT_FIELD_WIDGETS entries to load (default: all).

    Popping a widget's key does not reload it: text, number and pills widgets
    keep their element id when only the key's value goes away, and the browser
    re-sends the value it still shows on the next rerun. A value assigned to
    the key before the widget renders is pushed to the browser.
    """
    values = {
        "title": recipe.title,
        "base_servings": recipe.base_servings,
        "instructions": recipe.instructions or "",
        "cook_time_min": recipe.cook_time_min or 0,
        "source": recipe.source or "",
        "recipe_book": recipe.recipe_book or "",
        "tags": list(tags),
        "new_tag": "",
    }
    for name in (values if only is None else only):
        st.session_state[f"{_EDIT_FIELD_WIDGETS[name]}_{recipe_id}"] = values[name]


def _queue_reload(recipe_id: int, fields=(), notice: str | None = None, grid: bool = False) -> bool:
    """Mark edit widgets to reload from the DB on the next run.

    ``fields`` names _EDIT_FIELD_WIDGETS entries; ``grid`` rebuilds the
    ingredient grid from fresh rows. Returns True when the caller should rerun.
    """
    if fields:
        reload_key = f"_edit_reload_{recipe_id}"
        st.session_state[reload_key] = sorted(set(st.session_state.get(reload_key) or ()) | set(fields))
    if notice:
        st.session_state[f"_edit_notice_{recipe_id}"] = notice
    if grid:
        st.session_state.pop(f"_edit_ingr_rows_{recipe_id}", None)
        st.session_state.pop(f"edit_ingr_{recipe_id}", None)
    return bool(fields or notice or grid)


def _ingredients_frame(rows: list[dict]) -> pd.DataFrame:
    """Build the ingredient grid's DataFrame, keeping its columns when empty.

    ``pd.DataFrame([])`` has no columns, so a brand-new recipe got a grid with
    nothing to type into and its ingredients could never be added (Anny,
    2026-10-20). Numeric dtypes match what a non-empty frame infers.
    """
    df = pd.DataFrame(rows, columns=list(INGREDIENT_COLUMNS))
    if df.empty:
        df = df.astype({"id": "int64", "qty_per_serving": "float64", "sort_order": "int64"})
    return df


def _rows_from_editor(edited_ingr) -> list[dict]:
    """Turn the data_editor frame into plain row dicts with normalized ids.

    Applies nan_to_none to every cell first: pandas turns Python None into
    float('nan') for any mixed-type numeric column (e.g. qty_per_serving when
    some rows are blank, todoist_section when some rows are unsectioned), so
    a naive dict compare would see None != nan as a permanent phantom "update"
    and trigger an infinite autosave rerun loop. Blanket normalization at this
    boundary keeps the diff idempotent regardless of which columns are affected.
    After that, NaN/None ids become 0 so diff_ingredients treats those rows as
    adds.
    """
    after_rows = edited_ingr.to_dict("records") if hasattr(edited_ingr, "to_dict") else []
    for row in after_rows:
        for k in list(row):
            row[k] = nan_to_none(row[k])
        if row.get("id") is None:
            row["id"] = 0
    return after_rows


def _ingredient_diff(
    before_rows: list[dict], after_rows: list[dict],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """diff_ingredients minus the rows that can't be saved yet.

    Returns ``(diff, blanked)``. Added rows with no name are dropped (a row
    whose quantity was typed first waits for its name). Updates that clear an
    existing ingredient's name go to ``blanked`` instead of being written:
    name is NOT NULL, and that one row used to roll back the whole autosave
    transaction on every rerun.
    """
    diff = diff_ingredients(before_rows, after_rows)
    blanked = [row for row in diff["updates"] if not row_name(row)]
    diff["updates"] = [row for row in diff["updates"] if row_name(row)]
    diff["adds"] = [row for row in diff["adds"] if row_name(row)]
    return diff, blanked


def _plan_recipe_sync(snap: dict, recipe, current_tags: list[str], form: dict, merged: list[str]) -> dict:
    """Three-way merge of the form against the panel snapshot and the DB now.

    "mine" is form vs snapshot, "theirs" is DB vs snapshot. Returns:

    - writes: fields to write — this session's changes, minus conflicts
    - conflicts: fields both sessions changed (the DB's value stands)
    - theirs: fields the other session changed, to reload into the form
    - theirs_tags: whether the other session changed the tag set
    - final_tags: the merged tag set; tags: the same when it must be written,
      else None

    Tags merge one tag at a time: this session's additions and removals apply
    on top of the other session's set, so two people each adding a tag at
    once keep both (comparing whole sets called that a conflict and dropped one).
    """
    base = snap["fields"]
    db_now = _recipe_values(recipe)
    mine = {k: v for k, v in form.items() if v != base[k]}
    theirs = {k for k, v in db_now.items() if v != base[k]}
    writes = {k: v for k, v in mine.items() if v != db_now[k]}
    conflicts = sorted(k for k in writes if k in theirs)
    for k in conflicts:
        del writes[k]

    base_tags, mine_tags, db_tags = set(snap["tags"]), set(merged), set(current_tags)
    final = (db_tags | (mine_tags - base_tags)) - (base_tags - mine_tags)
    return {
        "writes": writes,
        "conflicts": conflicts,
        "theirs": theirs,
        "theirs_tags": db_tags != base_tags,
        "final_tags": sorted(final),
        "tags": sorted(final) if final != db_tags else None,
    }


def _plan_ingredient_sync(shown_rows: list[dict], fresh_rows: list[dict], after_rows: list[dict]) -> dict:
    """The grid's edits (vs the rows it showed) merged with the DB now.

    Returns:

    - diff: what to write (see _ingredient_diff), minus conflicts
    - blanked: rows whose name was cleared (not saved)
    - conflicts: edited or deleted rows the other session also changed or
      deleted (the DB's version stands)
    - theirs_changed: whether the DB rows differ from the grid's, so the grid
      should be rebuilt
    """
    diff, blanked = _ingredient_diff(shown_rows, after_rows)
    fresh_by_id = {row["id"]: row for row in fresh_rows}
    theirs_ids = {row["id"] for row in shown_rows if fresh_by_id.get(row["id"]) != row}
    shown_names = {row["id"]: row["name"] for row in shown_rows}
    conflicts = [
        {"id": row["id"], "name": shown_names.get(row["id"]) or row_name(row)}
        for row in diff["updates"] + diff["deletes"] if row["id"] in theirs_ids
    ]
    diff["updates"] = [row for row in diff["updates"] if row["id"] not in theirs_ids]
    diff["deletes"] = [row for row in diff["deletes"] if row["id"] not in theirs_ids]
    return {
        "diff": diff,
        "blanked": blanked,
        "conflicts": conflicts,
        "theirs_changed": fresh_rows != shown_rows,
    }


def _persist_recipe(
    recipe_id: int,
    fields: dict,
    tags: list[str] | None,
    ingr_diff: dict[str, list[dict]],
    *,
    expected_updated_at: str,
) -> tuple[str, str | None]:
    """Write recipe fields, tags and an ingredient diff in one transaction.

    ``fields`` holds only the changed columns (already normalized by
    _form_values); ``tags`` is None when the tag set didn't change. The first
    write is guarded on ``expected_updated_at``, the updated_at this run read,
    so nothing is written if another session saved in between.

    Pure persistence — no Streamlit side effects. Returns ``(status, err)``
    with status "ok", "deleted" (the recipe is gone), "stale" (another session
    wrote first, or an ingredient being edited vanished) or "error".
    """
    try:
        with _db._get_conn(_db.DB_PATH) as conn:
            queries.update_recipe(
                recipe_id, expected_updated_at=expected_updated_at, conn=conn, **fields,
            )
            if tags is not None:
                queries.set_recipe_tags(recipe_id, tags, conn=conn)
            for row in ingr_diff["deletes"]:
                queries.delete_ingredient(row["id"], conn=conn)
            for row in ingr_diff["updates"]:
                queries.update_ingredient(
                    row["id"],
                    name=row_name(row),
                    qty_per_serving=nan_to_none(row.get("qty_per_serving")),
                    unit=clean_optional_str(row.get("unit")),
                    notes=clean_optional_str(row.get("notes")),
                    todoist_section=clean_optional_str(row.get("todoist_section")),
                    sort_order=int(nan_to_none(row.get("sort_order")) or 0),
                    conn=conn,
                )
            for row in ingr_diff["adds"]:
                notes = clean_optional_str(row.get("notes"))
                queries.add_ingredient(
                    recipe_id,
                    name=row_name(row),
                    qty_per_serving=nan_to_none(row.get("qty_per_serving")),
                    unit=clean_optional_str(row.get("unit")),
                    notes=notes,
                    # Same default the importers use, so a grid-added ingredient
                    # doesn't land in whichever Todoist section is listed first.
                    todoist_section=(
                        clean_optional_str(row.get("todoist_section"))
                        or classify(row_name(row), notes or "")
                    ),
                    sort_order=int(nan_to_none(row.get("sort_order")) or 0),
                    conn=conn,
                )
        return "ok", None
    except queries.StaleRecipeError:
        return "stale", None
    except KeyError:
        # update_recipe raises KeyError(recipe_id) only when the recipe row is
        # gone; the ingredient helpers raise it for a vanished ingredient
        # (another session deleted the row this one edited). The transaction
        # rolled back either way — tell the two apart.
        try:
            queries.get_recipe(recipe_id)
        except KeyError:
            return "deleted", "Recipe was deleted by another session."
        return "stale", None
    except Exception as exc:
        return "error", str(exc)


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


def _build_export() -> bytes:
    try:
        return export_xlsx.build_workbook_bytes()
    except Exception:
        # Runs on a worker thread after the click, so nothing reaches the page;
        # log it for logs/console.err.log and let the download fail visibly.
        traceback.print_exc()
        raise


def _render_export_button() -> None:
    """Download the whole recipe DB as .xlsx (ignores the filters above).

    The workbook is built only when clicked (callable ``data``), so the tab's
    frequent reruns don't rebuild it; the browser saves it to Downloads.
    """
    st.download_button(
        "Export to Excel",
        data=_build_export,
        file_name=export_xlsx.export_filename(),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        on_click="ignore",
        use_container_width=True,
        key="export_xlsx_button",
        help="Download every recipe (ingredients, instructions, tags, nutrition) as an "
             ".xlsx — e.g. for a Claude project folder. Ignores the filters above.",
    )


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
                    st.session_state.pop("_clear_job_done", None)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to enqueue: {exc}")
        with col_no:
            if st.button("Cancel"):
                del st.session_state["_confirm_clear_at"]
                st.rerun()
