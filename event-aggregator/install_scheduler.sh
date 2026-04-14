#!/usr/bin/env bash
# Installs the launchd agent to run the event aggregator every 15 minutes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.home-tools.event-aggregator.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.home-tools.event-aggregator.plist"

# Validate plist is well-formed before touching anything
if ! plutil -lint "$PLIST_SRC" > /dev/null 2>&1; then
    echo "Error: plist is invalid: $PLIST_SRC" >&2
    exit 1
fi

# Resolve Python interpreter and write it into the installed plist
PYTHON_BIN="$(/usr/bin/which python3 2>/dev/null || echo "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3")"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Error: python3 not found at $PYTHON_BIN — install Python or update the plist manually" >&2
    exit 1
fi
mkdir -p "$(dirname "$PLIST_DEST")"
sed "s|/Library/Frameworks/Python.framework/Versions/3.14/bin/python3|${PYTHON_BIN}|g" \
    "$PLIST_SRC" > "$PLIST_DEST"

# Load the agent
launchctl unload "$PLIST_DEST" 2>/dev/null || true
if ! launchctl load "$PLIST_DEST"; then
    echo "Error: launchctl load failed — check $PLIST_DEST and error logs" >&2
    exit 1
fi

# Harden permissions on sensitive files
chmod 600 "$SCRIPT_DIR/.env" 2>/dev/null || true
chmod 600 "$SCRIPT_DIR"/credentials/*.json 2>/dev/null || true

echo "Scheduler installed using $PYTHON_BIN. Runs every 15 minutes."
echo "Logs:   /tmp/home-tools-event-aggregator.log"
echo "Errors: /tmp/home-tools-event-aggregator-error.log"
echo "Status: launchctl list | grep event-aggregator"
echo "Remove: launchctl unload $PLIST_DEST && rm $PLIST_DEST"
