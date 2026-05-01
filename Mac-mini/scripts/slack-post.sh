#!/usr/bin/env bash
# slack-post.sh — POST a message to Slack using the dispatcher bot token.
#
# Usage: slack-post.sh "<channel-or-channel-id>" "<body>"
#   channel: "#ian-event-aggregator" or a channel ID like "C12345"
#   body:    text to post (Slack mrkdwn supported)
#
# Reads bot token from login keychain (service=dispatcher-slack, account=bot_token).
# Self-unlocks the keychain (empty password — see project_mac_mini_keychain_shim memory).
#
# On non-200 from Slack:
#   - writes ~/Home-Tools/run/digest-failed.flag with {ts, channel, rc, err_raw}
#   - writes a line to ~/Library/Logs/home-tools/phase6.log
#   - exits 1
#
# Override the bot token via SLACK_BOT_TOKEN_OVERRIDE env (used by test gate 8).
# Phase 6 — see Mac-mini/PHASE6.md.

set -uo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <channel> <body>" >&2
  exit 2
fi

CHANNEL="$1"
BODY="$2"
KEYCHAIN_PATH="${KEYCHAIN_PATH:-$HOME/Library/Keychains/login.keychain-db}"
LOG_DIR="$HOME/Library/Logs/home-tools"
LOGFILE="$LOG_DIR/phase6.log"
RUN_DIR="$HOME/Home-Tools/run"
FLAGFILE="$RUN_DIR/digest-failed.flag"

mkdir -p "$LOG_DIR" "$RUN_DIR"

# Resolve the bot token.
if [[ -n "${SLACK_BOT_TOKEN_OVERRIDE:-}" ]]; then
  BOT_TOKEN="$SLACK_BOT_TOKEN_OVERRIDE"
else
  security unlock-keychain -p '' "$KEYCHAIN_PATH" 2>/dev/null || true
  BOT_TOKEN="$(security find-generic-password -s dispatcher-slack -a bot_token -w "$KEYCHAIN_PATH" 2>/dev/null || true)"
fi

write_flag() {
  local rc="$1" err_raw="$2"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '%s slack-post: channel=%s rc=%d err=%s\n' "$ts" "$CHANNEL" "$rc" "$err_raw" >> "$LOGFILE"
  TS="$ts" CH="$CHANNEL" RC="$rc" ERR="$err_raw" python3 - > "$FLAGFILE" <<'PYEOF'
import json, os
print(json.dumps({
    "ts": os.environ["TS"],
    "channel": os.environ["CH"],
    "rc": int(os.environ["RC"]),
    "err_raw": os.environ["ERR"],
}))
PYEOF
}

if [[ -z "$BOT_TOKEN" ]]; then
  write_flag 90 "no_bot_token_in_keychain"
  echo "[slack-post] no bot token (keychain miss or empty SLACK_BOT_TOKEN_OVERRIDE)" >&2
  exit 1
fi

# POST via inline Python (urllib, no extra deps).
TMPERR=$(mktemp /tmp/slack-post.XXXXXX)
trap 'rm -f "$TMPERR"' EXIT

set +e
BOT_TOKEN="$BOT_TOKEN" CHANNEL="$CHANNEL" BODY="$BODY" python3 - 2>"$TMPERR" <<'PYEOF'
import json, os, sys, urllib.request, urllib.error
data = json.dumps({"channel": os.environ["CHANNEL"], "text": os.environ["BODY"]}).encode()
req = urllib.request.Request(
    "https://slack.com/api/chat.postMessage",
    data=data,
    headers={
        "Authorization": f"Bearer {os.environ['BOT_TOKEN']}",
        "Content-Type": "application/json; charset=utf-8",
    },
)
try:
    resp = urllib.request.urlopen(req, timeout=10)
    body = resp.read().decode()
    obj = json.loads(body)
    if obj.get("ok"):
        sys.exit(0)
    print(json.dumps({"http": resp.status, "error": obj.get("error", "unknown")}), file=sys.stderr)
    sys.exit(3)
except urllib.error.HTTPError as e:
    print(json.dumps({"http": e.code, "error": e.reason}), file=sys.stderr)
    sys.exit(4)
except Exception as e:
    print(json.dumps({"http": 0, "error": str(e)}), file=sys.stderr)
    sys.exit(5)
PYEOF
rc=$?
set -e

if [[ "$rc" -ne 0 ]]; then
  err_raw=$(cat "$TMPERR" 2>/dev/null || true)
  [[ -z "$err_raw" ]] && err_raw="no_stderr_rc=${rc}"
  write_flag "$rc" "$err_raw"
  echo "[slack-post] failed rc=$rc err=$err_raw" >&2
  exit 1
fi

# Success — clear any stale flag.
[[ -f "$FLAGFILE" ]] && rm -f "$FLAGFILE"
exit 0
