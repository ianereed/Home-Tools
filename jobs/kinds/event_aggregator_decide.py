"""Console-driven event-aggregator mutations: approve / reject / undo.

The console (Streamlit at :8503) must never import huey in-process (it would hold
an orphan WAL fd and silently drop enqueues). Instead the Decisions tab enqueues
this kind over HTTP (console/jobs_client.py → :8504), and this task shells out to
event-aggregator's own CLI inside its own venv — the same subprocess pattern as
event_aggregator_fetch.py. All state mutation + GCal writes happen inside the CLI,
which takes the state flock internally, so this kind never touches state.json.

Params (splatted as kwargs by enqueue_http, `fn(**params)`):
  approve        list[int] | "all" | comma-string → cli.py decide --approve
  reject         list[int] | "all" | comma-string → cli.py decide --reject
  undo_gcal_id   str                               → cli.py undo --gcal-id

`decide` exit codes: 0 = all matched, 1 = none matched, 2 = partial.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from jobs import huey, requires

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parents[2] / "event-aggregator"
VENV_PYTHON = PROJECT / ".venv" / "bin" / "python3"
_TIMEOUT = 120


def _norm(v) -> str:
    """Coerce a param to a CLI-safe token: 'all', a comma-joined int list, or ''."""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (list, tuple)):
        return ",".join(str(int(x)) for x in v)
    if isinstance(v, int):
        return str(v)
    return ""


def _run(argv: list[str]) -> dict:
    try:
        proc = subprocess.run(
            [str(VENV_PYTHON), *argv],
            cwd=str(PROJECT), capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run already kills + reaps the child before raising.
        logger.warning("event_aggregator_decide: %s timed out after %ds", argv, _TIMEOUT)
        return {"rc": -1, "error": f"timeout after {_TIMEOUT}s", "summary": ""}
    if proc.returncode not in (0, 2):
        logger.warning("event_aggregator_decide %s rc=%d stderr=%s", argv, proc.returncode, proc.stderr[:200])
    return {
        "rc": proc.returncode,
        "summary": (proc.stdout or "").strip()[:500],
        "stderr_tail": (proc.stderr or "").splitlines()[-3:],
    }


@huey.task()
@requires(["fs:event-aggregator"])
def event_aggregator_decide(approve="", reject="", undo_gcal_id="") -> dict:
    """Apply a console decision. Undo takes precedence if a gcal_id is given;
    otherwise apply the approve/reject batch in one transaction."""
    undo_id = (undo_gcal_id or "").strip() if isinstance(undo_gcal_id, str) else str(undo_gcal_id)
    if undo_id:
        return _run(["cli.py", "undo", "--gcal-id", undo_id])

    a, r = _norm(approve), _norm(reject)
    if not a and not r:
        return {"rc": 1, "error": "nothing to decide", "summary": ""}
    argv = ["cli.py", "decide"]
    if a:
        argv += ["--approve", a]
    if r:
        argv += ["--reject", r]
    return _run(argv)
