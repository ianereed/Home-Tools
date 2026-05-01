#!/usr/bin/env python3
"""Phase 7 NAS backup wrapper.

Backs up priority files from ~/Home-Tools to the iananny NAS via restic.
Reads the repo password from the login keychain. Writes incidents.jsonl
state-change events with 2-fire debouncing. Logs to
~/Library/Logs/home-tools/restic-{profile}.log.

Usage: restic-backup.py --profile {hourly,daily}

See Mac-mini/PHASE7.md for the operator runbook.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ["HOME"])
BACKUP_ROOT = HOME / "Share1" / "mac-mini-backups"
RUN_DIR = HOME / "Home-Tools" / "run"
LOGS_DIR = HOME / "Home-Tools" / "logs"
INCIDENTS_FILE = LOGS_DIR / "incidents.jsonl"
KEYCHAIN_PATH = os.environ.get(
    "KEYCHAIN_PATH",
    str(HOME / "Library" / "Keychains" / "login.keychain-db"),
)
MOUNT_NAS_SH = HOME / "Home-Tools" / "Mac-mini" / "scripts" / "mount-nas.sh"

PROFILES: dict[str, dict] = {
    "hourly": {
        "repo_dir": BACKUP_ROOT / "restic-hourly",
        "keychain_service": "restic-hourly-backup",
        "files": [
            HOME / "Home-Tools" / "health-dashboard" / "data" / "health.db",
        ],
        "forget_args": [
            "--keep-hourly", "24",
            "--keep-daily", "14",
            "--keep-weekly", "8",
            "--keep-monthly", "12",
        ],
    },
    "daily": {
        "repo_dir": BACKUP_ROOT / "restic-daily",
        "keychain_service": "restic-daily-backup",
        "files": [
            HOME / "Home-Tools" / "event-aggregator" / "state.json",
            HOME / "Home-Tools" / "event-aggregator" / "event_log.jsonl",
            HOME / "Home-Tools" / "event-aggregator" / ".env",
            HOME / "Home-Tools" / "finance-monitor" / "data" / "finance.db",
            HOME / "Home-Tools" / "nas-intake" / "state.json",
            HOME / "Library" / "Keychains" / "login.keychain-db",
            HOME / "Home-Tools" / "logs" / "incidents.jsonl",
        ],
        "forget_args": [
            "--keep-daily", "30",
            "--keep-weekly", "12",
            "--keep-monthly", "24",
        ],
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def unlock_keychain() -> None:
    """Empty-password unlock per the Mac-mini keychain shim pattern."""
    subprocess.run(
        ["security", "unlock-keychain", "-p", "", KEYCHAIN_PATH],
        capture_output=True, check=False,
    )


def get_password(service: str, account: str = "password") -> str:
    p = subprocess.run(
        [
            "security", "find-generic-password",
            "-s", service, "-a", account, "-w", KEYCHAIN_PATH,
        ],
        capture_output=True, text=True, check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"keychain lookup failed for {service}/{account}: "
            f"exit={p.returncode} stderr={p.stderr.strip()}"
        )
    return p.stdout.strip()


def ensure_nas_mounted() -> bool:
    """Returns True if ~/Share1 is mounted (or could be re-mounted).

    Uses os.path.ismount() rather than iterdir() so the check works even
    without Full Disk Access — TCC silently filters iterdir() output on
    SMB mounts when the launchd-spawned python lacks FDA. ismount() reads
    only filesystem metadata (st_dev), which doesn't trigger TCC.
    """
    share = HOME / "Share1"
    if os.path.ismount(share):
        return True
    if MOUNT_NAS_SH.exists():
        subprocess.run(["bash", str(MOUNT_NAS_SH)], capture_output=True, check=False)
    return os.path.ismount(share)


def repo_health(repo_dir: Path) -> str:
    """Return 'ok', 'missing' (cleanly absent), or 'corrupt' (partial init)."""
    if not repo_dir.exists():
        return "missing"
    config_file = repo_dir / "config"
    keys_dir = repo_dir / "keys"
    config_present = config_file.exists()
    keys_present = keys_dir.exists() and keys_dir.is_dir() and any(keys_dir.iterdir())
    if not config_present and not keys_present:
        return "missing"
    if config_present and keys_present:
        return "ok"
    return "corrupt"


def run_restic(repo_dir: Path, password: str, args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["RESTIC_REPOSITORY"] = str(repo_dir)
    env["RESTIC_PASSWORD"] = password
    return subprocess.run(
        ["restic"] + args,
        env=env, capture_output=True, text=True, check=False,
    )


def append_event(event: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with INCIDENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def emit_state_change(profile: str, observed: str) -> None:
    """2-fire debouncing on state-change emission.

    Only emits to incidents.jsonl after the same new state is observed
    twice in a row. Catches flapping NAS noise (fail->ok->fail rapidly).
    """
    state_file = RUN_DIR / f"restic-{profile}-state.json"
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except (OSError, json.JSONDecodeError):
            state = {"observed": None, "consecutive": 0, "emitted": "ok"}
    else:
        state = {"observed": None, "consecutive": 0, "emitted": "ok"}

    if observed == state["observed"]:
        state["consecutive"] = state.get("consecutive", 0) + 1
    else:
        state["observed"] = observed
        state["consecutive"] = 1

    if state["consecutive"] >= 2 and state["observed"] != state.get("emitted"):
        prior = state.get("emitted", "ok")
        append_event({
            "ts": now_iso(),
            "kind": "state_change",
            "key": f"backup:{profile}",
            "prior": prior,
            "current": state["observed"],
        })
        state["emitted"] = state["observed"]

    state_file.write_text(json.dumps(state, indent=2, sort_keys=True))


def emit_repo_corrupt(profile: str, repo_dir: Path) -> None:
    """Distinct event kind so the operator can tell 'transient' from 'broken'."""
    append_event({
        "ts": now_iso(),
        "kind": "repo_corrupt",
        "key": f"backup:{profile}",
        "repo": str(repo_dir),
    })


def write_failed_flag(profile: str, reason: str) -> None:
    flag = RUN_DIR / f"restic-{profile}-failed.flag"
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    flag.write_text(json.dumps({"ts": now_iso(), "reason": reason}, indent=2))


def clear_failed_flag(profile: str) -> None:
    flag = RUN_DIR / f"restic-{profile}-failed.flag"
    if flag.exists():
        flag.unlink()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True, choices=list(PROFILES.keys()))
    p.add_argument("--dry-run", action="store_true", help="Pass --dry-run to restic backup")
    args = p.parse_args()

    profile = args.profile
    spec = PROFILES[profile]
    repo_dir: Path = spec["repo_dir"]

    print(f"restic-backup profile={profile} ts={now_iso()}", flush=True)

    unlock_keychain()

    try:
        password = get_password(spec["keychain_service"])
    except RuntimeError as e:
        print(f"ERROR: {e}", flush=True)
        write_failed_flag(profile, "keychain")
        emit_state_change(profile, "fail")
        return 1

    if not ensure_nas_mounted():
        print("ERROR: NAS not reachable, mount-nas.sh did not recover", flush=True)
        write_failed_flag(profile, "nas_unreachable")
        emit_state_change(profile, "fail")
        return 1

    health = repo_health(repo_dir)
    if health == "corrupt":
        print(f"ERROR: repo at {repo_dir} is corrupt (partial init?)", flush=True)
        write_failed_flag(profile, "repo_corrupt")
        emit_repo_corrupt(profile, repo_dir)
        return 1
    if health == "missing":
        print(f"ERROR: repo at {repo_dir} not initialized — refusing to auto-init", flush=True)
        write_failed_flag(profile, "repo_missing")
        emit_state_change(profile, "fail")
        return 1

    existing_files: list[str] = []
    missing_files: list[str] = []
    for f in spec["files"]:
        if f.exists():
            existing_files.append(str(f))
        else:
            missing_files.append(str(f))

    if missing_files:
        print(f"WARN: skipping missing files: {missing_files}", flush=True)

    if not existing_files:
        print("ERROR: no files to back up (all missing)", flush=True)
        write_failed_flag(profile, "no_files")
        emit_state_change(profile, "fail")
        return 1

    backup_args = ["backup"] + existing_files
    if args.dry_run:
        backup_args.append("--dry-run")

    t0 = time.time()
    r = run_restic(repo_dir, password, backup_args)
    dt = time.time() - t0

    if r.returncode != 0:
        print(f"ERROR: restic backup failed (exit {r.returncode}, {dt:.1f}s)", flush=True)
        if r.stdout:
            print(f"  stdout: {r.stdout[:500]}", flush=True)
        if r.stderr:
            print(f"  stderr: {r.stderr[:500]}", flush=True)
        write_failed_flag(profile, "backup_failed")
        emit_state_change(profile, "fail")
        return 1

    # Print last 4 lines of stdout (snapshot ID + summary).
    summary_lines = [ln for ln in r.stdout.splitlines() if ln.strip()][-4:]
    for ln in summary_lines:
        print(f"  {ln}", flush=True)
    print(f"backup ok ({dt:.1f}s)", flush=True)

    # Forget runs after each successful backup; prune is a separate weekly job.
    if not args.dry_run:
        forget_r = run_restic(repo_dir, password, ["forget"] + spec["forget_args"])
        if forget_r.returncode != 0:
            # Non-fatal: backup succeeded, retention can be retried next run.
            print(f"WARN: restic forget failed (exit {forget_r.returncode})", flush=True)
            if forget_r.stderr:
                print(f"  stderr: {forget_r.stderr[:300]}", flush=True)

    clear_failed_flag(profile)
    emit_state_change(profile, "ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
