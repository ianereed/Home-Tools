#!/usr/bin/env bash
# restic-bare-metal-restore.sh — documented manual recovery walkthrough.
#
# This is the script you run on a fresh mini after total loss. It does not
# run automatically; it's the procedure RECOVERY.md points at.
#
# Prerequisites (you have these from 1Password):
#   - NAS_USER, NAS_PASS, NAS_IP (SMB credentials for the iananny share)
#   - HOURLY_PW, DAILY_PW (restic repo passwords)
#
# Usage (interactive):
#   bash restic-bare-metal-restore.sh
#
# Usage (with all args, for the test gate):
#   bash restic-bare-metal-restore.sh <fake_home> <NAS_USER> <NAS_PASS> <NAS_IP> <HOURLY_PW> <DAILY_PW>
#
# Exit 0 = restore completed and integrity_check passed on health.db.
set -euo pipefail

if [ $# -eq 6 ]; then
  # Test-gate mode: all args provided, sandboxed HOME.
  FAKE_HOME="$1"
  NAS_USER="$2"
  NAS_PASS="$3"
  NAS_IP="$4"
  HOURLY_PW="$5"
  DAILY_PW="$6"
  export HOME="$FAKE_HOME"
  mkdir -p "$HOME/Share1"
  INTERACTIVE=0
elif [ $# -eq 0 ]; then
  # Interactive mode: prompt for each value.
  echo "== restic bare-metal recovery =="
  echo "Open your 1Password 'Mac mini home server recovery' note for these values."
  read -p "NAS SMB user: " NAS_USER
  read -s -p "NAS SMB password: " NAS_PASS; echo
  read -p "NAS IP (e.g. 192.168.4.39): " NAS_IP
  read -s -p "restic hourly password: " HOURLY_PW; echo
  read -s -p "restic daily password: " DAILY_PW; echo
  mkdir -p "$HOME/Share1"
  INTERACTIVE=1
else
  echo "usage: $0  (interactive)" >&2
  echo "       $0 <fake_home> <NAS_USER> <NAS_PASS> <NAS_IP> <HOURLY_PW> <DAILY_PW>" >&2
  exit 2
fi

# Verify restic + sqlite3 are installed.
command -v restic >/dev/null 2>&1 || { echo "FAIL: restic not installed (brew install restic)"; exit 1; }
command -v sqlite3 >/dev/null 2>&1 || { echo "FAIL: sqlite3 not on PATH"; exit 1; }
command -v mount_smbfs >/dev/null 2>&1 || { echo "FAIL: mount_smbfs not on PATH (macOS only)"; exit 1; }

# 1. Mount the NAS.
# In test-gate mode (sandboxed HOME), the live mount on the parent user's
# ~/Share1 is reused by symlink because mount_smbfs refuses concurrent
# mounts of the same share. In real bare-metal mode, mount fresh.
SHARE="$HOME/Share1"
DID_MOUNT=0
LIVE_SHARE="$(eval echo "~$(whoami)/Share1")"
if mount | grep -q " on $SHARE "; then
  echo "[1/5] NAS already mounted at $SHARE"
elif [ "${INTERACTIVE:-0}" = "0" ] && [ -n "$LIVE_SHARE" ] && mount | grep -q " on $LIVE_SHARE "; then
  echo "[1/5] reusing live SMB mount at $LIVE_SHARE (symlink for sandboxed test)"
  rm -rf "$SHARE"  # was mkdir'd above; replace with symlink
  ln -s "$LIVE_SHARE" "$SHARE"
else
  echo "[1/5] mounting NAS at $SHARE"
  mount_smbfs "//${NAS_USER}:${NAS_PASS}@${NAS_IP}/Share1" "$SHARE"
  DID_MOUNT=1
fi

cleanup() {
  if [ "${INTERACTIVE:-0}" = "0" ] && [ "$DID_MOUNT" = "1" ]; then
    umount "$SHARE" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# 2. Verify both repos are present.
echo "[2/5] verifying both repos"
HOURLY_REPO="$SHARE/mac-mini-backups/restic-hourly"
DAILY_REPO="$SHARE/mac-mini-backups/restic-daily"
[ -d "$HOURLY_REPO/keys" ] && [ -f "$HOURLY_REPO/config" ] || { echo "FAIL: hourly repo missing or corrupt at $HOURLY_REPO"; exit 1; }
[ -d "$DAILY_REPO/keys" ] && [ -f "$DAILY_REPO/config" ] || { echo "FAIL: daily repo missing or corrupt at $DAILY_REPO"; exit 1; }

# 3. Restore daily first (gets .env, keychain, state files).
echo "[3/5] restoring from daily repo"
TARGET="$HOME/restored"
mkdir -p "$TARGET"
RESTIC_REPOSITORY="$DAILY_REPO" RESTIC_PASSWORD="$DAILY_PW" \
  restic restore latest --target "$TARGET"

# 4. Restore hourly (gets latest health.db).
echo "[4/5] restoring from hourly repo"
RESTIC_REPOSITORY="$HOURLY_REPO" RESTIC_PASSWORD="$HOURLY_PW" \
  restic restore latest --target "$TARGET"

# 5. Verify the load-bearing files restored cleanly.
echo "[5/5] verifying restore"
HEALTH_DB="$(find "$TARGET" -name 'health.db' -type f | head -1)"
ENV_FILE="$(find "$TARGET" -path '*event-aggregator/.env' -type f | head -1)"

if [ -z "$HEALTH_DB" ]; then
  echo "FAIL: health.db not found in restored tree"
  exit 1
fi
echo "  health.db: $HEALTH_DB ($(stat -f %z "$HEALTH_DB" 2>/dev/null || stat -c %s "$HEALTH_DB") bytes)"

INTEGRITY="$(sqlite3 "$HEALTH_DB" 'PRAGMA integrity_check;' 2>&1 | head -1)"
echo "  health.db integrity_check: $INTEGRITY"
if [ "$INTEGRITY" != "ok" ]; then
  echo "FAIL: health.db integrity_check"
  exit 1
fi

if [ -z "$ENV_FILE" ]; then
  echo "FAIL: event-aggregator/.env not found — bare-metal recovery is broken (NAS creds wouldn't survive)"
  exit 1
fi
echo "  event-aggregator/.env: present ($ENV_FILE)"

echo
echo "PASS — bare-metal recovery succeeded. Restored tree under: $TARGET"
echo "Next steps for a real recovery (not the test gate):"
echo "  1. Copy restored files into place: ~/Home-Tools/, ~/Library/Keychains/"
echo "  2. Run install scripts for each project (event-aggregator, dispatcher, etc.)"
echo "  3. Reload LaunchAgents, run preflight.py, verify zero drift"
