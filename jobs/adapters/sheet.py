"""
Sheet adapter — STRICT STUB for v1 (TC7).

Phase 13 (meal-planner expansion) implements real Google Sheets append. v1
exists only so Jobs can declare `target: "sheet"` in their `output_config`
without crashing the framework — call site raises NotImplementedError.

Why exposed at all? Migration parity tests need to round-trip the kind name
through `adapters.dispatch`. Failing fast with a clear message is better
than silent KeyError on lookup.
"""
from __future__ import annotations


def append_row(output_config: dict, payload: dict) -> dict:
    raise NotImplementedError(
        "sheet adapter is a strict stub in Phase 12. "
        "Real Google Sheets append lands in Phase 13 (meal-planner expansion). "
        "Until then, route to the card or nas adapter."
    )
