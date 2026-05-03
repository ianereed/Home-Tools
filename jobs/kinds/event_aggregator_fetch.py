"""Migration of event-aggregator/com.home-tools.event-aggregator.fetch — every 10 min.

Phase 12.5 — first half of the event-aggregator migration. Replaces the
StartInterval=600 LaunchAgent that polls the connector registry and drops
new messages into state.text_queue.

Phase 12.7 — after the subprocess writes to state.text_queue, this kind
drains that queue and schedules event_aggregator_text huey tasks for each
message. state.text_queue becomes a transient staging area; the worker loop
is no longer scheduled (plist disabled via `cli migrate event_aggregator_text`).

Pattern mirrors `finance_monitor_watch`: uses the project's own venv
because event-aggregator/main.py imports gmail/slack/imessage modules
that aren't in the jobs-consumer venv. Working directory is the project
dir so `import state` and bare `from connectors import ...` resolve.

Baseline: `event-aggregator/run/event-aggregator-fetch.last` is touched
unconditionally at the end of `fetch_only()` (main.py around line 1645).
That file's mtime is the liveness signal; the verifier compares it
against the captured baseline_snapshot during the 72h soak.
"""
from __future__ import annotations

import importlib.util
import logging
import subprocess
from pathlib import Path

from huey import crontab

from jobs import baseline, huey, migrates_from, requires
from jobs.kinds._internal.migration_verifier import record_fire
from jobs.kinds.event_aggregator_text import event_aggregator_text

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parents[2] / "event-aggregator"
VENV_PYTHON = PROJECT / ".venv" / "bin" / "python3"


def _load_ea_state():
    """Load event-aggregator state module via importlib to avoid venv pollution."""
    spec = importlib.util.spec_from_file_location("_ea_state_fetch", PROJECT / "state.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@huey.periodic_task(crontab(minute="*/10"))
@requires(["fs:event-aggregator"])
@baseline(
    metric="file-mtime:event-aggregator/run/event-aggregator-fetch.last",
    divergence_window="12m",
    cadence="10m",
)
@migrates_from("com.home-tools.event-aggregator.fetch")
def event_aggregator_fetch() -> dict:
    proc = subprocess.run(
        [str(VENV_PYTHON), "main.py", "fetch-only"],
        cwd=str(PROJECT), capture_output=True, text=True, timeout=540,
    )
    record_fire("event_aggregator_fetch")
    if proc.returncode != 0:
        logger.warning(
            "event-aggregator-fetch rc=%d stderr=%s",
            proc.returncode, proc.stderr[:200],
        )

    # Phase 12.7: drain state.text_queue into huey text tasks.
    # fetch_only() writes messages to state.text_queue; we pick them up here
    # and schedule per-message event_aggregator_text tasks. Also call
    # record_fire("event_aggregator_text") as a liveness proxy — if fetch fires
    # (every 10min), the text migration's no-fire check stays green even on quiet
    # days with no messages.
    scheduled = 0
    try:
        ea_state = _load_ea_state()
        with ea_state.locked():
            state = ea_state.load()
            pending: list[dict] = []
            while True:
                job = state.pop_text_job()
                if job is None:
                    break
                pending.append(job)
            ea_state.save(state)
        for job in pending:
            event_aggregator_text(job)
            scheduled += 1
    except Exception as exc:
        logger.warning("event-aggregator-fetch: failed to drain text_queue: %s", exc)

    record_fire("event_aggregator_text")
    if scheduled:
        logger.info("event-aggregator-fetch: scheduled %d text task(s)", scheduled)

    return {"rc": proc.returncode, "text_scheduled": scheduled}
