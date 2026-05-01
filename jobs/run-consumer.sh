#!/bin/bash
# Wrapper for the huey consumer LaunchAgent.
# - Activates the project venv
# - Unlocks the login keychain (LaunchAgents have their own audit session)
# - Exports SLACK_BOT_TOKEN + TODOIST_API_TOKEN + RESTIC_PASSWORD_* from keychain
# - exec into `huey_consumer.py jobs.huey`

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
export SLACK_BOT_TOKEN="$(security find-generic-password -a 'home-tools' -s 'slack_bot_token' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"
export TODOIST_API_TOKEN="$(security find-generic-password -a 'home-tools' -s 'todoist_api_token' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"
export HOME_TOOLS_HTTP_TOKEN="$(security find-generic-password -a 'home-tools' -s 'jobs_http_token' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"
# Restic per-repo passwords (used by migration_verifier's restic-snapshot-count check)
export RESTIC_PASSWORD_RESTIC_HOURLY="$(security find-generic-password -a 'home-tools' -s 'restic_pw_hourly' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"
export RESTIC_PASSWORD_RESTIC_DAILY="$(security find-generic-password -a 'home-tools' -s 'restic_pw_daily' -w "$KEYCHAIN_PATH" 2>/dev/null || echo '')"

exec "$VENV/bin/huey_consumer.py" jobs.huey -w 2 -k thread
