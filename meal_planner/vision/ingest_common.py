"""Shared worker helpers for recipe-photo ingest.

Both the local-model ingest task (`meal_planner_ingest_photo`) and the Gemini
escalation task (`meal_planner_gemini_extract`) need the same two steps:

1. Turn the dropped file into one preprocessed image the extractor can read
   (rasterize PDFs, resize/autocontrast) — `preprocess_to_image`.
2. On a successful extraction, archive the file to `_done/`, insert the recipe +
   tags + ingredients, write the JSON sidecar, and mark `photos_intake` —
   `persist_recipe`.

Keeping these here (rather than duplicating across kinds) means the local and
Gemini paths stay byte-for-byte consistent on how a recipe lands in the DB; only
the extraction call and the success-status label differ.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from meal_planner import db as _db
from meal_planner.db import add_recipe_tag, insert_recipe
from meal_planner.eval.preprocess_images import _process_one
from meal_planner.seed_from_sheet import _insert_ingredients_batch
from meal_planner.vision import intake_db, rasterize
from meal_planner.vision.extract import ExtractResult

logger = logging.getLogger(__name__)

# Long-edge downscale before extraction. Phase 15 validated quality at 1500px;
# raising it preserves dense recipe text (a top cause of vision-path failures on
# otherwise-good images — esp. multi-page PDF stacks, where each page was
# shrinking to ~580px). `thumbnail()` only downscales, so a larger value can
# only add detail. Env-tunable so it can be retuned against the golden corpus
# without a redeploy.
DEFAULT_MAX_DIM = 2000


def _default_max_dim() -> int:
    try:
        return int(os.environ.get("MEAL_PLANNER_VISION_MAX_DIM", DEFAULT_MAX_DIM))
    except ValueError:
        return DEFAULT_MAX_DIM


def move_to_wedged(nas_path: str | Path, intake_dir: Path) -> None:
    """Move a wedged file out of _processing/ into _wedged/ so it stops showing
    as "in processing." Best-effort: a missing file or rename error is logged and
    swallowed (the DB row is already the source of truth, marked wedged). Mirrors
    nas-intake's `_WEDGED_*` behavior."""
    src = Path(nas_path)
    if not src.exists():
        return
    dest = intake_dir / "_wedged" / src.name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
    except OSError as exc:
        logger.warning("move_to_wedged: could not move %s: %s", src, exc)


def preprocess_to_image(
    nas_path: Path, sha: str, tmp: Path, *, max_dim: int | None = None, dpi: int = 200
) -> Path:
    """Produce one preprocessed JPEG (under `tmp`) ready for a vision extractor.

    PDFs are rasterized + page-stacked first (HEIC opens directly once
    register_heif() has run at import); everything then passes through the same
    resize + autocontrast step. Returns the preprocessed image path. `max_dim`
    defaults to the env-tunable value (`MEAL_PLANNER_VISION_MAX_DIM`).
    """
    if max_dim is None:
        max_dim = _default_max_dim()
    src_suffix = nas_path.suffix.lower() or ".jpg"
    preprocess_src = nas_path
    if src_suffix in rasterize.PDF_SUFFIXES:
        preprocess_src = tmp / f"{sha}_pages.png"
        rasterize.pdf_to_stacked_image(nas_path, preprocess_src, dpi=dpi)

    preprocessed = tmp / f"{sha}.jpg"
    _process_one(
        src=preprocess_src,
        dst=preprocessed,
        max_dim=max_dim,
        autocontrast_cutoff=2,
        log_path=tmp / "preprocess.log",
    )
    return preprocessed


def persist_recipe(
    sha: str,
    result: ExtractResult,
    *,
    nas_path: Path,
    intake_dir: Path,
    extraction_path: str,
    ok_status: str = "ok",
    partial_status: str = "ok_partial",
) -> dict:
    """Persist a successful extraction: archive file, insert recipe, mark status.

    Caller guarantees `result.status == "ok"`. Renames the source file into
    `_done/` FIRST (Option B: `photo_path` always points at a real file; a rename
    failure raises so the caller's except records the error). Inserts the recipe,
    the `photo-intake` tag + model tags, and the ingredients in one transaction,
    writes the post-normalize JSON sidecar, then marks `photos_intake`
    `partial_status` (when there are warnings) or `ok_status`.

    `ok_status`/`partial_status` differ by path: local uses ok/ok_partial, the
    Gemini path uses gemini_ok for both (extraction_path='gemini' + a non-null
    extraction_warnings column already distinguish a partial Gemini result, and
    there is no `gemini_ok_partial` status).

    Returns the worker result dict.
    """
    src_suffix = nas_path.suffix.lower() or ".jpg"
    done_dir = intake_dir / "_done"
    done_path = done_dir / f"{sha}{src_suffix}"

    done_dir.mkdir(parents=True, exist_ok=True)
    nas_path.rename(done_path)

    db_path = _db.DB_PATH
    conn = _db._get_conn(db_path)
    try:
        title = (result.parsed.get("title") or "") or sha
        _raw_instr = result.parsed.get("instructions")
        instructions = _raw_instr.strip() or None if isinstance(_raw_instr, str) else None
        _raw_book = result.parsed.get("recipe_book")
        recipe_book = _raw_book.strip() or None if isinstance(_raw_book, str) else None
        recipe_id = insert_recipe(
            title=title,
            source="nas-intake",
            photo_path=str(done_path),
            instructions=instructions,
            recipe_book=recipe_book,
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
        sidecar_path = done_dir / f"{sha}.json"
        sidecar_path.write_text(json.dumps(result.parsed, indent=2, ensure_ascii=False))
    except Exception as _sidecar_exc:  # noqa: BLE001 — sidecar is best-effort
        logger.warning("persist_recipe: sidecar write failed sha=%s: %s", sha, _sidecar_exc)

    norm_warnings = result.normalize_warnings or []
    all_warnings = list(ing_warnings) + list(norm_warnings)
    if all_warnings:
        intake_db.mark_status(
            sha, partial_status,
            recipe_id=recipe_id, extraction_path=extraction_path,
            extraction_warnings=json.dumps(all_warnings),
        )
        status_for_return = partial_status
    else:
        intake_db.mark_status(sha, ok_status, recipe_id=recipe_id, extraction_path=extraction_path)
        status_for_return = ok_status

    logger.info(
        "persist_recipe: %s sha=%s recipe_id=%d ing_warns=%d norm_warns=%d path=%s",
        status_for_return, sha, recipe_id, len(ing_warnings), len(norm_warnings), extraction_path,
    )
    return {
        "sha": sha,
        "status": status_for_return,
        "recipe_id": recipe_id,
        "latency_s": result.latency_s,
        "warning_count": len(all_warnings),
        "normalize_warning_count": len(norm_warnings),
    }
