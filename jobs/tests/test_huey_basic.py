"""
huey is wired up correctly: enqueue → result → no exceptions.

In immediate mode (set in conftest) the consumer is skipped, but the rest
of the pipeline (storage, serialization, return-value handling) runs the
same way as production.
"""
from __future__ import annotations


def test_nop_round_trip():
    from jobs.kinds.nop import nop
    result = nop({"hello": "world"})
    out = result(blocking=True, timeout=2)
    assert out["ok"] is True
    assert out["echo"] == {"hello": "world"}
    assert "ts" in out


def test_huey_storage_writes_to_tmp(tmp_path):
    from jobs.db import HUEY_DB_PATH
    # tmp HOME redirect in conftest puts HUEY_DB_PATH under tmp
    assert "Home-Tools/jobs" in str(HUEY_DB_PATH)
    assert HUEY_DB_PATH.parent.exists()


def test_immediate_mode_active():
    from jobs import huey
    assert huey.immediate is True
