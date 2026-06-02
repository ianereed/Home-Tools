"""Synchronous, huey-free meal-planner runners.

Why this module exists:
The Streamlit console (`console/`) cannot import `jobs.huey` —
`jobs/__init__.py` opens a SQLite WAL connection at import time that
holds an orphan fd in the Streamlit process and silently drops
enqueues elsewhere on the host (see memory entry
`feedback_streamlit_in_process_huey.md`).

So the actual implementations of `meal_planner_iphone_intake` and
`meal_planner_send_to_todoist` live here, in a module that never
touches huey. The `jobs/kinds/*.py` files become thin `@huey.task()`
wrappers; the Streamlit Capture tab calls these functions directly.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from meal_planner import db as _db
from meal_planner import todoist_client as todoist_adapter
from meal_planner.db import add_recipe_tag, delete_recipe, insert_recipe
from meal_planner.queries import get_recipe
from meal_planner.scaling import scale_ingredients
from meal_planner.sections import SKIP_SECTION
from meal_planner.seed_from_sheet import _insert_ingredients_batch
from meal_planner.vision import intake_db
from meal_planner.vision.gemini_fallback import call_gemini_vision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# iPhone intake — paths
# ---------------------------------------------------------------------------

_DEFAULT_IPHONE_INTAKE_DIR = str(
    Path.home() / "Home-Tools" / "jobs" / "data" / "iphone-intake"
)

_ALLOWED_INTENTS = frozenset({"save", "save_and_shop", "shop_only"})

MEALS_SECTION_NAME = "Meals"


def iphone_intake_dir() -> Path:
    """Resolves to MEAL_PLANNER_IPHONE_INTAKE_DIR if set, otherwise the
    default under ~/Home-Tools/jobs/data/iphone-intake."""
    return Path(os.environ.get("MEAL_PLANNER_IPHONE_INTAKE_DIR", _DEFAULT_IPHONE_INTAKE_DIR))


# ---------------------------------------------------------------------------
# send-to-Todoist core
# ---------------------------------------------------------------------------


def send_recipes_to_todoist_sync(recipe_scales: list[list]) -> dict:
    """Synchronous Todoist push. Used by the @huey.task wrapper AND by
    in-process callers (Phase 21 iPhone intake's shop_only path; the
    Streamlit Capture tab).

    recipe_scales: list of [recipe_id, target_servings] pairs.

    Required env:
        TODOIST_SECTIONS — JSON {section_name: section_id}; must include "Meals".

    Optional env:
        TODOIST_PROJECT_ID — defaults to inbox.
    """
    sections: dict[str, str] = json.loads(os.environ["TODOIST_SECTIONS"])
    fallback_name: str = next(iter(sections))

    if MEALS_SECTION_NAME not in sections:
        raise RuntimeError(
            f"TODOIST_SECTIONS is missing '{MEALS_SECTION_NAME}' section. "
            "Add it to meal_planner/.env and kickstart the consumer."
        )
    meals_section_id = sections[MEALS_SECTION_NAME]

    project_id = os.environ.get("TODOIST_PROJECT_ID")
    sent = 0
    attempted = 0
    skipped = 0  # ingredients marked "Skip" (household staples) — not sent

    # Buffer every ingredient task across all recipes so we can group "like
    # items" within each Todoist section. Todoist displays tasks in creation
    # order within a section, so creating same-section items consecutively and
    # name-sorted makes similar ingredients ("chicken thigh", "chicken
    # drumstick") land adjacent instead of scattered by source recipe. Recipe
    # headers are still created up front, in recipe order, so the Meals section
    # reads top-to-bottom by recipe.
    pending: list[dict] = []

    for rid, target_servings in recipe_scales:
        recipe = get_recipe(int(rid))
        scaled = scale_ingredients(recipe, int(target_servings))

        attempted += 1
        header_title = f"{recipe.title} ({int(target_servings)} servings)"
        header_result = todoist_adapter.create_task(
            output_config={
                "project_id": project_id,
                "section_id": meals_section_id,
                "labels": ["meal-planner"],
            },
            payload={
                "title": header_title,
                "source": "meal-planner",
                "source_id": f"recipes:{recipe.id}:header",
                "priority": "normal",
                "confidence": 1.0,
            },
        )
        if header_result.get("created"):
            sent += 1

        for ingredient in scaled:
            # Household staples kept in stock are marked "Skip": keep them on the
            # recipe for reference, but never add them to the grocery list.
            if ingredient.todoist_section == SKIP_SECTION:
                skipped += 1
                continue

            qty = ingredient.qty_per_serving
            if qty is not None:
                qty_str = f"{qty:.4g}"
                if ingredient.unit:
                    base = f"{qty_str} {ingredient.unit} {ingredient.name}"
                else:
                    base = f"{qty_str} {ingredient.name}"
            else:
                base = ingredient.name
            title = f"{base.strip()} ({recipe.title})"

            section_name = (
                ingredient.todoist_section
                if ingredient.todoist_section in sections
                else fallback_name
            )
            pending.append({
                "section_name": section_name,
                # group key: ingredient name (qty-stripped), so "1 cup flour"
                # and "2 cups flour" from different recipes sort together.
                "sort_key": (ingredient.name or "").strip().lower(),
                "title": title,
                "recipe_id": recipe.id,
            })

    # Group like items: order by section, then ingredient name. Creating tasks
    # in this order makes same-section items land adjacent and alphabetized.
    pending.sort(key=lambda t: (t["section_name"], t["sort_key"]))

    for task in pending:
        attempted += 1
        section_id = sections[task["section_name"]]
        result = todoist_adapter.create_task(
            output_config={
                "project_id": project_id,
                "section_id": section_id,
                "labels": ["meal-planner"],
            },
            payload={
                "title": task["title"],
                "source": "meal-planner",
                "source_id": f"recipes:{task['recipe_id']}",
                "priority": "normal",
                "confidence": 1.0,
            },
        )
        if result.get("created"):
            sent += 1

    logger.info(
        "send_recipes_to_todoist_sync: sent %d/%d items (%d skipped staples)",
        sent, attempted, skipped,
    )
    return {
        "items_sent": sent,
        "items_attempted": attempted,
        "items_skipped": skipped,
        "consolidate_failed": None,
        "consolidate_dropped": 0,
        "error": None,
    }


# ---------------------------------------------------------------------------
# iPhone intake core
# ---------------------------------------------------------------------------


def process_iphone_intake_sync(sha: str, intent: str, servings: int = 4) -> dict:
    """Run the iPhone intake pipeline end-to-end, synchronously.

    Called from both contexts:
      - The @huey.task wrapper in jobs/kinds/meal_planner_iphone_intake.py
        (when something enqueues it via /jobs HTTP).
      - The Streamlit Capture tab in console/tabs/capture.py (direct call;
        user waits on the upload form).

    Behavior:
      - save: insert recipe with source="iphone", return recipe_id.
      - save_and_shop: insert + run send_recipes_to_todoist_sync directly
        (synchronous — caller waits). Result includes items_sent.
      - shop_only: insert with source="iphone-shop-only", run send sync,
        then delete the recipe row on success. FK cascades clean up.

    Status vocab: ok, ok_partial, parse_fail, ollama_error, timeout,
    config_error, missing_file, todoist_failed, skipped_already_handled.
    """
    if intent not in _ALLOWED_INTENTS:
        raise ValueError(
            f"bad intent: {intent!r}; expected one of {sorted(_ALLOWED_INTENTS)}"
        )

    row = intake_db.get_by_sha(sha)
    if row is None or row.status != "pending":
        logger.info(
            "process_iphone_intake_sync: sha=%s status=%s — skip",
            sha, row.status if row else "missing",
        )
        return {
            "sha": sha, "intent": intent, "status": "skipped_already_handled",
            "recipe_id": None, "latency_s": None,
        }

    intake_db.mark_status(sha, "extracting")

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        intake_db.mark_status(sha, "ollama_error", error="GEMINI_API_KEY not set")
        return {
            "sha": sha, "intent": intent, "status": "config_error",
            "recipe_id": None, "latency_s": None,
            "error": "GEMINI_API_KEY not set",
        }

    intake_dir = iphone_intake_dir()
    processing_dir = intake_dir / "_processing"
    done_dir = intake_dir / "_done"
    processing_path = processing_dir / f"{sha}.jpg"
    done_path = done_dir / f"{sha}.jpg"

    if not processing_path.exists():
        intake_db.mark_status(sha, "ollama_error", error=f"missing file: {processing_path}")
        return {
            "sha": sha, "intent": intent, "status": "missing_file",
            "recipe_id": None, "latency_s": None,
        }

    try:
        parsed, metadata = call_gemini_vision(processing_path, api_key=api_key)
    except Exception as exc:
        intake_db.mark_status(sha, "ollama_error", error=f"gemini crash: {exc!r}"[:500])
        logger.exception("process_iphone_intake_sync: gemini crash sha=%s", sha)
        raise

    latency_s = metadata.get("latency_s")
    raw_status = metadata.get("http_status")

    if parsed is None:
        if raw_status is None:
            status = "timeout"
        elif raw_status == 200:
            status = "parse_fail"
        else:
            status = "ollama_error"
        intake_db.mark_status(sha, status, error=(metadata.get("raw_response") or "")[:500])
        logger.warning(
            "process_iphone_intake_sync: %s sha=%s http=%s",
            status, sha, raw_status,
        )
        return {
            "sha": sha, "intent": intent, "status": status,
            "recipe_id": None, "latency_s": latency_s,
        }

    # Move file into _done/ before DB insert so photo_path always points at a real file.
    done_dir.mkdir(parents=True, exist_ok=True)
    processing_path.rename(done_path)

    db_path = _db.DB_PATH
    source_label = "iphone-shop-only" if intent == "shop_only" else "iphone"
    title = (parsed.get("title") or "") or sha
    _raw_instr = parsed.get("instructions")
    instructions = _raw_instr.strip() if isinstance(_raw_instr, str) and _raw_instr.strip() else None
    _raw_book = parsed.get("recipe_book")
    recipe_book = _raw_book.strip() if isinstance(_raw_book, str) and _raw_book.strip() else None

    conn = _db._get_conn(db_path)
    try:
        recipe_id = insert_recipe(
            title=title,
            base_servings=servings,
            source=source_label,
            photo_path=str(done_path),
            instructions=instructions,
            recipe_book=recipe_book,
            conn=conn,
        )
        add_recipe_tag(recipe_id, "iphone-intake", conn=conn)
        for raw_tag in parsed.get("tags", []) or []:
            if not isinstance(raw_tag, str):
                continue
            t = raw_tag.strip()
            if t:
                add_recipe_tag(recipe_id, t, conn=conn)
        ing_count, ing_warnings = _insert_ingredients_batch(
            recipe_id=recipe_id,
            parsed=parsed.get("ingredients", []) or [],
            base_servings=servings,
            path=db_path,
            conn=conn,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    try:
        sidecar_path = done_dir / f"{sha}.json"
        sidecar_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False))
    except Exception as sidecar_exc:
        logger.warning(
            "process_iphone_intake_sync: sidecar write failed sha=%s: %s",
            sha, sidecar_exc,
        )

    norm_warnings = metadata.get("normalize_warnings") or []
    all_warnings = list(ing_warnings) + list(norm_warnings)
    intake_db.mark_status(
        sha,
        "ok_partial" if all_warnings else "ok",
        recipe_id=recipe_id,
        extraction_path="gemini",
        extraction_warnings=json.dumps(all_warnings) if all_warnings else None,
    )

    base_result = {
        "sha": sha,
        "intent": intent,
        "recipe_id": recipe_id,
        "latency_s": latency_s,
        "warning_count": len(all_warnings),
    }

    if intent == "save":
        base_result["status"] = "ok"
        return base_result

    # save_and_shop and shop_only both run send synchronously. The Streamlit
    # caller is waiting on the upload form; we want a definitive result, not
    # an enqueued task id. (For the worker path, this just means the send
    # runs inside the same task instead of as a second one — same outcome.)
    try:
        send_result = send_recipes_to_todoist_sync([[recipe_id, servings]])
    except Exception as exc:
        logger.exception(
            "process_iphone_intake_sync: %s Todoist crash recipe_id=%d",
            intent, recipe_id,
        )
        base_result["status"] = "todoist_failed"
        base_result["error"] = repr(exc)[:500]
        return base_result

    if send_result.get("error") or send_result.get("items_sent", 0) == 0:
        base_result["status"] = "todoist_failed"
        base_result["error"] = send_result.get("error") or "no items sent"
        base_result["items_attempted"] = send_result.get("items_attempted", 0)
        return base_result

    base_result["items_sent"] = send_result.get("items_sent", 0)

    if intent == "shop_only":
        deleted = delete_recipe(recipe_id, path=db_path)
        logger.info(
            "process_iphone_intake_sync: shop_only deleted recipe_id=%d (rows=%d) "
            "after sending %d items",
            recipe_id, deleted, send_result.get("items_sent", 0),
        )
        base_result["recipe_id"] = None

    base_result["status"] = "ok"
    return base_result
