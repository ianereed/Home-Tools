#!/bin/bash
# Phase 22 — fast-lane huey consumer LaunchAgent wrapper.
# Verbatim copy of run-consumer.sh, with the final exec line pointing at
# jobs.huey_fast instead of jobs.huey. Same venv, keychain unlock, token
# exports, and .env loaders — the fast lane shares the slow lane's env.

set -euo pipefail

cd "$(dirname "$0")/.."   # ~/Home-Tools

VENV="$(pwd)/jobs/.venv"
if [ ! -d "$VENV" ]; then
    echo "creating venv at $VENV" >&2
    /opt/homebrew/bin/python3.12 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r jobs/requirements.txt
fi
source "$VENV/bin/activate"

# Keychain self-unlock — see feedback_keychain_audit_session_unlock_scope.md
KEYCHAIN_PATH="${KEYCHAIN_PATH:-$HOME/Library/Keychains/login.keychain-db}"
security unlock-keychain -p "" "$KEYCHAIN_PATH" 2>/dev/null || true

# Pull tokens out into env vars (lazy adapters read os.environ).
export SLACK_BOT_TOKEN="$(security find-generic-password -s 'dispatcher-slack' -a 'bot_token' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"
export TODOIST_API_TOKEN="$(security find-generic-password -a 'home-tools' -s 'todoist_api_token' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"
export HOME_TOOLS_HTTP_TOKEN="$(security find-generic-password -a 'home-tools' -s 'jobs_http_token' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"
# Restic per-repo passwords (used by migration_verifier's restic-snapshot-count check).
# Keychain service+account match Mac-mini/scripts/restic-backup.py:get_password.
export RESTIC_PASSWORD_RESTIC_HOURLY="$(security find-generic-password -a 'password' -s 'restic-hourly-backup' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"
export RESTIC_PASSWORD_RESTIC_DAILY="$(security find-generic-password -a 'password' -s 'restic-daily-backup' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"

# Load meal-planner config (TODOIST_SECTIONS, GEMINI_API_KEY, TODOIST_PROJECT_ID, etc.).
# Use line-by-line read instead of `source` to avoid bash brace-expanding JSON values.
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

# Load event-aggregator Ollama config (LOCAL_VISION_MODEL, OLLAMA_NUM_CTX_VISION, etc.).
if [ -f "$(pwd)/event-aggregator/.env" ]; then
    while IFS= read -r _line || [[ -n "$_line" ]]; do
        [[ "$_line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${_line// }" ]] && continue
        _key="${_line%%=*}"
        _val="${_line#*=}"
        export "$_key=$_val"
    done < "$(pwd)/event-aggregator/.env"
    unset _line _key _val
fi

exec "$VENV/bin/huey_consumer" jobs.huey_fast -w 1 -k thread
