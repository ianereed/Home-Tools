#!/bin/bash
# Wrapper for the jobs HTTP enqueue LaunchAgent.
# - Activates the project venv (shared with consumer)
# - Unlocks keychain to read HOME_TOOLS_HTTP_TOKEN
# - Binds to tailscale0 IP (Tailscale-only; not exposed on en0/lo0)

set -euo pipefail

cd "$(dirname "$0")/.."   # ~/Home-Tools

VENV="$(pwd)/jobs/.venv"
if [ ! -d "$VENV" ]; then
    /opt/homebrew/bin/python3.12 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r jobs/requirements.txt
fi
source "$VENV/bin/activate"

KEYCHAIN_PATH="${KEYCHAIN_PATH:-$HOME/Library/Keychains/login.keychain-db}"
security unlock-keychain -p "" "$KEYCHAIN_PATH" 2>/dev/null || true

export HOME_TOOLS_HTTP_TOKEN="$(security find-generic-password -a 'home-tools' -s 'jobs_http_token' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"

# tailscale0 IP (the address that is reachable from iPhone over Tailscale)
TAILSCALE_IP="$(ifconfig 2>/dev/null | awk '/utun.*100\./ {print $2; exit}')"
if [ -z "$TAILSCALE_IP" ]; then
    # Fallback: bind localhost only if Tailscale is down. Better than failing
    # the LaunchAgent — service-monitor will flag it.
    TAILSCALE_IP="127.0.0.1"
    echo "warn: no tailscale0 IP found, binding to 127.0.0.1" >&2
fi

exec "$VENV/bin/python" -m jobs.enqueue_http --host "$TAILSCALE_IP" --port "${JOBS_HTTP_PORT:-8504}"
