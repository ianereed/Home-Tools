#!/usr/bin/env python3
"""LaunchAgent drift checker for the Mac mini.

Reads service-monitor/services.py:SERVICES + KNOWN_UNMONITORED_LABELS as the
source of truth and reports drift across four dimensions:

  1. Loaded but not in SERVICES (and not in KNOWN_UNMONITORED_LABELS)
     → either an unexpected agent or a missing entry
  2. In SERVICES but not loaded
     → incomplete install (the install script never ran, or the agent crashed
       hard enough to be removed from launchctl)
  3. SERVICES.plist_source_path missing on disk
     → broken reference; install would fail
  4. Source plist contents differ from what's installed in ~/Library/LaunchAgents/
     → installed copy is stale; re-running install would fix it

Exit 0 if no drift, 1 if drift found. Quiet on success unless --verbose.

Designed to run in two contexts:
  - On the mini itself (uses local launchctl + ~/Library/LaunchAgents/)
  - From a laptop/CI before pushing — pass --no-launchctl to skip dimensions 1,2

Usage:
  bash Mac-mini/scripts/preflight.py             # full check (mini)
  bash Mac-mini/scripts/preflight.py --verbose   # show OK lines too
  bash Mac-mini/scripts/preflight.py --no-launchctl  # source-only check
"""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import subprocess
import sys
from pathlib import Path

# Bring service-monitor/ into sys.path so we can import services
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_REPO / "service-monitor"))

try:
    from services import SERVICES, KNOWN_UNMONITORED_LABELS  # type: ignore
except ImportError as e:
    print(f"FATAL: could not import services from service-monitor/: {e}", file=sys.stderr)
    sys.exit(2)


HOME = Path.home()
INSTALLED_DIR = HOME / "Library/LaunchAgents"
LABEL_PREFIXES = ("com.home-tools.", "com.health-dashboard.")


def loaded_labels() -> set[str]:
    """Labels currently loaded into launchctl (any state, including last-exit-nonzero)."""
    out = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True, check=False
    ).stdout
    labels = set()
    for line in out.splitlines()[1:]:  # skip header "PID\tStatus\tLabel"
        parts = line.split("\t")
        if len(parts) >= 3:
            label = parts[2].strip()
            if any(label.startswith(p) for p in LABEL_PREFIXES):
                labels.add(label)
    return labels


def installed_plists() -> set[Path]:
    """Plist files on disk in ~/Library/LaunchAgents/ that match our prefixes."""
    if not INSTALLED_DIR.exists():
        return set()
    return {
        p
        for p in INSTALLED_DIR.iterdir()
        if p.is_file()
        and p.suffix == ".plist"
        and any(p.name.startswith(prefix) for prefix in LABEL_PREFIXES)
    }


def label_from_plist_filename(p: Path) -> str:
    """com.home-tools.dispatcher.plist → com.home-tools.dispatcher"""
    return p.stem


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true", help="Show OK lines too")
    ap.add_argument("--no-launchctl", action="store_true",
                    help="Skip launchctl + installed-plist checks (use from a host that's not the mini)")
    args = ap.parse_args()

    drift = []
    ok_lines = []

    # Dimension 3 + 4: source plist files (always run)
    for svc in SERVICES:
        if not svc.plist_source_path:
            ok_lines.append(f"[skip-3-4] {svc.label}: no plist_source_path declared")
            continue
        src = _REPO / svc.plist_source_path
        if not src.exists():
            drift.append(
                f"[3] {svc.label}: declared plist_source_path missing on disk: "
                f"{svc.plist_source_path}"
            )
            continue
        ok_lines.append(f"[ok-3] {svc.label}: source plist present at {svc.plist_source_path}")

        if args.no_launchctl:
            continue

        installed = INSTALLED_DIR / f"{svc.label}.plist"
        if installed.exists():
            try:
                if not filecmp.cmp(src, installed, shallow=False):
                    drift.append(
                        f"[4] {svc.label}: installed copy at {installed} differs from "
                        f"source {svc.plist_source_path} — re-run install"
                    )
                else:
                    ok_lines.append(f"[ok-4] {svc.label}: installed copy matches source")
            except OSError as e:
                drift.append(f"[4] {svc.label}: cannot compare installed vs source: {e}")

    # Dimensions 1 + 2: launchctl + installed (mini-only by default)
    if not args.no_launchctl:
        loaded = loaded_labels()
        in_services = {s.label for s in SERVICES}
        unmonitored = set(KNOWN_UNMONITORED_LABELS)

        # Dimension 1: loaded but not in SERVICES (and not explicitly unmonitored)
        for label in sorted(loaded - in_services - unmonitored):
            drift.append(
                f"[1] {label}: loaded in launchctl but not in services.py:SERVICES "
                f"and not in KNOWN_UNMONITORED_LABELS"
            )

        # Soft dimension 1b: KNOWN_UNMONITORED that's NOT loaded → declared but absent
        for label in sorted(unmonitored - loaded):
            ok_lines.append(
                f"[skip-1b] {label}: declared as known-unmonitored but not currently loaded"
            )

        # Dimension 2: in SERVICES but not loaded
        for label in sorted(in_services - loaded):
            drift.append(f"[2] {label}: in SERVICES but not loaded — incomplete install?")

        for label in sorted(loaded & in_services):
            ok_lines.append(f"[ok-1-2] {label}: loaded and in SERVICES")

    if args.verbose or drift:
        for line in ok_lines:
            if args.verbose:
                print(line)
        for line in drift:
            print(line)

    if drift:
        print(f"\nDRIFT: {len(drift)} issue(s) found.")
        return 1
    print(f"OK: {len(SERVICES)} services, {'launchctl checked' if not args.no_launchctl else 'launchctl skipped'}, no drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
