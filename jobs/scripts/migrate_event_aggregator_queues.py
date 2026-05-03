"""One-time migration: drain event-aggregator state.json queues into huey tasks.

Run this before or immediately after `jobs.cli migrate event_aggregator_text`
to ensure any jobs that were queued in the old worker loop are picked up by
the huey consumer instead of being silently dropped.

Idempotent: safe to re-run if the consumer was stopped mid-cutover.

Usage (on mini):
    cd ~/Home-Tools
    jobs/.venv/bin/python jobs/scripts/migrate_event_aggregator_queues.py [--dry-run]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# Ensure jobs package is importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.kinds.event_aggregator_text import event_aggregator_text
from jobs.kinds.event_aggregator_vision import event_aggregator_vision

PROJECT = Path(__file__).resolve().parents[2] / "event-aggregator"


def _load_ea_state():
    spec = importlib.util.spec_from_file_location("_ea_state_migrate", PROJECT / "state.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(dry_run: bool = False) -> None:
    ea_state = _load_ea_state()

    with ea_state.locked():
        state = ea_state.load()
        text_jobs: list[dict] = []
        ocr_jobs: list[dict] = []

        while True:
            job = state.pop_text_job()
            if job is None:
                break
            text_jobs.append(job)

        while True:
            job = state.pop_ocr_job()
            if job is None:
                break
            ocr_jobs.append(job)

        if dry_run:
            print(f"[dry-run] would schedule {len(text_jobs)} text task(s), {len(ocr_jobs)} vision task(s)")
            for j in text_jobs:
                print(f"  text: source={j.get('source')} id={j.get('id')}")
            for j in ocr_jobs:
                print(f"  vision: file={j.get('file_path')}")
            return

        # Clear queues atomically, then schedule.
        ea_state.save(state)

    for job in text_jobs:
        event_aggregator_text(job)
        print(f"scheduled text task: source={job.get('source')} id={job.get('id')}")

    for job in ocr_jobs:
        event_aggregator_vision(job)
        print(f"scheduled vision task: file={job.get('file_path')}")

    print(f"done: scheduled {len(text_jobs)} text + {len(ocr_jobs)} vision tasks")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Print what would be scheduled without doing it")
    args = p.parse_args()
    run(dry_run=args.dry_run)
