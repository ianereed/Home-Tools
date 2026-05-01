"""
state.save() must run inside a state.locked() block.

OPS6 closure (Phase 12 Commit 1). Without this invariant, a worker tick can
overwrite a CLI approval that lands between load() and save(), losing the
user's decision. The flock ensures cross-process serialisation; this test
asserts save() refuses to run when no flock is held.
"""
from __future__ import annotations

import pathlib

import pytest

import state as state_module


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """Redirect STATE_PATH and _LOCK_PATH to a tmpdir for the test."""
    monkeypatch.setattr(state_module, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(state_module, "_LOCK_PATH", tmp_path / ".state.lock")
    yield tmp_path


def test_save_raises_without_lock(tmp_state):
    s = state_module.State({})
    with pytest.raises(RuntimeError, match="without an active locked"):
        state_module.save(s)


def test_save_succeeds_inside_locked(tmp_state):
    s = state_module.State({})
    with state_module.locked():
        state_module.save(s)
    assert (tmp_state / "state.json").exists()


def test_locked_is_reentrant(tmp_state):
    s = state_module.State({})
    with state_module.locked():
        with state_module.locked():
            state_module.save(s)
        # Still inside outer lock: save should still work.
        state_module.save(s)
    # Outside everything: save must fail again.
    with pytest.raises(RuntimeError):
        state_module.save(s)


def test_lock_depth_resets_on_exception(tmp_state):
    s = state_module.State({})
    try:
        with state_module.locked():
            raise ValueError("boom")
    except ValueError:
        pass
    assert state_module._lock_depth == 0
    with pytest.raises(RuntimeError):
        state_module.save(s)
