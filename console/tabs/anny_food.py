"""Anny Food tab — Anny's pregnancy food-group tracker (Track mode, v1).

Weekly progress toward the DGA 2020–2025 pregnancy pattern (targets in
anny_food/targets.toml), a quick-add log, and per-food breakdown audits.
Breakdowns are computed offline (anny_food/rules.md) and imported with
`python -m anny_food.cli import`; this tab never calls an LLM.

Every tab renders on every rerun (st.tabs), so all widget keys carry the
"anny_" prefix and every write happens in a callback that opens, commits
and closes its own connection — never hold a write transaction open, the
Recipes tab writes to the same recipes.db.
"""
from __future__ import annotations

import traceback
from contextlib import contextmanager
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from anny_food import breakdown as _bd
from anny_food import db as _adb
from anny_food import log as _log
from anny_food import track as _tr
from anny_food.targets import load_targets
from console.tabs import _anny_food_view as _view
from meal_planner import db as _mp_db

_K = "anny_"
_DISCLAIMER = (
    "⚕️ A planning aid based on the Dietary Guidelines for Americans 2020–2025 "
    "pregnancy pattern — not medical advice. Check targets with your OB or dietitian."
)
_HISTORY_KEYS = ("veg_total", "fruit", "grain_whole", "dairy", "protein_total",
                 "protein_seafood", "oils", "calcium", "choline")
_HISTORY_WEEKS = 26

_schema_ready: set[str] = set()


@contextmanager
def _db():
    path = str(_mp_db.DB_PATH)
    if path not in _schema_ready:
        _adb.init_db()
        _schema_ready.add(path)
    conn = _adb.connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def render() -> None:
    try:
        _render_inner()
    except Exception as exc:
        traceback.print_exc()
        st.error("Anny Food tab error — see traceback below")
        st.exception(exc)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _settings(conn, targets) -> dict:
    due = _adb.get_setting(conn, "due_date")
    return {
        "due_date": date.fromisoformat(due) if due else None,
        "trimester_override": _adb.get_setting(conn, "trimester_override"),
        "week_start": _adb.get_setting(conn, "week_start", targets.meta.get("week_start", "monday")),
    }


def _on_save_settings() -> None:
    due = st.session_state.get(_K + "set_due")
    tri = st.session_state.get(_K + "set_tri")
    with _db() as conn:
        _adb.set_setting(conn, "due_date", due.isoformat() if due else None)
        _adb.set_setting(conn, "trimester_override", None if tri == "auto" else tri)
        _adb.set_setting(conn, "week_start", st.session_state.get(_K + "set_week_start"))
    _flash("success", "Settings saved.")


def _render_settings(s: dict, today: date) -> None:
    with st.expander("⚙️ Settings"):
        with st.form(_K + "settings_form", border=False):
            st.date_input("Due date", value=s["due_date"], key=_K + "set_due",
                          min_value=today - timedelta(days=60),
                          max_value=today + timedelta(days=300))
            options = ["auto", "t1", "t2", "t3"]
            current = s["trimester_override"] or "auto"
            st.selectbox("Trimester", options, index=options.index(current), key=_K + "set_tri",
                         format_func=lambda o: "From due date" if o == "auto" else f"Trimester {o[1]}")
            starts = ["monday", "sunday"]
            st.selectbox("Week starts on", starts, key=_K + "set_week_start",
                         index=starts.index(s["week_start"]) if s["week_start"] in starts else 0,
                         format_func=str.capitalize)
            st.form_submit_button("Save settings", on_click=_on_save_settings)


# ---------------------------------------------------------------------------
# Flash messages (callbacks run before the rerun renders)
# ---------------------------------------------------------------------------

def _flash(level: str, msg: str) -> None:
    st.session_state[_K + "flash"] = (level, msg)


def _show_flash() -> None:
    f = st.session_state.pop(_K + "flash", None)
    if f:
        getattr(st, f[0])(f[1])


# ---------------------------------------------------------------------------
# Week navigation
# ---------------------------------------------------------------------------

def _shift_week(days: int) -> None:
    cur = st.session_state.get(_K + "week_of", date.today())
    st.session_state[_K + "week_of"] = cur + timedelta(days=days)


def _this_week() -> None:
    st.session_state.pop(_K + "week_of", None)


# ---------------------------------------------------------------------------
# Adding entries
# ---------------------------------------------------------------------------

def _food_key(food: dict) -> str:
    return f"{food['kind']}:{food['id']}"


def _on_add(food_map: dict[str, dict]) -> None:
    key = st.session_state.get(_K + "add_food")
    if not key or key not in food_map:
        _flash("warning", "Pick a food first.")
        return
    food = food_map[key]
    servings = st.session_state.get(_K + "add_servings") or 1.0
    eaten_on = st.session_state.get(_K + "add_date") or date.today()
    with _db() as conn:
        _log.add_entry(
            conn, eaten_on=eaten_on, kind=food["kind"], label=food["label"], servings=servings,
            recipe_id=food["id"] if food["kind"] == "recipe" else None,
            staple_id=food["id"] if food["kind"] == "staple" else None,
        )
    _flash("success", f"Added {servings:g}× {food['label']}.")


def _on_custom(kind: str, value_keys: list[str]) -> None:
    label = (st.session_state.get(_K + "custom_label") or "").strip()
    if not label:
        _flash("warning", "Describe what you ate first.")
        return
    values = {k: float(st.session_state.get(_K + "custom_" + k) or 0.0) for k in value_keys}
    if kind == "custom" and not any(values.values()):
        _flash("warning", "All amounts are 0 — fill some in, or use “Figure it out later”.")
        return
    eaten_on = st.session_state.get(_K + "custom_date") or date.today()
    with _db() as conn:
        _log.add_entry(conn, eaten_on=eaten_on, kind=kind, label=label,
                       values=values if kind == "custom" else None)
    if kind == "pending":
        _flash("success", f"Saved “{label}” — it will count once it's broken down.")
    else:
        _flash("success", f"Added “{label}”.")


def _render_add(conn, targets, today: date) -> None:
    foods = _log.list_foods(conn)
    food_map = {_food_key(f): f for f in foods}
    with st.form(_K + "add_form", clear_on_submit=True):
        st.selectbox(
            "Log food", options=list(food_map), index=None, key=_K + "add_food",
            format_func=lambda k: _view.food_option_label(food_map[k]),
            placeholder="Search recipes & staples…",
        )
        c1, c2 = st.columns(2)
        c1.number_input("Servings", min_value=0.25, max_value=20.0, value=1.0, step=0.25,
                        key=_K + "add_servings")
        c2.date_input("Date", value=today, max_value=today, key=_K + "add_date")
        st.form_submit_button("Add", type="primary", width="stretch",
                              on_click=_on_add, args=(food_map,))

    with st.expander("✏️ Something not on the list"):
        value_keys = targets.value_keys()
        with st.form(_K + "custom_form", clear_on_submit=True, border=False):
            st.text_input("What did you eat?", key=_K + "custom_label",
                          placeholder="e.g. large bowl of chicken pho")
            st.date_input("Date", value=today, max_value=today, key=_K + "custom_date")
            st.caption("Know the amounts? Fill in what applies. Otherwise just tap "
                       "**Figure it out later** and it'll be broken down at the next "
                       "breakdown run.")
            st.form_submit_button("Figure it out later", width="stretch",
                                  on_click=_on_custom, args=("pending", value_keys))
            cols = st.columns(2)
            for i, k in enumerate(value_keys):
                t = targets.get(k)
                step = 10.0 if t.unit in ("mg",) else (1.0 if t.unit == "g" else 0.25)
                cols[i % 2].number_input(f"{t.label} ({t.unit})", min_value=0.0, value=0.0,
                                         step=step, key=_K + "custom_" + k)
            st.form_submit_button("Save with these amounts", type="primary", width="stretch",
                                  on_click=_on_custom, args=("custom", value_keys))


# ---------------------------------------------------------------------------
# Editing entries
# ---------------------------------------------------------------------------

def _on_save_entry(entry_id: int) -> None:
    with _db() as conn:
        _log.update_entry(conn, entry_id,
                          servings=st.session_state.get(f"{_K}srv_{entry_id}"),
                          eaten_on=st.session_state.get(f"{_K}date_{entry_id}"))
    _flash("success", "Entry updated.")


def _on_delete_entry(entry_id: int, label: str) -> None:
    with _db() as conn:
        _log.delete_entry(conn, entry_id)
    for k in (f"{_K}srv_{entry_id}", f"{_K}date_{entry_id}"):
        st.session_state.pop(k, None)
    _flash("success", f"Deleted {label}.")


def _render_days(entries, resolver, targets, tri, start, end, today) -> None:
    by_day: dict[date, list[dict]] = {}
    for e in entries:
        by_day.setdefault(date.fromisoformat(e["eaten_on"]), []).append(e)
    days = set(by_day)
    if start <= today <= end:
        days.add(today)
    if not days:
        st.info("Nothing logged this week.")
        return
    for d in sorted(days, reverse=True):
        day_entries = by_day.get(d, [])
        n = len(day_entries)
        title = f"{_view.day_label(d, today)} · {n} item{'s' if n != 1 else ''}"
        with st.expander(title, expanded=(d == today)):
            day_tot = _tr.weekly_totals(
                _tr.sum_values([resolver.values(e) for e in day_entries]), targets, tri)
            st.caption(_view.day_summary(day_tot))
            for e in day_entries:
                _, problem = resolver.per_serving(e)
                with st.popover(_view.entry_label(e, problem), width="stretch"):
                    st.number_input("Servings", min_value=0.25, max_value=20.0, step=0.25,
                                    value=float(e["servings"]), key=f"{_K}srv_{e['id']}")
                    st.date_input("Date", value=date.fromisoformat(e["eaten_on"]),
                                  max_value=today, key=f"{_K}date_{e['id']}")
                    c1, c2 = st.columns(2)
                    c1.button("Save", key=f"{_K}save_{e['id']}", type="primary", width="stretch",
                              on_click=_on_save_entry, args=(e["id"],))
                    c2.button("Delete", key=f"{_K}del_{e['id']}", width="stretch",
                              on_click=_on_delete_entry, args=(e["id"], e["label"]))


# ---------------------------------------------------------------------------
# Breakdown audit / overrides
# ---------------------------------------------------------------------------

def _on_save_overrides(recipe_id: int, computed: dict[str, float], keys: list[str]) -> None:
    changed = 0
    with _db() as conn:
        for k in keys:
            v = float(st.session_state.get(f"{_K}ovr_{recipe_id}_{k}") or 0.0)
            if abs(v - float(computed.get(k, 0.0))) > 1e-9:
                _bd.set_override(conn, recipe_id, k, v)
                changed += 1
            else:
                _bd.clear_override(conn, recipe_id, k)
    _flash("success", f"Saved breakdown edits ({changed} value{'s' if changed != 1 else ''} overridden).")


def _on_reset_overrides(recipe_id: int, keys: list[str]) -> None:
    with _db() as conn:
        _bd.clear_override(conn, recipe_id)
    for k in keys:
        st.session_state.pop(f"{_K}ovr_{recipe_id}_{k}", None)
    _flash("success", "Overrides cleared — using the computed breakdown.")


def _values_frame(targets, values: dict, overrides: dict | None = None) -> pd.DataFrame:
    rows = []
    for k in targets.value_keys():
        t = targets.get(k)
        v = values.get(k, 0.0)
        rows.append({
            "Food group": t.label,
            "Per serving": _view.fmt_amount(v, t.unit) if t.unit in ("mg", "g") else f"{v:.2f}",
            "Unit": t.unit,
            "Edited": "✎" if overrides and k in overrides else "",
        })
    return pd.DataFrame(rows)


def _render_breakdowns(conn, targets) -> None:
    with st.expander("🔍 Food breakdowns — check or edit"):
        foods = _log.list_foods(conn)
        food_map = {_food_key(f): f for f in foods}
        key = st.selectbox("Recipe or staple", options=list(food_map), index=None,
                           key=_K + "bd_pick", placeholder="Pick a food…",
                           format_func=lambda k: _view.food_option_label(food_map[k]))
        if not key:
            return
        food = food_map[key]
        if food["kind"] == "staple":
            s = _log.staple(conn, food["id"])
            st.caption(f"Staple · confidence {s['confidence']}" + (f" · {s['notes']}" if s["notes"] else ""))
            st.dataframe(_values_frame(targets, s["values"]), hide_index=True)
            if s["breakdown"]:
                st.dataframe(pd.DataFrame(s["breakdown"]), hide_index=True)
            return

        rid = food["id"]
        n = _bd.recipe_nutrition(conn, rid, targets)
        if not n.has_breakdown:
            st.warning("No breakdown yet — this recipe counts as zero until the next breakdown run.")
        else:
            st.caption(f"Confidence **{n.confidence}** · {n.model or ''}"
                       + (f" · {n.notes}" if n.notes else ""))
            if n.stale:
                st.warning("The recipe changed since this was computed — still counted with the "
                           "old numbers until the next breakdown run.")
        if n.safety_flags:
            st.error("Pregnancy safety: " + "; ".join(targets.safety_flags.get(f, f) for f in n.safety_flags))
        if n.stale_overrides:
            st.warning("Some edits were made before the recipe last changed — double-check them: "
                       + ", ".join(targets.get(k).label for k in n.stale_overrides if k in targets.value_keys()))
        st.dataframe(_values_frame(targets, n.values, n.overrides), hide_index=True)
        if n.breakdown:
            st.markdown("**How it was worked out** (per serving)")
            st.dataframe(pd.DataFrame(n.breakdown), hide_index=True)

        keys = targets.value_keys()
        with st.popover("✎ Edit breakdown"):
            with st.form(f"{_K}ovr_form_{rid}", border=False):
                st.caption("Change any per-serving value. Edits stick even when the breakdown is recomputed.")
                cols = st.columns(2)
                for i, k in enumerate(keys):
                    t = targets.get(k)
                    cols[i % 2].number_input(f"{t.label} ({t.unit})", min_value=0.0,
                                             value=float(n.values.get(k, 0.0)),
                                             step=10.0 if t.unit == "mg" else 0.05,
                                             key=f"{_K}ovr_{rid}_{k}")
                st.form_submit_button("Save edits", type="primary",
                                      on_click=_on_save_overrides, args=(rid, n.computed, keys))
            if n.overrides:
                st.button("Reset to computed", key=f"{_K}ovr_reset_{rid}",
                          on_click=_on_reset_overrides, args=(rid, keys))


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def _render_history(conn, targets, s, current_start: date) -> None:
    with st.expander("📅 Past weeks"):
        first = _log.first_entry_date(conn)
        if first is None or first >= current_start:
            st.caption("No past weeks yet.")
            return
        resolver = _log.ValueResolver(conn, targets)
        first_start, _ = _tr.week_bounds(first, s["week_start"])
        rows = []
        wk = current_start - timedelta(days=7)
        while wk >= first_start and len(rows) < _HISTORY_WEEKS:
            entries = _log.entries_between(conn, wk, wk + timedelta(days=6))
            tri = _tr.trimester(s["due_date"], wk, s["trimester_override"])
            totals = _tr.weekly_totals(_tr.sum_values([resolver.values(e) for e in entries]),
                                       targets, tri)
            row = {"Week": _view.week_label(wk, wk + timedelta(days=6)), "Tri": tri.upper()}
            for k in _HISTORY_KEYS:
                t = targets.get(k)
                row[t.label.split(" (")[0]] = f"{_tr.percent(t, totals.get(k, 0.0), tri):.0f}%"
            rows.append(row)
            wk -= timedelta(days=7)
        st.caption("% of each weekly target reached.")
        st.dataframe(pd.DataFrame(rows), hide_index=True)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _render_inner() -> None:
    targets = load_targets()
    today = date.today()
    with _db() as conn:
        s = _settings(conn, targets)
        tri = _tr.trimester(s["due_date"], today, s["trimester_override"])
        ga = _tr.gestational_age(s["due_date"], today) if s["due_date"] else None

        st.caption(_DISCLAIMER)
        st.markdown(_view.trimester_line(tri, ga, s["trimester_override"] is not None))
        _show_flash()

        _render_add(conn, targets, today)

        week_of = st.session_state.get(_K + "week_of", today)
        start, end = _tr.week_bounds(week_of, s["week_start"])
        nav = st.container(horizontal=True, vertical_alignment="center")
        nav.button("◀", key=_K + "prev_week", on_click=_shift_week, args=(-7,))
        nav.markdown(f"**Week of {_view.week_label(start, end)}**")
        nav.button("▶", key=_K + "next_week", on_click=_shift_week, args=(7,),
                   disabled=end >= today)
        if not start <= today <= end:
            nav.button("This week", key=_K + "this_week", on_click=_this_week)

        entries = _log.entries_between(conn, start, end)
        resolver = _log.ValueResolver(conn, targets)
        totals = _tr.weekly_totals(_tr.sum_values([resolver.values(e) for e in entries]),
                                   targets, tri)
        pace_frac = _tr.pace(today, start)
        st.markdown(_view.bars_html(targets, totals, tri, pace_frac), unsafe_allow_html=True)
        if totals.get("legume_protein", 0) > 0:
            st.caption(f"Beans/lentils beyond the vegetable target count as "
                       f"{totals['legume_protein']:.1f} oz eq of protein.")
        uncounted = [e for e in entries if not resolver.per_serving(e)[0]]
        if uncounted:
            st.caption(f"⏳ {len(uncounted)} entr{'y' if len(uncounted) == 1 else 'ies'} "
                       "not counted yet — waiting for a breakdown.")

        _render_days(entries, resolver, targets, tri, start, end, today)
        _render_breakdowns(conn, targets)
        _render_history(conn, targets, s, _tr.week_bounds(today, s["week_start"])[0])
        _render_settings(s, today)
