#!/usr/bin/env bash
# Idempotent: mount Whale_Shark SMB share at /Volumes/Share1 if not already.
#
# Why password-in-URL instead of bare keychain lookup: macOS' mount_smbfs
# uses a NetFS-bookmark internal lookup that doesn't match plain
# `security add-internet-password` entries reliably from headless contexts.
# Our entry IS in the keychain (queryable via `security
# find-internet-password`), but mount_smbfs's NetFS path doesn't see it.
# Workaround: pull the password from the keychain at mount time, URL-encode
# it, and embed it in the mount URL. Password is briefly visible in argv
# during the mount call.
#
# One-time setup expected:
#   /Volumes/Share1 exists and is owned by `homeserver:staff`
#     (sudo mkdir -p /Volumes/Share1 && sudo chown homeserver:staff /Volumes/Share1)
#   security entry: -s "Whale_Shark" -a iananny -r "smb " in login.keychain-db

set -uo pipefail

MOUNT_POINT="/Volumes/Share1"
SMB_HOST="whale_shark._smb._tcp.local"
SMB_USER="iananny"
SMB_SHARE="Share1"
KEYCHAIN_SERVER="Whale_Shark"
KEYCHAIN_PATH="$HOME/Library/Keychains/login.keychain-db"
LOG_PREFIX="$(date -u +%Y-%m-%dT%H:%M:%SZ) mount-nas:"

# Already mounted? Bail out quietly so the periodic StartInterval is cheap.
if mount | grep -q "on $MOUNT_POINT "; then
  echo "$LOG_PREFIX already mounted"
  exit 0
fi

# Mount point must exist; we don't try to create under /Volumes/ (root-only).
if [[ ! -d "$MOUNT_POINT" ]]; then
  echo "$LOG_PREFIX $MOUNT_POINT does not exist (run: sudo mkdir -p $MOUNT_POINT && sudo chown $USER:staff $MOUNT_POINT)" >&2
  exit 1
fi

# Pull password from keychain.
P="$(security find-internet-password -s "$KEYCHAIN_SERVER" -a "$SMB_USER" -w "$KEYCHAIN_PATH" 2>/dev/null)"
if [[ -z "$P" ]]; then
  echo "$LOG_PREFIX no password in keychain (service=$KEYCHAIN_SERVER, account=$SMB_USER)" >&2
  exit 1
fi

# URL-encode so special characters in the password don't break the URL parse.
P_ENC="$(/usr/bin/python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$P")"
unset P  # keep argv-visibility of the raw password as short as possible

mount_smbfs "//$SMB_USER:$P_ENC@$SMB_HOST/$SMB_SHARE" "$MOUNT_POINT" 2>&1
rc=$?
unset P_ENC

if (( rc == 0 )); then
  echo "$LOG_PREFIX mounted $MOUNT_POINT"
  exit 0
fi

echo "$LOG_PREFIX mount_smbfs exited $rc" >&2
exit "$rc"
