#!/bin/bash
# Wrapper for the Mini Ops console LaunchAgent.
# Reuses the jobs/ venv since console depends on streamlit + jobs imports.
set -euo pipefail

# launchd's environment is sparse — /sbin is needed for ifconfig on macOS.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/sbin:/usr/sbin"

cd "$(dirname "$0")/.."   # ~/Home-Tools

VENV="$(pwd)/console/.venv"
if [ ! -d "$VENV" ]; then
    /opt/homebrew/bin/python3.12 -m venv "$VENV"
    "$VENV/bin/pip" install -q streamlit requests
    # Console imports jobs.huey, so install jobs reqs into the same venv
    "$VENV/bin/pip" install -q -r jobs/requirements.txt
fi
source "$VENV/bin/activate"

KEYCHAIN_PATH="${KEYCHAIN_PATH:-$HOME/Library/Keychains/login.keychain-db}"
security unlock-keychain -p "" "$KEYCHAIN_PATH" 2>/dev/null || true
export HOME_TOOLS_HTTP_TOKEN="$(security find-generic-password -a 'home-tools' -s 'jobs_http_token' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"

# Phase 21 v2: Capture tab calls meal_planner.runner.process_iphone_intake_sync
# directly inside Streamlit, so this process needs GEMINI_API_KEY + Todoist
# config in env. Mirrors jobs/run-consumer.sh.
export TODOIST_API_TOKEN="$(security find-generic-password -a 'home-tools' -s 'todoist_api_token' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"

# Load meal_planner/.env (TODOIST_SECTIONS, GEMINI_API_KEY, TODOIST_PROJECT_ID).
# Line-by-line read avoids bash brace-expanding the JSON in TODOIST_SECTIONS.
if [ -f "$(pwd)/meal_planner/.env" ]; then
    while IFS= read -r _line || [[ -n "$_line" ]]; do
        [[ "$_line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${_line// }" ]] && continue
        _key="${_line%%=*}"
        _val="${_line#*=}"
        export "$_key=$_val"
    done < "$(pwd)/meal_planner/.env"
    unset _line _key _val
fi

# Tailscale IP (so we don't expose console on en0/lo0). Don't let a missing
# ifconfig kill the script — fall back to localhost.
set +e
TAILSCALE_IP="$(ifconfig 2>/dev/null | awk '/inet 100\./ {print $2; exit}')"
set -e
TAILSCALE_IP="${TAILSCALE_IP:-127.0.0.1}"

# jobs-http binds to the Tailscale IP; MagicDNS self-resolution doesn't work
# on macOS, so point the jobs client directly at the detected IP.
export HOME_TOOLS_HTTP_URL="http://${TAILSCALE_IP}:8504"

# Session resilience over a flaky tailnet link: keep a disconnected session's
# widget state alive for 15 min (default 120s) so a reconnect resumes the SAME
# session instead of dropping in-progress edits, and ping the client every 20s
# to keep the websocket alive / detect drops faster. Streamlit's own docs name
# the "Connection error" symptom as the case for tuning websocketPingInterval.
exec "$VENV/bin/streamlit" run console/app.py \
    --server.port 8503 \
    --server.address "$TAILSCALE_IP" \
    --server.headless true \
    --server.disconnectedSessionTTL 900 \
    --server.websocketPingInterval 20 \
    --browser.gatherUsageStats false
