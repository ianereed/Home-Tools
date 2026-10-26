#!/bin/bash
# Wrapper for the gateway LaunchAgent — Caddy fronting console + health dashboard.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/sbin:/usr/sbin"

cd "$(dirname "$0")"   # ~/Home-Tools/gateway

# Tailscale IP — the gateway is the only tailnet-facing listener for the UIs.
# At boot launchd can start us before tailscaled has the 100.x utun address,
# so poll for up to 2 min, then exit nonzero so launchd keeps retrying.
set +e
TAILSCALE_IP=""
for _try in {1..24}; do
    TAILSCALE_IP="$(ifconfig 2>/dev/null | awk '/inet 100\./ {print $2; exit}')"
    [ -n "$TAILSCALE_IP" ] && break
    sleep 5
done
set -e
if [ -z "$TAILSCALE_IP" ]; then
    echo "error: no Tailscale IP (inet 100.*) after 120s; exiting so launchd retries" >&2
    exit 1
fi
export TAILSCALE_IP

exec /opt/homebrew/bin/caddy run --config "$(pwd)/Caddyfile" --adapter caddyfile
