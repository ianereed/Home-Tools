#!/usr/bin/env bash
# weekly-ssh-digest.sh — Mon 09:00 weekly SSH-failure digest.
#
# Greps the last 7 days of unified logs for sshd failures (failed/invalid login
# attempts), summarizes by source IP, and posts to Slack.
#
# Phase 6 — see Mac-mini/PHASE6.md.

set -uo pipefail

CHANNEL="${PHASE6_CHANNEL:-#ian-event-aggregator}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SLACK_POST="$SCRIPT_DIR/slack-post.sh"

# `log show --last 7d` can take 30-60s — that's fine, this runs offline at 09:00 Monday.
RAW=$(log show --predicate 'process == "sshd"' --last 7d 2>/dev/null \
      | grep -iE 'failed|invalid|authentication failure' \
      | head -5000)

if [[ -z "$RAW" ]]; then
  body=$(printf '*sshd weekly digest — %s*\n:white_check_mark: 0 failed/invalid sshd events in last 7d.' "$(date +%F)")
  bash "$SLACK_POST" "$CHANNEL" "$body"
  exit 0
fi

# Total + top-5 source-IP summary.
total=$(printf '%s\n' "$RAW" | wc -l | tr -d ' ')
top_ips=$(printf '%s\n' "$RAW" \
  | grep -oE 'from [0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}' \
  | sort | uniq -c | sort -rn | head -5)

# Build mrkdwn body.
body=$(printf '*sshd weekly digest — %s*\n' "$(date +%F)")
body+=$(printf ':warning: %s failed/invalid sshd event(s) in last 7d.\n' "$total")
if [[ -n "$top_ips" ]]; then
  body+=$'\nTop source IPs:\n'
  while IFS= read -r line; do
    body+="  • $line"$'\n'
  done <<< "$top_ips"
fi

bash "$SLACK_POST" "$CHANNEL" "$body"
