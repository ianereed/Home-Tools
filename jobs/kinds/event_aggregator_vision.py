"""Phase 12.7 — on-demand OCR/image analysis for one queued file.

Replaces the vision-processing arm of the event-aggregator worker loop.
Scheduled by `event_aggregator_decision_poller` for each file that lands
in state.ocr_queue (enqueue-image CLI).

The jobs consumer handles model loading via @requires_model("vision");
the subprocess handles OCR in the event-aggregator venv.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from jobs import huey, requires, requires_model
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parents[2] / "event-aggregator"
VENV_PYTHON = PROJECT / ".venv" / "bin" / "python3"
TOUCH_FILE = PROJECT / "run" / "event-aggregator-text-or-vision.last"


@huey.task()
@requires_model("vision")
@requires(["fs:event-aggregator"])
def event_aggregator_vision(job: dict) -> dict:
    """Run one OCR job via event-aggregator subprocess.

    @requires_model("vision") ensures the vision model is loaded before the
    subprocess runs. The subprocess calls cli.py run-ocr-job which runs
    _run_ocr_job → cli._cmd_ingest_image (full OCR pipeline).
    """
    file_path = job.get("file_path", "")
    proc = subprocess.run(
        [str(VENV_PYTHON), "cli.py", "run-ocr-job", "--file", file_path],
        cwd=str(PROJECT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    record_fire("event_aggregator_text")  # liveness signal for worker migration
    if proc.returncode != 0:
        logger.warning(
            "event-aggregator-vision rc=%d file=%s stderr=%s",
            proc.returncode,
            file_path,
            proc.stderr[:300],
        )
    else:
        TOUCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOUCH_FILE.touch()
    return {"rc": proc.returncode, "file_path": file_path}
