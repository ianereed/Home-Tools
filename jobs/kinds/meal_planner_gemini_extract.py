"""Gemini escalation for recipe photos the local model couldn't extract.

When `meal_planner_photo_intake_scan` finds a row that has exhausted its local
llama3.2-vision retries, it consumes one unit of the daily Gemini budget, marks
the row `gemini_pending`, and enqueues this task. Gemini 2.5 Flash is materially
better on hard photos than any local model (see meal_planner/eval/PHASE15_NOTES),
so this is the rescue path.

The daily cap (GEMINI_DAILY_CAP) is enforced at enqueue time by the scan via
`intake_db.gemini_try_consume`, leaving free-tier headroom for other Gemini work.
This task does NOT consume budget itself — it just runs the call the scan already
paid for.

On success: persists the recipe + moves the file to _done/ + marks `gemini_ok`.
On failure (or missing API key): marks `wedged` and moves the file to _wedged/ so
it stops sitting in the processing bucket. No re-escalation — a wedged row is
terminal.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from jobs import huey, requires
from meal_planner.vision import intake_db
from meal_planner.vision.extract import extract_recipe_from_gemini
from meal_planner.vision.ingest_common import move_to_wedged, persist_recipe, preprocess_to_image

logger = logging.getLogger(__name__)

_DEFAULT_INTAKE_DIR = "/Users/homeserver/Share1/Documents/Recipes/photo-intake"

# Max Gemini escalations per local calendar day. Keeps free-tier headroom for the
# user's other Gemini tasks. The scan enforces this via intake_db.gemini_try_consume.
GEMINI_DAILY_CAP = 5


@huey.task()
@requires(["fs:meal_planner"])
def meal_planner_gemini_extract(sha: str) -> dict:
    row = intake_db.get_by_sha(sha)
    if row is None or row.status != "gemini_pending":
        logger.info(
            "meal_planner_gemini_extract: sha=%s status=%s — skip",
            sha, row.status if row else "missing",
        )
        return {"sha": sha, "status": "skipped_already_handled", "recipe_id": None, "latency_s": None}

    intake_dir = Path(os.environ.get("MEAL_PLANNER_NAS_INTAKE_DIR", _DEFAULT_INTAKE_DIR))
    nas_path = Path(row.nas_path)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        intake_db.mark_status(sha, "wedged", error="gemini escalation: GEMINI_API_KEY not set")
        move_to_wedged(nas_path, intake_dir)
        logger.warning("meal_planner_gemini_extract: sha=%s no GEMINI_API_KEY — wedged", sha)
        return {"sha": sha, "status": "wedged", "recipe_id": None, "latency_s": None}

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            preprocessed = preprocess_to_image(nas_path, sha, Path(tmpdir))
            result = extract_recipe_from_gemini(preprocessed, api_key=api_key, timeout_s=60)

        if result.status == "ok":
            return persist_recipe(
                sha, result,
                nas_path=nas_path,
                intake_dir=intake_dir,
                extraction_path="gemini",
                ok_status="gemini_ok",
                partial_status="gemini_ok",
            )

        # Gemini couldn't extract it either — terminal. Wedge + move so it leaves
        # the processing bucket; do NOT re-escalate (budget already spent).
        intake_db.mark_status(
            sha, "wedged",
            error=f"gemini {result.status}: {(result.error or '')[:200]}",
        )
        move_to_wedged(nas_path, intake_dir)
        logger.warning(
            "meal_planner_gemini_extract: gemini %s sha=%s — wedged",
            result.status, sha,
        )
        return {"sha": sha, "status": "wedged", "recipe_id": None, "latency_s": result.latency_s}

    except Exception as exc:
        intake_db.mark_status(sha, "wedged", error=f"gemini escalation crash: {exc!r}"[:500])
        move_to_wedged(nas_path, intake_dir)
        raise
