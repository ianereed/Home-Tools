#!/bin/bash
# Wrapper for the Mini Ops console LaunchAgent.
# Reuses the jobs/ venv since console depends on streamlit + jobs imports.
set -euo pipefail

cd "$(dirname "$0")/.."   # ~/Home-Tools

VENV="$(pwd)/console/.venv"
if [ ! -d "$VENV" ]; then
    /opt/homebrew/bin/python3.12 -m venv "$VENV"
    "$VENV/bin/pip" install -q streamlit requests
    # Console imports jobs.huey, so install jobs reqs into the same venv
    "$VENV/bin/pip" install -q -r jobs/requirements.txt
fi
source "$VENV/bin/activate"

# Tailscale IP (so we don't expose console on en0/lo0)
TAILSCALE_IP="$(ifconfig 2>/dev/null | awk '/utun.*100\./ {print $2; exit}')"
TAILSCALE_IP="${TAILSCALE_IP:-127.0.0.1}"

exec "$VENV/bin/streamlit" run console/app.py \
    --server.port 8503 \
    --server.address "$TAILSCALE_IP" \
    --server.headless true \
    --browser.gatherUsageStats false
