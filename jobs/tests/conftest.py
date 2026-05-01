"""
Pytest config — redirects HUEY_DB_PATH and MIGRATIONS_STATE_PATH to a tmpdir
so tests don't touch the real ~/Home-Tools/jobs/jobs.db.

Also flips huey.immediate=True so tasks execute synchronously in the test
process (no consumer needed).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the repo importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Use a per-test-session tmp before any jobs.* import.
import tempfile

_TMP = Path(tempfile.mkdtemp(prefix="jobs_tests_"))
os.environ["HOME"] = str(_TMP)
# Pin huey's SQLite file under tmp regardless of how Path.home() resolved at
# the time jobs/__init__.py imported.
os.environ["JOBS_DB_OVERRIDE"] = str(_TMP / "Home-Tools" / "jobs" / "jobs.db")
(_TMP / "Home-Tools" / "jobs").mkdir(parents=True, exist_ok=True)
(_TMP / "Home-Tools" / "run").mkdir(parents=True, exist_ok=True)
(_TMP / "Home-Tools" / "logs").mkdir(parents=True, exist_ok=True)

import pytest

from jobs import huey  # noqa: E402

# Synchronous mode: every enqueue executes inline.
huey.immediate = True


@pytest.fixture(autouse=True)
def _reset_huey_storage():
    """Each test starts with an empty huey storage."""
    yield
    try:
        huey.flush()
    except Exception:
        pass
