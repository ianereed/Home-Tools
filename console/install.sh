#!/bin/bash
# Install / update the Mini Ops console LaunchAgent. Idempotent.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHAGENTS="$HOME/Library/LaunchAgents"
CONSOLE="$REPO/console"
LOGS="$REPO/logs"

mkdir -p "$LAUNCHAGENTS" "$LOGS"
chmod +x "$CONSOLE/run.sh"

# Drop plist + load.
cp "$CONSOLE/config/com.home-tools.console.plist" "$LAUNCHAGENTS/com.home-tools.console.plist"
launchctl unload "$LAUNCHAGENTS/com.home-tools.console.plist" 2>/dev/null || true
launchctl load "$LAUNCHAGENTS/com.home-tools.console.plist"

echo "console loaded. Wait ~10s for streamlit to bind, then visit http://homeserver:8503/"
