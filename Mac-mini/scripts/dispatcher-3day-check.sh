#!/usr/bin/env bash
# One-shot health check for the dispatcher LaunchAgent.
#
# Scheduled by Mac-mini/LaunchAgents/com.home-tools.dispatcher-3day-check.plist
# to fire on 2026-04-27 09:00 local. Self-unloads after firing so it doesn't
# re-fire annually.
#
# Posts a single message to #ian-event-aggregator via the dispatcher's existing
# bot token (read from the login keychain). Nothing leaves the LAN.

set -uo pipefail

TARGET_DATE="2026-04-27"
if [[ "$(date +%F)" != "$TARGET_DATE" ]]; then
  # Belt-and-suspenders: if the self-unload below ever failed in a prior run,
  # don't fire on the same calendar day next year.
  exit 0
fi

KEYCHAIN_PATH="$HOME/Library/Keychains/login.keychain-db"
DISPATCHER_DIR="$HOME/Home-Tools/dispatcher"
FINANCE_INTAKE="$HOME/Home-Tools/finance-monitor/intake"
CHANNEL="ian-event-aggregator"

attention=""
findings=()

# 1. LaunchAgent status — PID present, exit 0?
if launchctl list | grep -q com.home-tools.dispatcher$; then
  pid=$(launchctl list | awk '$3 == "com.home-tools.dispatcher" {print $1}')
  exit_status=$(launchctl list | awk '$3 == "com.home-tools.dispatcher" {print $2}')
  if [[ "$pid" == "-" ]]; then
    attention="ATTENTION"
    findings+=(":x: dispatcher loaded but no PID (last exit=$exit_status)")
  else
    findings+=(":white_check_mark: dispatcher PID $pid, exit=$exit_status")
  fi
else
  attention="ATTENTION"
  findings+=(":x: dispatcher LaunchAgent not loaded")
fi

# 2. Logs — error/traceback lines in the last 200 lines of stdout, plus the
#    size of the stderr log (anything substantial there is suspicious).
#    Plist log paths moved to ~/Library/Logs/ on 2026-04-24; check both
#    locations so this script keeps working if it ever ships ahead of the
#    plist change.
err_count=0
for log in "$HOME/Library/Logs/home-tools-dispatcher.log" /tmp/home-tools-dispatcher.log; do
  if [[ -f "$log" ]]; then
    err_count=$(tail -200 "$log" | grep -cE "ERROR|Traceback|Exception:" || true)
    break
  fi
done
err_log_size=0
for log in "$HOME/Library/Logs/home-tools-dispatcher-error.log" /tmp/home-tools-dispatcher-error.log; do
  if [[ -f "$log" ]]; then
    err_log_size=$(wc -c < "$log" | tr -d ' ')
    break
  fi
done
if (( err_count > 0 )); then
  findings+=(":warning: $err_count error/traceback line(s) in dispatcher.log tail")
fi
if (( err_log_size > 4096 )); then
  attention="ATTENTION"
  findings+=(":x: dispatcher-error.log is $err_log_size bytes")
elif (( err_log_size > 0 )); then
  findings+=(":warning: dispatcher-error.log is $err_log_size bytes")
fi

# 3. tmp/ backlog — files stuck here = classification failures
tmp_count=$(find "$DISPATCHER_DIR/tmp" -type f ! -name '.gitkeep' 2>/dev/null | wc -l | tr -d ' ')
if (( tmp_count > 5 )); then
  attention="ATTENTION"
  findings+=(":x: dispatcher/tmp/ backlog: $tmp_count file(s) — classification failures?")
elif (( tmp_count > 0 )); then
  findings+=(":warning: dispatcher/tmp/ has $tmp_count file(s)")
fi

# 4. nas-staging counts by category — Unsorted/ specifically flags low-confidence
staging_summary=""
unsorted_count=0
if [[ -d "$DISPATCHER_DIR/nas-staging" ]]; then
  while IFS= read -r dir; do
    cat=$(basename "$dir")
    count=$(find "$dir" -type f ! -name '.gitkeep' 2>/dev/null | wc -l | tr -d ' ')
    (( count > 0 )) && staging_summary+="$cat=$count "
    [[ "$cat" == "Unsorted" ]] && unsorted_count=$count
  done < <(find "$DISPATCHER_DIR/nas-staging" -mindepth 1 -maxdepth 1 -type d 2>/dev/null)
fi
if (( unsorted_count > 3 )); then
  attention="ATTENTION"
  findings+=(":x: nas-staging/Unsorted has $unsorted_count file(s) — classifier confidence issue?")
fi

# 5. finance-monitor intake — image files left here mean OCR failed
finance_stuck=0
if [[ -d "$FINANCE_INTAKE" ]]; then
  finance_stuck=$(find "$FINANCE_INTAKE" -maxdepth 1 -type f \
    \( -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' -o -name '*.heic' \
       -o -name '*.heif' -o -name '*.tiff' -o -name '*.webp' \) 2>/dev/null \
    | wc -l | tr -d ' ')
fi
if (( finance_stuck > 0 )); then
  attention="ATTENTION"
  findings+=(":x: $finance_stuck image(s) stuck in finance-monitor/intake/ — failed OCR")
fi

# Stats line — always include for visibility into "normal" activity
findings+=(":bar_chart: tmp=$tmp_count finance-intake=$finance_stuck nas-staging: ${staging_summary:-(empty)}")

# ── Build + send the Slack message ───────────────────────────────────────────

title="dispatcher 3-day health check"
[[ -n "$attention" ]] && title="[ATTENTION] $title"

body="*$title*"$'\n'
for f in "${findings[@]}"; do
  body+="• $f"$'\n'
done

BOT_TOKEN="$(security find-generic-password -s dispatcher-slack -a bot_token -w "$KEYCHAIN_PATH" 2>/dev/null || true)"

if [[ -n "$BOT_TOKEN" ]]; then
  TOKEN="$BOT_TOKEN" CHANNEL="$CHANNEL" BODY="$body" python3 - <<'PYEOF'
import json, os, sys, urllib.request
data = json.dumps({"channel": os.environ["CHANNEL"], "text": os.environ["BODY"]}).encode()
req = urllib.request.Request(
    "https://slack.com/api/chat.postMessage",
    data=data,
    headers={
        "Authorization": f"Bearer {os.environ['TOKEN']}",
        "Content-Type": "application/json; charset=utf-8",
    },
)
try:
    print(urllib.request.urlopen(req, timeout=10).read().decode())
except Exception as e:
    print(f"slack post failed: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
else
  echo "[3day-check] no bot token in keychain (service=dispatcher-slack, account=bot_token)" >&2
  echo "$body" >&2
fi

# ── Self-unload so this doesn't fire on April 27 next year ───────────────────

PLIST="$HOME/Library/LaunchAgents/com.home-tools.dispatcher-3day-check.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
exit 0
