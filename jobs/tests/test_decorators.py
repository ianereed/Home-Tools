"""
@requires + @baseline decorators + RequirementsNotMet error message quality.

The error message is tested in addition to the type because it's the
operator's only signal when a Job fails pre-flight on the mini.
"""
from __future__ import annotations

import os

import pytest

from jobs import baseline, requires
from jobs.lib import Baseline, RequirementsNotMet, _parse_duration


def test_baseline_attaches_metadata():
    @baseline(metric="incidents.jsonl-mtime", divergence_window="35m")
    def f():
        return 1

    bl: Baseline = f._baseline
    assert bl.metric == "incidents.jsonl-mtime"
    assert bl.divergence_window == "35m"
    assert bl.divergence_seconds == 35 * 60


def test_requires_passes_when_env_set(monkeypatch):
    monkeypatch.setenv("MY_TEST_SECRET", "abc")

    @requires(["secret:MY_TEST_SECRET"])
    def f():
        return "ran"

    assert f() == "ran"


def test_requires_blocks_when_env_missing(monkeypatch):
    monkeypatch.delenv("DEFINITELY_UNSET_TEST_SECRET", raising=False)

    @requires(["secret:DEFINITELY_UNSET_TEST_SECRET"])
    def f():
        pytest.fail("body should not execute when requirements fail")

    with pytest.raises(RequirementsNotMet) as exc:
        f()
    msg = str(exc.value)
    # Operator-readable error contract: must mention job name + the missing dep
    assert "f" in msg
    assert "DEFINITELY_UNSET_TEST_SECRET" in msg
    assert "secret" in msg


def test_requires_unknown_kind_message():
    @requires(["frobnicate:zzz"])
    def f():
        pass

    with pytest.raises(RequirementsNotMet) as exc:
        f()
    assert "unknown requires kind" in str(exc.value)
    assert "supported" in str(exc.value).lower()


def test_requires_malformed_entry():
    @requires(["no_colon_here"])
    def f():
        pass

    with pytest.raises(RequirementsNotMet) as exc:
        f()
    assert "malformed" in str(exc.value)


def test_parse_duration():
    assert _parse_duration("30s") == 30
    assert _parse_duration("5m") == 300
    assert _parse_duration("2h") == 7200
    assert _parse_duration("8d") == 8 * 86400


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError, match="unparseable"):
        _parse_duration("forever")


def test_requires_missing_db_message(tmp_path, monkeypatch):
    # db: kind looks under ~/Home-Tools/<rel>; conftest pinned $HOME to a tmp
    @requires(["db:does/not/exist.db"])
    def f():
        pass

    with pytest.raises(RequirementsNotMet) as exc:
        f()
    msg = str(exc.value)
    assert "does/not/exist.db" in msg
    assert "missing" in msg
