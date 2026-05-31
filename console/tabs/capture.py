"""Capture tab — iPhone-driven recipe intake via dashboard upload (Phase 21 v2).

Replaces the original Phase 21 Apple Shortcut path. The user opens this tab
from a home-screen icon on their iPhone (Safari → Add to Home Screen pointed
at `homeserver:8503/?tab=capture`), uploads a photo of a recipe, picks an
intent, and waits ~10 s for the result.

Calls `meal_planner.runner.process_iphone_intake_sync` directly — no huey
enqueue, no polling. The runner module is huey-free by construction so
Streamlit doesn't hold an orphan WAL fd on jobs.db (see memory entry
`feedback_streamlit_in_process_huey.md`).
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

import streamlit as st

from meal_planner import db as _db
from meal_planner.runner import iphone_intake_dir, process_iphone_intake_sync
from meal_planner.vision import intake_db


_INTENT_LABELS = {
    "save": "Save to library",
    "save_and_shop": "Save and send to Todoist",
    "shop_only": "Send to Todoist only (don't save the recipe)",
}


def render() -> None:
    st.markdown("### Capture a recipe")
    st.caption(
        "Snap a recipe photo (cookbook, magazine, handout). Pick what to do "
        "with it. Extraction uses Gemini — usually 5–15 seconds."
    )

    intent = st.radio(
        "What do you want to do?",
        options=list(_INTENT_LABELS.keys()),
        format_func=lambda k: _INTENT_LABELS[k],
        index=1,  # default to save_and_shop — common case
        horizontal=False,
        key="capture_intent",
    )

    servings = st.number_input(
        "Servings to scale to (for Todoist)",
        min_value=1,
        max_value=32,
        value=4,
        step=1,
        key="capture_servings",
    )

    uploaded = st.file_uploader(
        "Photo of the recipe",
        type=["jpg", "jpeg", "png", "heic"],
        accept_multiple_files=False,
        key="capture_upload",
    )

    if uploaded is not None:
        _handle_upload(uploaded, intent, int(servings))

    st.divider()
    _render_recent()


def _handle_upload(uploaded, intent: str, servings: int) -> None:
    photo_bytes = uploaded.getbuffer().tobytes()
    if not photo_bytes:
        st.error("Empty file.")
        return

    sha = hashlib.sha256(photo_bytes).hexdigest()[:16]

    intake_dir = iphone_intake_dir()
    processing_dir = intake_dir / "_processing"
    processing_dir.mkdir(parents=True, exist_ok=True)
    photo_path = processing_dir / f"{sha}.jpg"

    first_time = intake_db.record_intake(
        sha,
        source_path=uploaded.name or "capture-upload",
        nas_path=str(photo_path),
        source="iphone",
    )

    if not first_time:
        st.warning(
            f"Already processed (sha={sha}). Existing row: "
            f"see status below in 'Recent intakes'."
        )
        return

    try:
        photo_path.write_bytes(photo_bytes)
    except OSError as exc:
        st.error(f"Could not write photo to {photo_path}: {exc}")
        return

    with st.spinner("Extracting recipe via Gemini…"):
        result = process_iphone_intake_sync(sha, intent, servings)

    _render_result(result)


def _render_result(result: dict) -> None:
    status = result.get("status")
    intent = result.get("intent", "?")

    if status == "ok":
        recipe_id = result.get("recipe_id")
        items_sent = result.get("items_sent")
        if intent == "save":
            st.success(
                f"Saved recipe #{recipe_id}. "
                f"Open it on the [Recipes tab](?tab=recipes)."
            )
        elif intent == "save_and_shop":
            st.success(
                f"Saved recipe #{recipe_id} and sent {items_sent} items to Todoist."
            )
        elif intent == "shop_only":
            st.success(
                f"Sent {items_sent} items to Todoist. Recipe not saved (shop-only)."
            )
        else:
            st.success(f"Done: {result}")
        warning_count = result.get("warning_count") or 0
        if warning_count:
            st.caption(
                f"Note: {warning_count} extraction warnings — view in the "
                f"intake row's `extraction_warnings` field."
            )
        return

    if status == "todoist_failed":
        st.error(
            f"Recipe saved (#{result.get('recipe_id')}) but Todoist push failed: "
            f"{result.get('error', 'unknown error')}. Retry from the Recipes tab."
        )
        return

    if status == "skipped_already_handled":
        st.info("Already processed — nothing to do.")
        return

    if status in ("parse_fail", "ollama_error", "timeout", "missing_file", "config_error"):
        st.error(
            f"Extraction failed ({status}): {result.get('error', '(see logs)')}"
        )
        return

    st.warning(f"Unexpected status: {result}")


def _render_recent() -> None:
    """List the 10 most recent iPhone intakes for quick status checking."""
    st.subheader("Recent intakes")
    try:
        with _db._get_conn(_db.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT pi.sha, pi.status, pi.enqueued_at, pi.completed_at,
                       pi.recipe_id, pi.error, r.title
                  FROM photos_intake pi
                  LEFT JOIN recipes r ON r.id = pi.recipe_id
                 WHERE pi.source = 'iphone'
                 ORDER BY pi.enqueued_at DESC
                 LIMIT 10
                """
            ).fetchall()
    except sqlite3.OperationalError as exc:
        st.caption(f"(no intakes yet — {exc})")
        return

    if not rows:
        st.caption("(no iPhone intakes yet)")
        return

    display = []
    for r in rows:
        ts = (r["completed_at"] or r["enqueued_at"] or "")[:19].replace("T", " ")
        title = r["title"] or "—"
        err = (r["error"] or "")[:60]
        display.append({
            "time": ts,
            "status": r["status"],
            "recipe": title if r["recipe_id"] else "—",
            "sha": r["sha"][:8],
            "error": err,
        })
    st.dataframe(display, hide_index=True, use_container_width=True)
