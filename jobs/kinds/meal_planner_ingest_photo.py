"""Phase 16 Chunk 2 — Extract recipe from a NAS photo and seed the DB.

Triggered by meal_planner_photo_intake_scan for each new photo sha.
On success: inserts recipe row + tag + ingredients; moves file to _done/.
On non-ok extraction: records error in photos_intake, leaves file in _processing/.
Card UX (Chunk 3) and wedge logic (Chunk 4) are not yet wired.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from jobs import huey, requires, requires_model
from meal_planner.vision import intake_db, rasterize
from meal_planner.vision.extract import extract_recipe_from_photo, extract_recipe_from_text
from meal_planner.vision.ingest_common import persist_recipe, preprocess_to_image

logger = logging.getLogger(__name__)

_DEFAULT_INTAKE_DIR = "/Users/homeserver/Share1/Documents/Recipes/photo-intake"

# Let PIL open HEIC/HEIF (iPhone photos). Idempotent; cheap to call at import.
rasterize.register_heif()


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
        src_suffix = nas_path.suffix.lower() or ".jpg"
        intake_dir = Path(os.environ.get("MEAL_PLANNER_NAS_INTAKE_DIR", _DEFAULT_INTAKE_DIR))

        # Text-layer fast-path: a digital recipe PDF (e.g. an NYT Cooking
        # printout) carries a clean embedded text layer that reads far more
        # reliably than rasterizing the page and OCRing it with the vision
        # model (which is non-deterministic on dense recipes). Use the text
        # directly when present; scanned/photographed PDFs have no usable text
        # layer and fall through to the vision path below.
        extraction_path_used = "ollama"
        result = None
        if src_suffix in rasterize.PDF_SUFFIXES:
            text_layer = rasterize.extract_text_layer(nas_path)
            if text_layer:
                extraction_path_used = "text-layer"
                logger.info(
                    "meal_planner_ingest_photo: text-layer path sha=%s (%d chars)",
                    sha, len(text_layer),
                )
                result = extract_recipe_from_text(
                    text_layer,
                    timeout_s=500,
                    keep_alive="300s",
                )

        if result is None:
            with tempfile.TemporaryDirectory() as tmpdir:
                preprocessed = preprocess_to_image(nas_path, sha, Path(tmpdir))
                result = extract_recipe_from_photo(
                    preprocessed,
                    timeout_s=500,
                    keep_alive="300s",
                )

        if result.status == "ok":
            return persist_recipe(
                sha, result,
                nas_path=nas_path,
                intake_dir=intake_dir,
                extraction_path=extraction_path_used,
            )

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
