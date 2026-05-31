"""Phase 22 contention probe — DO NOT MERGE TO MAIN.

This kind is added on feat/phase22-two-lane-huey, used once on the mini
to occupy the slow lane for the contention test, then deleted in the
same branch before /ship. If you see this on main, something went wrong.

Bound to the default (slow) huey on purpose: occupies the same lane the
periodic background kinds use so the contention test mirrors the real
wedge scenario.
"""
from __future__ import annotations

import time

from jobs import huey


@huey.task()
def _debug_sleep(seconds: int = 120) -> dict:
    time.sleep(seconds)
    return {"slept": seconds}
