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

from jobs.db import HUEY_DB_PATH, configure_sqlite

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

# The migrations.json runtime-state path. Created lazily by migration_verifier.
MIGRATIONS_STATE_PATH = Path.home() / "Home-Tools" / "run" / "migrations.json"

# Re-export so kinds can write `from jobs import huey, requires, baseline`.
from jobs.lib import baseline, requires  # noqa: E402

__all__ = ["huey", "requires", "baseline", "MIGRATIONS_STATE_PATH"]
