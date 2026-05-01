"""
nop — smoke-test Job. Returns its input. Used by:
  - jobs/cli.py doctor       (verifies the consumer is alive)
  - tests/test_huey_basic.py (verifies enqueue → consume round-trip)
"""
from __future__ import annotations

from datetime import datetime, timezone

from jobs import huey


@huey.task()
def nop(echo: dict | None = None) -> dict:
    return {
        "echo": echo or {},
        "ts": datetime.now(timezone.utc).isoformat(),
        "ok": True,
    }
