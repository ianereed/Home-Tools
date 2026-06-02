"""Phase 16 Chunk 2 — Discover new recipe photos in the NAS drop zone.

Every 5 minutes:
1. Walks MEAL_PLANNER_NAS_INTAKE_DIR for .jpg/.jpeg/.png files.
2. SHA-256-dedup: skips any file whose content hash is already in photos_intake.
3. Renames accepted files to _processing/<sha>.jpg (atomic on same FS).
4. Inserts a pending row in photos_intake.
5. Enqueues meal_planner_ingest_photo(sha) for extraction.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from huey import crontab

from jobs import huey, requires
from jobs.kinds.meal_planner_ingest_photo import meal_planner_ingest_photo
from meal_planner.vision import intake_db

logger = logging.getLogger(__name__)

_DEFAULT_INTAKE_DIR = "/Users/homeserver/Share1/Documents/Recipes/photo-intake"
# Photos, HEIC (iPhone), and PDF (recipe prints). The ingest task converts
# HEIC/PDF into an image before extraction; see meal_planner/vision/rasterize.py.
_SUPPORTED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".pdf"})

# llama3.2-vision occasionally emits malformed JSON for an image that parses
# fine on a re-run (non-deterministic; see intake_db.RETRYABLE_STATUSES). Each
# scan tick re-arms failed rows up to this many extra attempts, then wedges them
# so they stop cycling. Ticks are 5 min apart, so this is the retry spacing too.
_MAX_RETRIES = 2  # → up to 3 total extraction attempts
_SUBFOLDERS = ("_processing", "_done", "_skipped", "_wedged")


def _sha256_hex16(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _move_to_wedged(nas_path: str, intake_dir: Path) -> None:
    """Move a wedged file out of _processing/ into _wedged/ so it stops showing
    as "in processing." Best-effort: a missing file or rename error is logged and
    swallowed (the DB row is already the source of truth, marked wedged). Mirrors
    nas-intake's `_WEDGED_*` behavior so a wedged recipe doesn't sit in the
    processing bucket forever.
    """
    src = Path(nas_path)
    if not src.exists():
        return
    dest = intake_dir / "_wedged" / src.name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
    except OSError as exc:
        logger.warning("meal_planner_photo_intake_scan: could not move wedged file %s: %s", src, exc)


@huey.periodic_task(crontab(minute="*/5"))
@requires(["fs:meal_planner"])
def meal_planner_photo_intake_scan() -> dict:
    intake_dir = Path(os.environ.get("MEAL_PLANNER_NAS_INTAKE_DIR", _DEFAULT_INTAKE_DIR))

    try:
        files = list(intake_dir.iterdir())
    except OSError as exc:
        logger.warning("meal_planner_photo_intake_scan: drop zone unreachable: %s", exc)
        return {
            "discovered": 0,
            "enqueued": 0,
            "skipped_dup": 0,
            "tick_at": datetime.now(timezone.utc).isoformat(),
        }

    for sub in _SUBFOLDERS:
        (intake_dir / sub).mkdir(parents=True, exist_ok=True)

    # Self-heal: re-enqueue pending rows whose file still exists in _processing/.
    # Covers consumer crashes and enqueue failures from prior ticks.
    # Rows whose file is missing are handled by Chunk 4 wedge logic.
    re_enqueued = 0
    for row in intake_db.list_pending():
        if Path(row.nas_path).exists():
            try:
                meal_planner_ingest_photo(row.sha)
                re_enqueued += 1
            except Exception as exc:
                logger.warning("self-heal enqueue failed sha=%s: %s", row.sha, exc)

    # Retry sweep: re-arm recoverable failures (non-deterministic bad JSON,
    # transient ollama/transport errors). Wedge first so rows already at the cap
    # don't get bumped again; then retry the rest. The two sets are disjoint
    # (n_retries >= _MAX_RETRIES vs < _MAX_RETRIES), so no row is touched twice.
    retried = 0
    wedged = 0
    for row in intake_db.list_exhausted(_MAX_RETRIES):
        intake_db.mark_status(
            row.sha, "wedged",
            error=f"max retries ({_MAX_RETRIES}) exhausted; last: {(row.error or '')[:200]}",
        )
        _move_to_wedged(row.nas_path, intake_dir)
        wedged += 1
        logger.warning("meal_planner_photo_intake_scan: wedged sha=%s after %d retries", row.sha, row.n_retries)
    for row in intake_db.list_retryable(_MAX_RETRIES):
        if not Path(row.nas_path).exists():
            # File gone from _processing/ — a retry can never succeed; wedge it.
            intake_db.mark_status(row.sha, "wedged", error="retry: source file missing from _processing")
            wedged += 1
            continue
        intake_db.bump_retry(row.sha)  # → pending, n_retries + 1, error cleared
        try:
            meal_planner_ingest_photo(row.sha)
            retried += 1
            logger.info("meal_planner_photo_intake_scan: retry sha=%s (attempt %d)", row.sha, row.n_retries + 2)
        except Exception as exc:
            logger.warning("meal_planner_photo_intake_scan: retry enqueue failed sha=%s: %s", row.sha, exc)

    discovered = 0
    enqueued = 0
    skipped_dup = 0

    for f in files:
        if not f.is_file():
            continue
        if f.suffix.lower() not in _SUPPORTED_SUFFIXES:
            continue

        discovered += 1
        sha = _sha256_hex16(f)
        # Preserve the original extension so the ingest task knows whether to
        # rasterize (.pdf) or open directly (.jpg/.png/.heic).
        target = intake_dir / "_processing" / f"{sha}{f.suffix.lower()}"

        existing = intake_db.get_by_sha(sha)
        if existing is not None:
            logger.info(
                "meal_planner_photo_intake_scan: dup sha=%s file=%s status=%s",
                sha, f.name, existing.status,
            )
            skipped_dup += 1
            continue

        # Claim first (DB row is canonical), then move file.
        if not intake_db.record_intake(sha, source_path=f.name, nas_path=str(target)):
            # Race: another tick claimed this sha. Treat as dup.
            skipped_dup += 1
            continue

        try:
            f.rename(target)
        except OSError as exc:
            logger.warning(
                "meal_planner_photo_intake_scan: rename failed sha=%s: %s — rolling back row",
                sha, exc,
            )
            intake_db._delete_by_sha(sha)
            continue

        try:
            meal_planner_ingest_photo(sha)
            enqueued += 1
            logger.info("meal_planner_photo_intake_scan: enqueued sha=%s", sha)
        except Exception as exc:
            logger.warning(
                "meal_planner_photo_intake_scan: enqueue failed sha=%s: %s",
                sha, exc,
            )
            intake_db.mark_status(sha, "ollama_error", error=f"enqueue failed: {exc!r}"[:500])

    return {
        "discovered": discovered,
        "enqueued": enqueued,
        "skipped_dup": skipped_dup,
        "re_enqueued": re_enqueued,
        "retried": retried,
        "wedged": wedged,
        "tick_at": datetime.now(timezone.utc).isoformat(),
    }
