#!/usr/bin/env bash
# Installs the launchd agent to run the event aggregator every 15 minutes.
# Edit the ProgramArguments and WorkingDirectory in the .plist first.
set -euo pipefail

PLIST_SRC="$(dirname "$0")/com.home-tools.event-aggregator.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.home-tools.event-aggregator.plist"

cp "$PLIST_SRC" "$PLIST_DEST"
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo "Scheduler installed. Runs every 15 minutes."
echo "Logs: /tmp/antora-event-aggregator.log"
echo "To uninstall: launchctl unload $PLIST_DEST && rm $PLIST_DEST"
