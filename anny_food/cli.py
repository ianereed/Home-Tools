"""Anny Food breakdown CLI.

    python -m anny_food.cli export-pending [--ids 3 7] [--force]   > pending.json
    python -m anny_food.cli import breakdowns.json [--dry-run]     (or - for stdin)
    python -m anny_food.cli show 12

Typical loop (from a Claude Code session on the laptop):
    ssh homeserver@homeserver 'cd ~/Home-Tools && console/.venv/bin/python -m anny_food.cli export-pending'
    … compute breakdowns per anny_food/rules.md …
    ssh homeserver@homeserver 'cd ~/Home-Tools && console/.venv/bin/python -m anny_food.cli import - --dry-run' < out.json
"""
from __future__ import annotations

import argparse
import json
import sys

from anny_food import breakdown as _bd
from anny_food import db as _adb
from anny_food.targets import load_targets


def _cmd_export(conn, targets, args) -> int:
    payload = _bd.pending(conn, targets, ids=args.ids, force=args.force)
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    print(f"{len(payload['recipes'])} recipe(s), {len(payload['log'])} log entr(ies) pending",
          file=sys.stderr)
    return 0


def _cmd_import(conn, targets, args) -> int:
    raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    payload = json.loads(raw)
    rep = _bd.import_payload(conn, payload, targets, dry_run=args.dry_run)
    if not rep.ok:
        conn.rollback()
        for e in rep.errors:
            print(f"ERROR {e}", file=sys.stderr)
        print(f"{len(rep.errors)} error(s); nothing imported", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry run OK: would import {rep.recipes} recipe(s), "
              f"{rep.staples} staple(s), {rep.log} log entr(ies)")
        return 0
    conn.commit()
    print(f"imported {rep.recipes} recipe(s), {rep.staples} staple(s), {rep.log} log entr(ies)")
    return 0


def _fmt(v) -> str:
    return "" if v is None else f"{v:g}"


def _cmd_show(conn, targets, args) -> int:
    row = conn.execute("SELECT title, base_servings FROM recipes WHERE id = ?",
                       (args.recipe_id,)).fetchone()
    if row is None:
        print(f"no recipe {args.recipe_id}", file=sys.stderr)
        return 1
    n = _bd.recipe_nutrition(conn, args.recipe_id, targets)
    print(f"#{args.recipe_id} {row['title']}  (serves {row['base_servings']})")
    if not n.has_breakdown:
        print("  no breakdown yet")
    else:
        state = "STALE — ingredients changed" if n.stale else "current"
        print(f"  {state}; confidence {n.confidence}; model {n.model}")
    print(f"\n  {'key':<24}{'computed':>10}{'override':>10}{'effective':>11}  unit")
    for t in targets.rows:
        if not t.enabled or t.derived:
            continue
        mark = " *" if t.key in n.stale_overrides else ""
        print(f"  {t.key:<24}{_fmt(n.computed.get(t.key)):>10}"
              f"{_fmt(n.overrides.get(t.key)):>10}{_fmt(n.values[t.key]):>11}  {t.unit}{mark}")
    if n.stale_overrides:
        print("  * override set before the ingredients last changed")
    if n.breakdown:
        print("\n  breakdown:")
        for b in n.breakdown:
            note = f"  ({b['note']})" if b.get("note") else ""
            print(f"    {b.get('ingredient', '?')}: {b.get('amount', '')} → "
                  f"{b.get('key', '?')} {_fmt(b.get('eq'))}{note}")
    if n.safety_flags:
        print("\n  safety: " + "; ".join(targets.safety_flags.get(f, f) for f in n.safety_flags))
    if n.notes:
        print(f"\n  notes: {n.notes}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anny_food.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="recipes.db path (default: the live one)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export-pending", help="JSON of recipes/log entries needing a breakdown")
    p.add_argument("--ids", type=int, nargs="+", help="only these recipe ids")
    p.add_argument("--force", action="store_true", help="include up-to-date recipes too")
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser("import", help="load breakdowns JSON (see rules.md)")
    p.add_argument("file", help="path, or - for stdin")
    p.add_argument("--dry-run", action="store_true", help="validate only")
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("show", help="audit one recipe's breakdown")
    p.add_argument("recipe_id", type=int)
    p.set_defaults(func=_cmd_show)

    args = parser.parse_args(argv)
    _adb.init_db(args.db)
    conn = _adb.connect(args.db)
    try:
        return args.func(conn, load_targets(), args)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
