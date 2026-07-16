#!/usr/bin/env python3
"""Import quarterly DEXA scan rows into the dashboard DB.

Source: cardiology/dexa_scans.csv (gitignored — CARDIO_PLAN.md Appendix C), one
row per scan. US DEXA reports print lb, so the CSV is lb-native; this script
converts to kg and writes BOTH body_composition (full DEXA snapshot) and
body_weight (so DEXA weigh-ins show up alongside Garmin/Apple weight),
source='dexa', timestamp f"{date}T00:00:00".

All writes are INSERT OR REPLACE keyed on UNIQUE(timestamp, source) — the whole
CSV re-imports idempotently, so correcting a row and re-running converges.

Usage:
    python3 import_dexa.py [--csv PATH] [--db PATH] [--init]

Defaults: --csv is the sibling dexa_scans.csv next to this script; --db is
data/health.db under the health-dashboard root (same file collectors.db.DB_PATH
points at). --init writes a header + example-row template to --csv and exits.
"""
import argparse
import csv
import datetime
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV_PATH = os.path.join(HERE, "dexa_scans.csv")
DEFAULT_DB_PATH = os.path.join(os.path.dirname(HERE), "data", "health.db")

LB_TO_KG = 0.45359237

TEMPLATE = (
    "date,weight_lb,body_fat_pct,lean_mass_lb,fat_mass_lb,bone_mass_lb,"
    "visceral_fat_lb,note\n"
    "2026-09-15,186.2,24.1,134.5,44.9,6.8,1.2,example row — delete me\n"
)

_REQUIRED_COLUMNS = [
    "date", "weight_lb", "body_fat_pct", "lean_mass_lb", "fat_mass_lb",
    "bone_mass_lb", "visceral_fat_lb", "note",
]


def lb_to_kg(lb):
    return lb * LB_TO_KG


def _optional_float(raw):
    raw = (raw or "").strip()
    return float(raw) if raw else None


def parse_row(row, line_no):
    """Validate + convert one CSV row (dict) to kg-native fields.

    Raises ValueError (line-numbered) on a hard-reject condition: unparseable
    date, or body_fat_pct/weight outside a physiologically plausible range.
    A lean+fat+bone-vs-weight mismatch is returned as `warning`, not raised —
    Appendix C says warn, don't fail (DEXA rounding/segmentation noise).
    """
    missing = [c for c in _REQUIRED_COLUMNS if c not in row]
    if missing:
        raise ValueError(f"line {line_no}: missing column(s) {missing}")

    date_raw = row["date"].strip()
    try:
        date = datetime.date.fromisoformat(date_raw).isoformat()
    except ValueError:
        raise ValueError(f"line {line_no}: unparseable date {date_raw!r}")

    try:
        weight_lb = float(row["weight_lb"])
    except (TypeError, ValueError):
        raise ValueError(
            f"line {line_no}: unparseable weight_lb {row['weight_lb']!r}")
    weight_kg = lb_to_kg(weight_lb)
    if not (40 <= weight_kg <= 200):
        raise ValueError(
            f"line {line_no}: weight_kg={weight_kg:.1f} out of range [40, 200]")

    try:
        body_fat_pct = float(row["body_fat_pct"])
    except (TypeError, ValueError):
        raise ValueError(
            f"line {line_no}: unparseable body_fat_pct {row['body_fat_pct']!r}")
    if not (3 <= body_fat_pct <= 60):
        raise ValueError(
            f"line {line_no}: body_fat_pct={body_fat_pct} out of range [3, 60]")

    lean_lb = _optional_float(row.get("lean_mass_lb"))
    fat_lb = _optional_float(row.get("fat_mass_lb"))
    bone_lb = _optional_float(row.get("bone_mass_lb"))
    visceral_lb = _optional_float(row.get("visceral_fat_lb"))
    note = (row.get("note") or "").strip() or None

    warning = None
    if lean_lb is not None and fat_lb is not None and bone_lb is not None:
        mass_sum_lb = lean_lb + fat_lb + bone_lb
        diff_pct = abs(mass_sum_lb - weight_lb) / weight_lb
        if diff_pct > 0.02:
            warning = (
                f"line {line_no}: lean+fat+bone={mass_sum_lb:.1f} lb vs "
                f"weight={weight_lb:.1f} lb ({diff_pct:.1%} off) — check CSV row")

    return {
        "date": date,
        "weight_kg": weight_kg,
        "body_fat_pct": body_fat_pct,
        "lean_mass_kg": lb_to_kg(lean_lb) if lean_lb is not None else None,
        "fat_mass_kg": lb_to_kg(fat_lb) if fat_lb is not None else None,
        "bone_mass_kg": lb_to_kg(bone_lb) if bone_lb is not None else None,
        "visceral_fat_mass_kg": lb_to_kg(visceral_lb) if visceral_lb is not None else None,
        "note": note,
        "warning": warning,
    }


def write_row(conn, parsed):
    ts = f"{parsed['date']}T00:00:00"
    conn.execute(
        "INSERT OR REPLACE INTO body_composition "
        "(timestamp, weight_kg, body_fat_pct, lean_mass_kg, fat_mass_kg, "
        "bone_mass_kg, visceral_fat_mass_kg, note, source) "
        "VALUES (?,?,?,?,?,?,?,?, 'dexa')",
        (ts, parsed["weight_kg"], parsed["body_fat_pct"], parsed["lean_mass_kg"],
         parsed["fat_mass_kg"], parsed["bone_mass_kg"],
         parsed["visceral_fat_mass_kg"], parsed["note"]),
    )
    conn.execute(
        "INSERT OR REPLACE INTO body_weight (timestamp, weight_kg, source) "
        "VALUES (?,?, 'dexa')",
        (ts, parsed["weight_kg"]),
    )


def import_csv(csv_path, db_path):
    """Import every row in csv_path into db_path. Returns a summary dict."""
    imported = []
    rejected = []
    warnings = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for line_no, row in enumerate(reader, start=2):  # header is line 1
            try:
                parsed = parse_row(row, line_no)
            except ValueError as exc:
                rejected.append(str(exc))
                continue
            if parsed["warning"]:
                warnings.append(parsed["warning"])
            imported.append(parsed)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        for parsed in imported:
            write_row(conn, parsed)
        conn.commit()
    finally:
        conn.close()

    return {
        "rows": len(imported) + len(rejected),
        "imported": len(imported),
        "rejected": rejected,
        "warnings": warnings,
    }


def write_template(path):
    if os.path.exists(path):
        sys.exit(f"{path} already exists — refusing to overwrite")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(TEMPLATE)
    print(f"wrote template to {path} — edit/delete the example row, then append "
          f"real scans")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Import DEXA scan CSV rows into body_composition + body_weight.")
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH,
                         help=f"path to dexa_scans.csv (default: {DEFAULT_CSV_PATH})")
    parser.add_argument("--db", default=DEFAULT_DB_PATH,
                         help=f"path to health.db (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--init", action="store_true",
                         help="write a header+example-row template to --csv and exit")
    args = parser.parse_args(argv)

    if args.init:
        write_template(args.csv)
        return

    if not os.path.exists(args.csv):
        sys.exit(f"{args.csv} not found — run with --init to create a template first")

    summary = import_csv(args.csv, args.db)
    print(f"rows={summary['rows']} imported={summary['imported']} "
          f"rejected={len(summary['rejected'])}")
    for msg in summary["warnings"]:
        print(f"WARNING: {msg}")
    for msg in summary["rejected"]:
        print(f"REJECTED: {msg}")
    if summary["rejected"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
