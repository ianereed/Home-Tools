"""
Mini Jobs framework — Phase 12.

Single typed-job framework based on huey (SqliteHuey backend). Replaces the
mini's per-task LaunchAgent pattern: cron-style agents become @huey.periodic_task
decorated functions in jobs/kinds/*.py; one-shot enqueues come over HTTP at
:8504 or via `jobs/cli.py enqueue`.

The huey instance defined here is imported by every Job kind. The consumer
plist (jobs/config/com.home-tools.jobs-consumer.plist) launches `huey_consumer`
pointing at this module.

Package layout:
  jobs/
    __init__.py          ← huey instance (this file)
    db.py                ← SQLite WAL/busy-timeout config
    lib.py               ← @requires, @baseline decorators
    adapters/            ← gcal/todoist/slack/card/nas/sheet adapters
    kinds/               ← Job kind definitions (one file per kind)
      _internal/
        migration_verifier.py
      heartbeat.py
      ...
    cli.py               ← enqueue/status/kinds/new/doctor/migrate/rollback
    enqueue_http.py      ← stdlib http.server on :8504
    install.sh           ← installer for consumer + http
    config/              ← LaunchAgent plists
"""
from __future__ import annotations

import os
from pathlib import Path

from huey import SqliteHuey

from jobs.db import HUEY_DB_PATH, JOBS_DIR, configure_sqlite

# The huey storage file is created lazily on first connection. Tests can
# override HUEY_DB_PATH by setting $HOME (so Path.home() resolves to a tmp)
# before importing jobs.

# Read JOBS_DB_OVERRIDE if a test set it; otherwise compute from $HOME at
# import time. The override path lets pytest pin the location even after
# jobs.db was imported via collection.
_db_path = Path(os.environ.get("JOBS_DB_OVERRIDE", str(HUEY_DB_PATH)))
_db_path.parent.mkdir(parents=True, exist_ok=True)

huey = SqliteHuey(
    name="home-tools-jobs",
    filename=str(_db_path),
    immediate=False,  # production: real consumer; tests flip to immediate=True
    fsync=True,       # durability over throughput on a hobby workload
)

# Apply WAL + busy_timeout the moment the SQLite file is created.
configure_sqlite(_db_path)

# Phase 22 — second lane for user-initiated kinds. Same SqliteHuey shape,
# separate DB file so a slow batch job on `huey` never blocks a click on
# `huey_fast`. Kinds opt in with `from jobs import huey_fast as huey` at
# the top of their module.
_fast_db_path = Path(
    os.environ.get("JOBS_FAST_DB_OVERRIDE", str(JOBS_DIR / "jobs-fast.db"))
)
_fast_db_path.parent.mkdir(parents=True, exist_ok=True)

huey_fast = SqliteHuey(
    name="home-tools-jobs-fast",
    filename=str(_fast_db_path),
    immediate=False,
    fsync=True,
)
configure_sqlite(_fast_db_path)

# The migrations.json runtime-state path. Created lazily by migration_verifier.
MIGRATIONS_STATE_PATH = Path.home() / "Home-Tools" / "run" / "migrations.json"

# Re-export so kinds can write `from jobs import huey, requires, baseline, requires_model`.
from jobs.lib import baseline, migrates_from, record_swap, requires, requires_model  # noqa: E402

__all__ = ["huey", "huey_fast", "requires", "baseline", "migrates_from", "requires_model", "record_swap", "MIGRATIONS_STATE_PATH"]


def _load_all_kinds() -> None:
    """Import every Job kind so its `@huey.task` / `@huey.periodic_task`
    decorator fires and the task lands in huey's registry. Without this,
    huey_consumer.py jobs.huey starts with an empty registry — the
    consumer would log "X not found in TaskRegistry" on every dequeue.

    Auto-discover by walking jobs/kinds/ + jobs/kinds/_internal/ once at
    import time. Failures are logged but non-fatal so a single broken kind
    doesn't take down the whole consumer.
    """
    import importlib
    import logging
    import pkgutil
    from pathlib import Path

    logger = logging.getLogger(__name__)
    import jobs.kinds as _kinds_pkg
    for finder, name, ispkg in pkgutil.iter_modules(
        _kinds_pkg.__path__, prefix="jobs.kinds."
    ):
        try:
            importlib.import_module(name)
        except Exception as exc:  # one bad kind shouldn't kill the consumer
            logger.warning("failed to load kind %s: %s", name, exc)
    internal_root = Path(_kinds_pkg.__path__[0]) / "_internal"
    if internal_root.exists():
        for f in internal_root.glob("*.py"):
            if f.name.startswith("_"):
                continue
            modname = f"jobs.kinds._internal.{f.stem}"
            try:
                importlib.import_module(modname)
            except Exception as exc:
                logger.warning("failed to load internal kind %s: %s", modname, exc)


def _init_meal_planner_schema() -> None:
    try:
        from meal_planner.db import init_db
        init_db()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "meal_planner.init_db() failed at consumer boot: %s", exc
        )


_init_meal_planner_schema()
_load_all_kinds()
