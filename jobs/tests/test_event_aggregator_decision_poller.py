"""Phase 12.7 — event_aggregator_decision_poller kind sanity checks."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone

from jobs.kinds import event_aggregator_decision_poller as mod


def _make_fake_ea_state(ocr_queue=None, swap_decisions=None):
    """Return a fake ea_state module with controllable state.json contents."""

    class _FakeState:
        def __init__(self):
            self._data = {
                "ocr_queue": list(ocr_queue or []),
                "swap_decisions": dict(swap_decisions or {}),
            }
            self._popped = []

        def pop_ocr_job(self):
            q = self._data.get("ocr_queue", [])
            return q.pop(0) if q else None

        def ocr_queue_depth(self):
            return len(self._data.get("ocr_queue", []))

        def add_swap_decision(self, ocr_path, text_depth):
            import secrets
            did = secrets.token_hex(4)
            self._data.setdefault("swap_decisions", {})[did] = {
                "ocr_path": ocr_path,
                "text_queue_depth_at_request": text_depth,
                "decision": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            return did

    class _FakeEaState:
        _state = _FakeState()

        @classmethod
        def reset(cls, ocr_queue=None, swap_decisions=None):
            cls._state = _FakeState()
            cls._state._data["ocr_queue"] = list(ocr_queue or [])
            cls._state._data["swap_decisions"] = dict(swap_decisions or {})

        @staticmethod
        def locked():
            return contextlib.nullcontext()

        @classmethod
        def load(cls):
            return cls._state

        @classmethod
        def save(cls, _state):
            pass

    _FakeEaState.reset(ocr_queue=ocr_queue, swap_decisions=swap_decisions)
    return _FakeEaState


def test_empty_ocr_queue_schedules_nothing(monkeypatch):
    fake = _make_fake_ea_state(ocr_queue=[])
    monkeypatch.setattr(mod, "_load_ea_state", lambda: fake)
    scheduled = []
    monkeypatch.setattr(mod, "event_aggregator_vision", lambda job: scheduled.append(job))
    monkeypatch.setattr(mod, "_pending_task_count_by_name", lambda _name: 0)

    result = mod.event_aggregator_decision_poller.func()
    assert result["scheduled_vision"] == 0
    assert scheduled == []


def test_ocr_queue_items_become_vision_tasks(monkeypatch):
    jobs = [
        {"file_path": "/tmp/a.png"},
        {"file_path": "/tmp/b.pdf"},
    ]
    fake = _make_fake_ea_state(ocr_queue=jobs)
    monkeypatch.setattr(mod, "_load_ea_state", lambda: fake)

    scheduled = []
    monkeypatch.setattr(mod, "event_aggregator_vision", lambda job: scheduled.append(job))
    monkeypatch.setattr(mod, "_pending_task_count_by_name", lambda _name: 0)

    result = mod.event_aggregator_decision_poller.func()
    assert result["scheduled_vision"] == 2
    assert len(scheduled) == 2
    assert scheduled[0]["file_path"] == "/tmp/a.png"
    assert scheduled[1]["file_path"] == "/tmp/b.pdf"


def test_stale_swap_decisions_auto_resolved(monkeypatch):
    """Pending decisions older than timeout must be auto-resolved to 'wait'."""
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    fake = _make_fake_ea_state(
        swap_decisions={"d1": {"decision": "pending", "created_at": old_ts}}
    )
    monkeypatch.setattr(mod, "_load_ea_state", lambda: fake)
    monkeypatch.setattr(mod, "event_aggregator_vision", lambda job: None)
    monkeypatch.setattr(mod, "_pending_task_count_by_name", lambda _name: 0)

    mod.event_aggregator_decision_poller.func()
    assert fake._state._data["swap_decisions"]["d1"]["decision"] == "wait"
    assert fake._state._data["swap_decisions"]["d1"].get("auto_resolved") is True


def test_interrupt_decision_consumed(monkeypatch):
    fake = _make_fake_ea_state(
        swap_decisions={"d2": {
            "decision": "interrupt",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    monkeypatch.setattr(mod, "_load_ea_state", lambda: fake)
    monkeypatch.setattr(mod, "event_aggregator_vision", lambda job: None)
    monkeypatch.setattr(mod, "_pending_task_count_by_name", lambda _name: 0)

    result = mod.event_aggregator_decision_poller.func()
    assert result["interrupt_consumed"] is True
    assert fake._state._data["swap_decisions"]["d2"]["decision"] == "consumed"
