"""
jobs CLI — `python3 -m jobs.cli <subcommand>`.

Subcommands:
  enqueue <kind> [--params JSON]   Enqueue a one-shot run of <kind>.
  status                           Print queue depth + pending + recent results.
  kinds                            List all registered Job kinds.
  new <name>                       Scaffold a new jobs/kinds/<name>.py.
  doctor                           Smoke-test: enqueue nop, wait for consumer, report.
  migrate <kind>                   Begin a migration: rename old plist → .disabled,
                                   record baseline in migrations.json.
  rollback <kind>                  Manual rollback of an in-flight migration.
  halt-verifier <kind>             Pause migration_verifier checks for one kind.
  cleanup-soaked                   Remove .disabled plists for promoted migrations.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import pkgutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _ensure_repo_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


_ensure_repo_on_path()


def _registered_kinds() -> dict[str, Any]:
    """Discover every @huey.task / @huey.periodic_task in jobs.kinds.*."""
    from jobs import huey  # noqa: F401  — ensure the singleton is constructed
    import jobs.kinds as kinds_pkg

    kinds: dict[str, Any] = {}
    # Walk jobs/kinds/ + jobs/kinds/_internal/ — single-level only.
    for finder, name, ispkg in pkgutil.iter_modules(kinds_pkg.__path__, prefix="jobs.kinds."):
        if name.endswith(".__init__"):
            continue
        try:
            mod = importlib.import_module(name)
        except Exception as exc:
            print(f"warn: failed to import {name}: {exc}", file=sys.stderr)
            continue
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if hasattr(attr, "task_class") and hasattr(attr, "name"):
                # huey-decorated function. attr.name is "module.fn_name"
                short = attr_name
                kinds[short] = attr
    # Internal kinds (verifier).
    internal_root = Path(kinds_pkg.__path__[0]) / "_internal"
    for f in internal_root.glob("*.py"):
        if f.name.startswith("_"):
            continue
        modname = f"jobs.kinds._internal.{f.stem}"
        try:
            mod = importlib.import_module(modname)
        except Exception as exc:
            print(f"warn: failed to import {modname}: {exc}", file=sys.stderr)
            continue
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if hasattr(attr, "task_class") and hasattr(attr, "name"):
                kinds[attr_name] = attr
    return kinds


def _print_kinds() -> int:
    from jobs.lib import get_baseline, get_requires
    kinds = _registered_kinds()
    if not kinds:
        print("no kinds registered.", file=sys.stderr)
        return 1
    print(f"{len(kinds)} job kind(s):")
    for name in sorted(kinds):
        fn = kinds[name]
        bl = get_baseline(fn)
        bl_summary = f" baseline={bl.metric!r} window={bl.divergence_window}" if bl else ""
        req = get_requires(fn)
        req_summary = f" requires={req.items}" if req and req.items else ""
        print(f"  - {name}{bl_summary}{req_summary}")
    return 0


def _enqueue(kind: str, params_json: str | None) -> int:
    kinds = _registered_kinds()
    fn = kinds.get(kind)
    if fn is None:
        print(f"unknown kind: {kind!r}. Run `python3 -m jobs.cli kinds` to list.", file=sys.stderr)
        return 2
    params = {}
    if params_json:
        try:
            params = json.loads(params_json)
        except json.JSONDecodeError as exc:
            print(f"bad --params JSON: {exc}", file=sys.stderr)
            return 2
    if not isinstance(params, dict):
        print("--params must encode a JSON object", file=sys.stderr)
        return 2
    result = fn(**params) if params else fn()
    # huey returns a Result wrapper for tasks; it's truthy.
    print(f"enqueued: {kind} → result_id={getattr(result, 'id', '?')}")
    return 0


def _status() -> int:
    from jobs import huey
    storage = huey.storage
    pending = storage.queue_size()
    print(f"queue_size: {pending}")
    print(f"db: {storage.filename}")
    return 0


def _doctor() -> int:
    """Enqueue nop and wait up to 10s for the consumer to run it."""
    print("doctor: enqueueing nop…")
    try:
        from jobs.kinds.nop import nop
    except Exception as exc:
        print(f"FAIL: import error: {exc}", file=sys.stderr)
        return 2
    result = nop({"hello": "world"})
    print(f"doctor: enqueued result_id={result.id}; waiting up to 10s…")
    try:
        out = result(blocking=True, timeout=10)
    except Exception as exc:
        print(f"FAIL: consumer didn't pick up the job in 10s ({exc}). "
              "Is the huey-consumer LaunchAgent loaded?", file=sys.stderr)
        return 1
    print(f"PASS: nop returned {out}")
    return 0


def _new(name: str) -> int:
    """Scaffold a new jobs/kinds/<name>.py from a tiny template."""
    if not name.replace("_", "").isalnum():
        print(f"name must be alnum + underscore only: {name!r}", file=sys.stderr)
        return 2
    target = Path(__file__).parent / "kinds" / f"{name}.py"
    if target.exists():
        print(f"already exists: {target}", file=sys.stderr)
        return 1
    template = (
        '"""TODO: describe what this Job does and why."""\n'
        "from __future__ import annotations\n\n"
        "from jobs import huey, requires, baseline\n\n\n"
        "@huey.task()\n"
        "@requires([])  # e.g. ['secret:SLACK_BOT_TOKEN', 'db:health.db']\n"
        f"def {name}(payload: dict | None = None) -> dict:\n"
        '    """TODO."""\n'
        "    return {\"ok\": True}\n"
    )
    target.write_text(template)
    print(f"created: {target}")
    return 0


def _migrate(kind: str) -> int:
    """Begin a migration: rename old plist to .plist.disabled, record baseline."""
    from jobs.lib import get_baseline
    kinds = _registered_kinds()
    fn = kinds.get(kind)
    if fn is None:
        print(f"unknown kind: {kind!r}", file=sys.stderr)
        return 2
    bl = get_baseline(fn)
    if bl is None:
        print(f"kind {kind!r} has no @baseline declared — cannot migrate safely", file=sys.stderr)
        return 2
    plist_label = f"com.home-tools.{kind.replace('_', '-')}"

    # Find the old plist on the mini.
    candidates = [
        Path.home() / "Library" / "LaunchAgents" / f"{plist_label}.plist",
    ]
    plist = next((c for c in candidates if c.exists()), None)
    if plist is None:
        print(
            f"old plist not found for {plist_label!r}.\n"
            f"  looked in: {candidates}\n"
            f"  if this kind has no pre-existing plist, run `enqueue` directly instead.",
            file=sys.stderr,
        )
        return 1

    # Rename → .disabled (rollback-ready).
    disabled = plist.with_suffix(plist.suffix + ".disabled")
    if disabled.exists():
        print(f"already migrating? {disabled} already exists.", file=sys.stderr)
        return 1
    plist.rename(disabled)
    try:
        subprocess.run(["launchctl", "unload", str(disabled)], check=False, capture_output=True)
    except FileNotFoundError:
        pass

    # Determine cadence from huey's task wrapper.
    cadence_seconds = getattr(fn, "_cadence_seconds", 0) or _guess_cadence(fn)

    from jobs.kinds._internal.migration_verifier import load_state, save_state, log_incident
    state = load_state()
    state.setdefault("in_flight", {})[kind] = {
        "kind": kind,
        "plist_label": plist_label,
        "plist_source_path": str(plist),
        "cadence_seconds": cadence_seconds,
        "baseline_metric": bl.metric,
        "divergence_window": bl.divergence_window,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "hours_soaked": 0,
        "last_fire": "",
        "last_check": "",
        "notes": [],
    }
    save_state(state)
    log_incident("migration_begun", kind=kind, baseline=bl.metric, window=bl.divergence_window)
    print(f"migrated {kind} (old plist → {disabled.name}). Verifier will check hourly.")
    return 0


def _guess_cadence(fn) -> int:
    """Heuristic — fall back to a conservative 1-hour cadence."""
    return 3600


def _rollback(kind: str) -> int:
    from jobs.kinds._internal.migration_verifier import load_state, save_state, rollback as do_rollback
    state = load_state()
    m = state.get("in_flight", {}).get(kind)
    if not m:
        print(f"{kind} is not in_flight", file=sys.stderr)
        return 1
    do_rollback(m, reason="manual", evidence={"by": "cli"})
    state.setdefault("rolled_back", []).append({**m, "reason": "manual", "at": datetime.now(timezone.utc).isoformat()})
    del state["in_flight"][kind]
    save_state(state)
    print(f"rolled back {kind}")
    return 0


def _halt_verifier(kind: str) -> int:
    from jobs.kinds._internal.migration_verifier import load_state, save_state
    state = load_state()
    if kind not in state.get("in_flight", {}):
        print(f"{kind} is not in_flight", file=sys.stderr)
        return 1
    state["in_flight"][kind].setdefault("notes", []).append(
        f"verifier halted manually at {datetime.now(timezone.utc).isoformat()}",
    )
    state["in_flight"][kind]["halted"] = True
    save_state(state)
    print(f"verifier paused for {kind}; resume by editing migrations.json `halted` → false")
    return 0


def _cleanup_soaked() -> int:
    from jobs.kinds._internal.migration_verifier import load_state
    state = load_state()
    promoted = state.get("promoted", [])
    if not promoted:
        print("no soaked migrations to clean up.")
        return 0
    removed = 0
    for m in promoted:
        plist = Path(m["plist_source_path"])
        disabled = plist.with_suffix(plist.suffix + ".disabled")
        if disabled.exists():
            disabled.unlink()
            removed += 1
    print(f"removed {removed} .disabled plist file(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jobs.cli", description="Mini Jobs framework CLI")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp_enq = sp.add_parser("enqueue", help="enqueue a one-shot run of a kind")
    sp_enq.add_argument("kind")
    sp_enq.add_argument("--params", help="JSON object passed to the Job function")

    sp.add_parser("status", help="queue depth + recent results")
    sp.add_parser("kinds", help="list registered Job kinds")
    sp.add_parser("doctor", help="smoke-test consumer with nop()")

    sp_new = sp.add_parser("new", help="scaffold jobs/kinds/<name>.py")
    sp_new.add_argument("name")

    sp_mig = sp.add_parser("migrate", help="begin a migration: rename old plist → .disabled")
    sp_mig.add_argument("kind")

    sp_rb = sp.add_parser("rollback", help="manual rollback of an in-flight migration")
    sp_rb.add_argument("kind")

    sp_halt = sp.add_parser("halt-verifier", help="pause verifier checks for one kind")
    sp_halt.add_argument("kind")

    sp.add_parser("cleanup-soaked", help="remove .disabled plists for promoted migrations")

    args = p.parse_args(argv)

    if args.cmd == "enqueue":
        return _enqueue(args.kind, args.params)
    if args.cmd == "status":
        return _status()
    if args.cmd == "kinds":
        return _print_kinds()
    if args.cmd == "doctor":
        return _doctor()
    if args.cmd == "new":
        return _new(args.name)
    if args.cmd == "migrate":
        return _migrate(args.kind)
    if args.cmd == "rollback":
        return _rollback(args.kind)
    if args.cmd == "halt-verifier":
        return _halt_verifier(args.kind)
    if args.cmd == "cleanup-soaked":
        return _cleanup_soaked()
    p.error(f"unknown subcommand {args.cmd!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
