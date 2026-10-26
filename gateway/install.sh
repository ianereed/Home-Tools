#!/bin/bash
# Install / update the gateway LaunchAgent. Idempotent.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHAGENTS="$HOME/Library/LaunchAgents"
GATEWAY="$REPO/gateway"

[ -x /opt/homebrew/bin/caddy ] || brew install caddy
# validate parses + provisions without binding; any IP works for the {$TAILSCALE_IP} placeholder.
TAILSCALE_IP=127.0.0.1 /opt/homebrew/bin/caddy validate --config "$GATEWAY/Caddyfile" --adapter caddyfile

mkdir -p "$LAUNCHAGENTS" "$REPO/logs"
chmod +x "$GATEWAY/run.sh"

cp "$GATEWAY/config/com.home-tools.gateway.plist" "$LAUNCHAGENTS/com.home-tools.gateway.plist"
launchctl unload "$LAUNCHAGENTS/com.home-tools.gateway.plist" 2>/dev/null || true
launchctl load "$LAUNCHAGENTS/com.home-tools.gateway.plist"

echo "gateway loaded. Console: http://homeserver:8503/  Health: http://homeserver:8503/health/"
