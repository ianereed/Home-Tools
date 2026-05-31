"""Phase 16 Chunk 2 — Extract recipe from a NAS photo and seed the DB.

Triggered by meal_planner_photo_intake_scan for each new photo sha.
On success: inserts recipe row + tag + ingredients; moves file to _done/.
On non-ok extraction: records error in photos_intake, leaves file in _processing/.
Card UX (Chunk 3) and wedge logic (Chunk 4) are not yet wired.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from jobs import huey, requires, requires_model
from meal_planner import db as _db
from meal_planner.db import add_recipe_tag, insert_recipe
from meal_planner.eval.preprocess_images import _process_one
from meal_planner.seed_from_sheet import _insert_ingredients_batch
from meal_planner.vision import intake_db
from meal_planner.vision.extract import extract_recipe_from_photo

logger = logging.getLogger(__name__)

_DEFAULT_INTAKE_DIR = "/Users/homeserver/Share1/Documents/Recipes/photo-intake"


# No huey-level retries: the outer except below marks the row ollama_error
# before re-raising, and the skip-check at the top gates on status=='pending'.
# With retries enabled, huey would re-dequeue the task only to have it return
# skipped_already_handled — wasted queue cycles. Recovery for ollama_error rows
# is owned by Chunk 4 wedge logic.
@huey.task()
@requires_model("vision", keep_alive=300, batch_hint="drain")
@requires(["fs:meal_planner", "model:llama3.2-vision:11b"])
def meal_planner_ingest_photo(sha: str) -> dict:
    row = intake_db.get_by_sha(sha)
    if row is None or row.status != "pending":
        logger.info("meal_planner_ingest_photo: sha=%s status=%s — skip", sha, row.status if row else "missing")
        return {"sha": sha, "status": "skipped_already_handled", "recipe_id": None, "latency_s": None}

    intake_db.mark_status(sha, "extracting")

    try:
        nas_path = Path(row.nas_path)
        intake_dir = Path(os.environ.get("MEAL_PLANNER_NAS_INTAKE_DIR", _DEFAULT_INTAKE_DIR))
        done_dir = intake_dir / "_done"
        done_path = done_dir / f"{sha}.jpg"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            preprocessed = tmp / f"{sha}.jpg"
            _process_one(
                src=nas_path,
                dst=preprocessed,
                max_dim=1500,
                autocontrast_cutoff=2,
                log_path=tmp / "preprocess.log",
            )
            result = extract_recipe_from_photo(
                preprocessed,
                timeout_s=500,
                keep_alive="300s",
            )

        if result.status == "ok":
            # Option B: rename first so photo_path always points at a real file.
            # If rename fails, the outer try/except catches it → ollama_error, no recipe row.
            done_dir.mkdir(parents=True, exist_ok=True)
            nas_path.rename(done_path)

            db_path = _db.DB_PATH
            conn = _db._get_conn(db_path)
            try:
                title = (result.parsed.get("title") or "") or sha
                _raw_instr = result.parsed.get("instructions")
                if isinstance(_raw_instr, str):
                    instructions = _raw_instr.strip() or None
                else:
                    instructions = None
                recipe_id = insert_recipe(
                    title=title,
                    source="nas-intake",
                    photo_path=str(done_path),
                    instructions=instructions,
                    conn=conn,
                )
                add_recipe_tag(recipe_id, "photo-intake", conn=conn)
                for raw_tag in result.parsed.get("tags", []):
                    if not isinstance(raw_tag, str):
                        continue
                    t = raw_tag.strip()
                    if not t:
                        continue
                    add_recipe_tag(recipe_id, t, conn=conn)
                ing_count, ing_warnings = _insert_ingredients_batch(
                    recipe_id=recipe_id,
                    parsed=result.parsed.get("ingredients", []),
                    base_servings=4,
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
                # Sidecar captures post-normalize output (Chunk F). Raw LLM text is in
                # result.metadata["raw_response"]; normalize_warnings in metadata too.
                sidecar_path = done_dir / f"{sha}.json"
                sidecar_path.write_text(json.dumps(result.parsed, indent=2, ensure_ascii=False))
            except Exception as _sidecar_exc:
                logger.warning("meal_planner_ingest_photo: sidecar write failed sha=%s: %s", sha, _sidecar_exc)

            # Merge normalize_warnings (qty/unit splits, discarded unit content)
            # into the persisted warnings so the DB reflects every transformation
            # applied between raw LLM output and stored ingredients.
            norm_warnings = result.normalize_warnings or []
            all_warnings = list(ing_warnings) + list(norm_warnings)
            if all_warnings:
                intake_db.mark_status(
                    sha, "ok_partial",
                    recipe_id=recipe_id, extraction_path="ollama",
                    extraction_warnings=json.dumps(all_warnings),
                )
                status_for_return = "ok_partial"
            else:
                intake_db.mark_status(sha, "ok", recipe_id=recipe_id, extraction_path="ollama")
                status_for_return = "ok"
            logger.info(
                "meal_planner_ingest_photo: %s sha=%s recipe_id=%d ing_warns=%d norm_warns=%d",
                status_for_return, sha, recipe_id, len(ing_warnings), len(norm_warnings),
            )
            return {"sha": sha, "status": status_for_return, "recipe_id": recipe_id,
                    "latency_s": result.latency_s,
                    "warning_count": len(all_warnings),
                    "normalize_warning_count": len(norm_warnings)}

        intake_db.mark_status(sha, result.status, error=result.error)
        logger.warning(
            "meal_planner_ingest_photo: %s sha=%s error=%s",
            result.status, sha, (result.error or "")[:200],
        )
        return {"sha": sha, "status": result.status, "recipe_id": None, "latency_s": result.latency_s}

    except Exception as exc:
        intake_db.mark_status(
            sha, "ollama_error",
            error=f"ingest crash: {exc!r}"[:500],
        )
        raise
