"""Phase 12.7 — on-demand text extraction for one queued message.

Replaces the text-processing arm of the event-aggregator worker loop.
Scheduled by `event_aggregator_fetch` for each message that fetch_only()
enqueues. The jobs consumer handles model loading; the subprocess handles
the actual extraction in the event-aggregator venv.

Migration: @migrates_from("com.home-tools.event-aggregator.worker")
Baseline: event-aggregator/run/event-aggregator-text-or-vision.last
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from jobs import baseline, huey, migrates_from, requires, requires_model
from jobs.kinds._internal.migration_verifier import record_fire

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parents[2] / "event-aggregator"
VENV_PYTHON = PROJECT / ".venv" / "bin" / "python3"
TOUCH_FILE = PROJECT / "run" / "event-aggregator-text-or-vision.last"


@huey.task()
@requires_model("text", batch_hint="drain")
@requires(["fs:event-aggregator"])
@baseline(
    metric="file-mtime:event-aggregator/run/event-aggregator-text-or-vision.last",
    divergence_window="2h",
    cadence="2h",
)
@migrates_from("com.home-tools.event-aggregator.worker")
def event_aggregator_text(job: dict) -> dict:
    """Run one text-extraction job via event-aggregator subprocess.

    @requires_model("text", batch_hint="drain") ensures the text model is
    loaded before the subprocess runs. The subprocess makes Ollama API calls
    using the already-loaded model — no duplicate warmup.
    """
    proc = subprocess.run(
        [str(VENV_PYTHON), "cli.py", "run-text-job", "--job-json", json.dumps(job)],
        cwd=str(PROJECT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    record_fire("event_aggregator_text")
    if proc.returncode != 0:
        logger.warning(
            "event-aggregator-text rc=%d source=%s id=%s stderr=%s",
            proc.returncode,
            job.get("source", "?"),
            job.get("id", "?"),
            proc.stderr[:300],
        )
    else:
        TOUCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOUCH_FILE.touch()
    return {"rc": proc.returncode, "source": job.get("source"), "id": job.get("id")}
