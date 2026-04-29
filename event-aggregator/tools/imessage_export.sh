#!/bin/zsh
# Laptop-side wrapper invoked by com.home-tools.imessage-export.plist.
# Reads chat.db via tools/imessage_export.py, ships the JSONL over Tailscale
# SSH to the mini, atomically renames it on the receiver. set -euo pipefail
# means any step's failure aborts the rest — the next 10-min tick retries.
set -euo pipefail

LAPTOP_PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
REPO_ROOT="$HOME/Documents/GitHub/Home-Tools"
EXPORT_DIR="$HOME/imessage-export"
LOG_DIR="$HOME/Library/Logs/home-tools"
MINI_USER=homeserver
MINI_HOST=homeserver
MINI_DEST=/Users/homeserver/Home-Tools/event-aggregator/cache/imessage.jsonl

mkdir -p "$EXPORT_DIR" "$LOG_DIR"
chmod 700 "$EXPORT_DIR"

"$LAPTOP_PY" "$REPO_ROOT/event-aggregator/tools/imessage_export.py" \
    --out "$EXPORT_DIR/imessage.jsonl" --days 14

scp -o ConnectTimeout=10 -o BatchMode=yes \
    "$EXPORT_DIR/imessage.jsonl" \
    "$MINI_USER@$MINI_HOST:$MINI_DEST.tmp"

# Single ssh round-trip: chmod+atomic rename. Any failure leaves the previous
# good JSONL in place and dashboard goes stale, which is the desired signal.
ssh -o BatchMode=yes -o ConnectTimeout=10 "$MINI_USER@$MINI_HOST" \
    "chmod 600 '$MINI_DEST.tmp' && mv '$MINI_DEST.tmp' '$MINI_DEST'"
