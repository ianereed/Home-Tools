"""Phase 21 — iPhone-driven recipe intake.

An Apple Shortcut on the user's phone POSTs a photo + intent to
POST /iphone-intake on jobs-http (see jobs/enqueue_http.py). That handler
writes the photo to IPHONE_INTAKE_DIR/_processing/<sha>.jpg, records the
intake row, and enqueues this kind.

This kind:
  1. Calls Gemini (faster + more accurate than the local llama3.2-vision
     on single photos) to extract the recipe.
  2. Branches on `intent`:
       - "save"          → insert recipe, return recipe_id.
       - "save_and_shop" → insert recipe, enqueue send-to-todoist (fire-and-forget).
       - "shop_only"     → insert with source="iphone-shop-only", run the
                           Todoist push synchronously, then hard-delete the
                           recipe row (FK cascades clean up). Audit trail
                           survives in the sidecar JSON.

Deliberate decoupling from the existing NAS pipeline (meal_planner_ingest_photo):
  - No @requires_model decorator — Gemini is remote, doesn't compete for the
    local GPU slot, so this job can run while a NAS-pipeline Ollama call is
    in flight on the same worker (different queue slot, separate kind).
  - Photos live outside the NAS share (~/Home-Tools/jobs/data/iphone-intake/)
    so the SMB mount isn't on the upload critical path.
  - Dedup still shares meal_planner.vision.intake_db — a photo that arrives
    via both paths is processed once.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from jobs import huey
from meal_planner import db as _db
from meal_planner.db import add_recipe_tag, delete_recipe, insert_recipe
from meal_planner.seed_from_sheet import _insert_ingredients_batch
from meal_planner.vision import intake_db
from meal_planner.vision.gemini_fallback import call_gemini_vision

logger = logging.getLogger(__name__)

_DEFAULT_INTAKE_DIR = str(Path.home() / "Home-Tools" / "jobs" / "data" / "iphone-intake")

_ALLOWED_INTENTS = frozenset({"save", "save_and_shop", "shop_only"})


def iphone_intake_dir() -> Path:
    """Resolves to MEAL_PLANNER_IPHONE_INTAKE_DIR if set, otherwise the default
    under ~/Home-Tools/jobs/data/iphone-intake. Imported by the HTTP handler too."""
    return Path(os.environ.get("MEAL_PLANNER_IPHONE_INTAKE_DIR", _DEFAULT_INTAKE_DIR))


@huey.task()
def meal_planner_iphone_intake(sha: str, intent: str, servings: int = 4) -> dict:
    if intent not in _ALLOWED_INTENTS:
        raise ValueError(f"bad intent: {intent!r}; expected one of {sorted(_ALLOWED_INTENTS)}")

    row = intake_db.get_by_sha(sha)
    if row is None or row.status != "pending":
        logger.info(
            "meal_planner_iphone_intake: sha=%s status=%s — skip",
            sha, row.status if row else "missing",
        )
        return {"sha": sha, "intent": intent, "status": "skipped_already_handled",
                "recipe_id": None, "latency_s": None}

    intake_db.mark_status(sha, "extracting")

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        intake_db.mark_status(sha, "ollama_error", error="GEMINI_API_KEY not set")
        return {"sha": sha, "intent": intent, "status": "config_error",
                "recipe_id": None, "latency_s": None,
                "error": "GEMINI_API_KEY not set"}

    intake_dir = iphone_intake_dir()
    processing_dir = intake_dir / "_processing"
    done_dir = intake_dir / "_done"
    processing_path = processing_dir / f"{sha}.jpg"
    done_path = done_dir / f"{sha}.jpg"

    if not processing_path.exists():
        intake_db.mark_status(sha, "ollama_error", error=f"missing file: {processing_path}")
        return {"sha": sha, "intent": intent, "status": "missing_file",
                "recipe_id": None, "latency_s": None}

    try:
        parsed, metadata = call_gemini_vision(processing_path, api_key=api_key)
    except Exception as exc:
        intake_db.mark_status(sha, "ollama_error", error=f"gemini crash: {exc!r}"[:500])
        logger.exception("meal_planner_iphone_intake: gemini crash sha=%s", sha)
        raise

    latency_s = metadata.get("latency_s")
    raw_status = metadata.get("http_status")

    if parsed is None:
        # Map Gemini failure modes back onto the existing intake_db status vocab.
        if raw_status is None:
            status = "timeout"
        elif raw_status == 200:
            status = "parse_fail"
        else:
            status = "ollama_error"
        intake_db.mark_status(sha, status, error=(metadata.get("raw_response") or "")[:500])
        logger.warning(
            "meal_planner_iphone_intake: %s sha=%s http=%s",
            status, sha, raw_status,
        )
        return {"sha": sha, "intent": intent, "status": status,
                "recipe_id": None, "latency_s": latency_s}

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
            "meal_planner_iphone_intake: sidecar write failed sha=%s: %s",
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

    if intent == "save_and_shop":
        # Fire-and-forget — Shortcut polls /jobs/<id> for this kind's task, but the
        # downstream Todoist push runs after this job returns. Recipe is already saved.
        from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist
        send_result = meal_planner_send_to_todoist([[recipe_id, servings]])
        base_result["status"] = "ok"
        base_result["todoist_task_id"] = getattr(send_result, "id", None)
        return base_result

    # shop_only: run send synchronously so we can confirm success before deleting.
    # Single-worker huey can't .get() its own enqueued task, so call the
    # sync helper directly inside this worker.
    from jobs.kinds.meal_planner_send_to_todoist import send_recipes_to_todoist_sync
    try:
        send_result = send_recipes_to_todoist_sync([[recipe_id, servings]])
    except Exception as exc:
        logger.exception("meal_planner_iphone_intake: shop_only Todoist crash recipe_id=%d", recipe_id)
        base_result["status"] = "todoist_failed"
        base_result["error"] = repr(exc)[:500]
        return base_result

    if send_result.get("error") or send_result.get("items_sent", 0) == 0:
        base_result["status"] = "todoist_failed"
        base_result["error"] = send_result.get("error") or "no items sent"
        base_result["items_attempted"] = send_result.get("items_attempted", 0)
        return base_result

    deleted = delete_recipe(recipe_id, path=db_path)
    logger.info(
        "meal_planner_iphone_intake: shop_only deleted recipe_id=%d (rows=%d) after sending %d items",
        recipe_id, deleted, send_result.get("items_sent", 0),
    )
    base_result["recipe_id"] = None
    base_result["items_sent"] = send_result.get("items_sent", 0)
    base_result["status"] = "ok"
    return base_result
