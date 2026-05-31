"""Pure-helper tests for console/tabs/decisions.py (no Streamlit runtime needed)."""
from __future__ import annotations

import pytest

from console.tabs import decisions


def test_fmt_dt_parses_iso():
    assert decisions._fmt_dt("2026-05-30T14:00:00+00:00") == "Sat May 30, 2PM"


def test_fmt_dt_handles_none_and_garbage():
    assert decisions._fmt_dt(None) == "?"
    assert decisions._fmt_dt("not-a-date") == "not-a-date"


@pytest.mark.parametrize("item,expected", [
    ({"kind": "todo", "due_date": "2026-06-01", "priority": "high"}, "todo · due 2026-06-01 · high"),
    ({"kind": "todo"}, "todo"),
    ({"kind": "fuzzy_event"}, "event · no specific date"),
    ({"kind": "merge", "matched_title": "Dentist"}, "merge → Dentist"),
    ({"kind": "event", "start_dt": "2026-05-30T14:00:00+00:00", "source": "gmail"}, "Sat May 30, 2PM · gmail"),
])
def test_pending_subtitle(item, expected):
    assert decisions._pending_subtitle(item) == expected
