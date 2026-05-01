"""
All 12 migration kinds + the verifier + nop are registered, each migration
declares @baseline, and crontab declarations parse without error.
"""
from __future__ import annotations

import pytest

EXPECTED_MIGRATIONS = {
    "heartbeat",
    "daily_digest",
    "weekly_ssh_digest",
    "dispatcher_3day_check",
    "finance_monitor_watch",
    "nas_intake_scan",
    "health_collect",
    "health_intervals_poll",
    "health_staleness",
    "restic_hourly",
    "restic_daily",
    "restic_prune",
}


@pytest.fixture
def kinds():
    from jobs.cli import _registered_kinds
    return _registered_kinds()


def test_all_12_migrations_registered(kinds):
    missing = EXPECTED_MIGRATIONS - set(kinds)
    assert not missing, f"missing migrations: {missing}"


def test_nop_and_verifier_present(kinds):
    assert "nop" in kinds
    assert "migration_verifier" in kinds


def test_each_migration_has_baseline(kinds):
    """Every migrated Job must declare @baseline so the verifier can check it."""
    from jobs.lib import get_baseline
    for name in EXPECTED_MIGRATIONS:
        fn = kinds[name]
        bl = get_baseline(fn)
        assert bl is not None, f"{name} is missing @baseline"
        assert bl.metric, f"{name} baseline.metric is empty"
        assert bl.divergence_window, f"{name} baseline.divergence_window is empty"
        assert bl.divergence_seconds > 0


def test_each_migration_has_requires(kinds):
    """Each migration should declare @requires so failures surface early."""
    from jobs.lib import get_requires
    for name in EXPECTED_MIGRATIONS:
        fn = kinds[name]
        req = get_requires(fn)
        assert req is not None, f"{name} is missing @requires"
        assert req.items, f"{name} has empty requires list"


def test_baseline_metrics_use_supported_kinds(kinds):
    """Each baseline metric must dispatch to a known check in migration_verifier."""
    from jobs.lib import get_baseline
    supported_prefixes = (
        "incidents.jsonl-mtime",
        "file-mtime:",
        "db-mtime:",
        "restic-snapshot-count:",
        "no-op",
    )
    for name in EXPECTED_MIGRATIONS:
        fn = kinds[name]
        bl = get_baseline(fn)
        assert bl is not None, f"{name} missing baseline"
        metric = bl.metric
        assert any(metric == p or metric.startswith(p) for p in supported_prefixes), (
            f"{name}: baseline metric {metric!r} doesn't match any supported prefix "
            f"{supported_prefixes}"
        )
