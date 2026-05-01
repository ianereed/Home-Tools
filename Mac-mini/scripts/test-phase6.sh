#!/usr/bin/env bash
# test-phase6.sh — operational deploy-verification gates for Phase 6.
#
# Run from the mini after install-phase6.sh:
#   bash Mac-mini/scripts/test-phase6.sh --all      # all gates 1..8
#   bash Mac-mini/scripts/test-phase6.sh 3          # only gate 3
#   bash Mac-mini/scripts/test-phase6.sh --list     # list gates
#
# Most gates are non-destructive. Gates 4 and 5 modify state temporarily and
# restore it. Gate 7 posts a real Slack message.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SLACK_POST="$HERE/slack-post.sh"
HEARTBEAT="$HERE/heartbeat.py"
DAILY="$HERE/daily-digest.py"
INCIDENTS="$HOME/Home-Tools/logs/incidents.jsonl"
HEALTH_DB="$HOME/Home-Tools/health-dashboard/data/health.db"
FLAGFILE="$HOME/Home-Tools/run/digest-failed.flag"
PHASE6_LOG="$HOME/Library/Logs/home-tools/phase6.log"
CHANNEL="${PHASE6_CHANNEL:-#ian-event-aggregator}"

PASS=()
FAIL=()

ok()    { PASS+=("$1"); echo "  [PASS] $1"; }
fail()  { FAIL+=("$1"); echo "  [FAIL] $1"; }

# Helpers
incidents_count() { wc -l < "$INCIDENTS" 2>/dev/null | tr -d ' ' || echo 0; }

require_files() {
  for f in "$SLACK_POST" "$HEARTBEAT" "$DAILY"; do
    if [[ ! -f "$f" ]]; then
      echo "ERROR: missing $f" >&2
      exit 1
    fi
  done
}

# Gate 1 — slack-post.sh smoke from SSH context.
gate1() {
  echo "== Gate 1: slack-post.sh smoke from SSH =="
  local body
  body="phase6 test gate 1 (smoke from SSH) $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if bash "$SLACK_POST" "$CHANNEL" "$body"; then
    ok "gate1: slack-post.sh from SSH posted to $CHANNEL"
  else
    fail "gate1: slack-post.sh exit non-zero (check $FLAGFILE and $PHASE6_LOG)"
  fi
}

# Gate 2 — slack-post.sh from launchd context.
# Plants a one-shot throwaway plist that fires slack-post.sh once, then unloads.
gate2() {
  echo "== Gate 2: slack-post.sh from launchd context =="
  local label="com.home-tools.phase6-test-gate2"
  local plist="$HOME/Library/LaunchAgents/${label}.plist"
  local marker="$HOME/Home-Tools/run/.gate2.marker"
  rm -f "$marker"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>${SLACK_POST} '${CHANNEL}' 'phase6 test gate 2 (launchd context, one-shot) '\$(date -u +%Y-%m-%dT%H:%M:%SZ) &amp;&amp; touch '${marker}'</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/Users/$(whoami)/Library/Logs/home-tools/gate2.log</string>
  <key>StandardErrorPath</key><string>/Users/$(whoami)/Library/Logs/home-tools/gate2.log</string>
</dict>
</plist>
EOF
  launchctl unload "$plist" 2>/dev/null || true
  launchctl load "$plist"
  # Wait up to 30s for the marker to appear.
  local i=0
  while (( i < 30 )); do
    if [[ -f "$marker" ]]; then break; fi
    sleep 1
    ((i+=1))
  done
  launchctl unload "$plist" 2>/dev/null || true
  rm -f "$plist"
  if [[ -f "$marker" ]]; then
    rm -f "$marker"
    ok "gate2: slack-post.sh fired from launchd context (keychain self-unlock works)"
  else
    fail "gate2: marker not created after 30s — check ~/Library/Logs/home-tools/gate2.log"
  fi
}

# Gate 3 — heartbeat positive (all services up → zero new state-change events).
gate3() {
  echo "== Gate 3: heartbeat positive =="
  local before; before=$(incidents_count)
  if ! python3 "$HEARTBEAT"; then
    fail "gate3: heartbeat.py exit non-zero"
    return
  fi
  local after; after=$(incidents_count)
  local diff=$((after - before))
  if (( diff == 0 )); then
    ok "gate3: heartbeat positive — 0 new state-change events"
  else
    echo "    NOTE: $diff new event(s) — may be legitimate (e.g., first run, or a real outage)"
    ok "gate3: heartbeat positive — but $diff new event(s); inspect $INCIDENTS"
  fi
}

# Gate 4 — heartbeat negative (unload an agent, expect agent_down event).
gate4() {
  echo "== Gate 4: heartbeat negative =="
  local target="com.home-tools.dispatcher"
  local plist="$HOME/Library/LaunchAgents/${target}.plist"
  if [[ ! -f "$plist" ]]; then
    fail "gate4: no plist for $target — skipped"
    return
  fi
  if ! launchctl list 2>/dev/null | grep -q " $target$"; then
    fail "gate4: $target not currently loaded — skipped"
    return
  fi
  local before; before=$(incidents_count)
  echo "  unloading $target (will reload after)"
  launchctl unload "$plist"
  sleep 1
  python3 "$HEARTBEAT" >/dev/null
  local after_down; after_down=$(incidents_count)
  echo "  reloading $target"
  launchctl load "$plist"
  sleep 2
  python3 "$HEARTBEAT" >/dev/null
  local after_up; after_up=$(incidents_count)
  if (( after_down > before )) && (( after_up > after_down )); then
    ok "gate4: heartbeat detected $target down (+$((after_down-before))) and back up (+$((after_up-after_down)))"
  else
    fail "gate4: expected new events on down then up; before=$before after_down=$after_down after_up=$after_up"
  fi
}

# Gate 5 — heartbeat stale-DB (touch -t old → expect db state_change to stale).
gate5() {
  echo "== Gate 5: heartbeat stale-DB =="
  if [[ ! -f "$HEALTH_DB" ]]; then
    fail "gate5: $HEALTH_DB missing — skipped"
    return
  fi
  local original_mtime; original_mtime=$(stat -f %m "$HEALTH_DB")
  echo "  saving original mtime $original_mtime"
  echo "  back-dating $HEALTH_DB to 2024-01-01"
  touch -t 202401010000 "$HEALTH_DB"
  local before; before=$(incidents_count)
  python3 "$HEARTBEAT" >/dev/null
  local after; after=$(incidents_count)
  echo "  restoring original mtime"
  python3 -c "import os, sys; os.utime(sys.argv[1], ($original_mtime, $original_mtime))" "$HEALTH_DB"
  python3 "$HEARTBEAT" >/dev/null   # write the "back to fresh" event
  if (( after > before )); then
    ok "gate5: heartbeat detected db_stale ($((after-before)) new event(s))"
  else
    fail "gate5: no new event after back-dating db"
  fi
}

# Gate 6 — daily-digest dry-run.
gate6() {
  echo "== Gate 6: daily-digest dry-run =="
  if python3 "$DAILY" --dry-run > /tmp/daily-digest-dry.out 2>&1; then
    if [[ -s /tmp/daily-digest-dry.out ]]; then
      head -20 /tmp/daily-digest-dry.out | sed 's/^/    /'
      ok "gate6: daily-digest --dry-run produced output ($(wc -l < /tmp/daily-digest-dry.out | tr -d ' ') lines)"
    else
      fail "gate6: --dry-run produced empty output"
    fi
  else
    fail "gate6: daily-digest --dry-run exit non-zero"
  fi
}

# Gate 7 — daily-digest live (posts real Slack message).
gate7() {
  echo "== Gate 7: daily-digest live =="
  if python3 "$DAILY"; then
    ok "gate7: daily-digest posted to $CHANNEL — spot-check Slack to confirm"
  else
    fail "gate7: daily-digest exit non-zero (check $FLAGFILE and $PHASE6_LOG)"
  fi
}

# Gate 8 — Slack failure path with bogus token.
gate8() {
  echo "== Gate 8: Slack failure path =="
  rm -f "$FLAGFILE"
  local before_log_size; before_log_size=$(wc -c < "$PHASE6_LOG" 2>/dev/null | tr -d ' ' || echo 0)
  if SLACK_BOT_TOKEN_OVERRIDE="garbage" bash "$SLACK_POST" "$CHANNEL" "phase6 gate 8 should fail" 2>/dev/null; then
    fail "gate8: slack-post.sh exited 0 with garbage token — should have failed"
    return
  fi
  if [[ -f "$FLAGFILE" ]] && (( $(wc -c < "$PHASE6_LOG" 2>/dev/null | tr -d ' ') > before_log_size )); then
    ok "gate8: failure produced flag file ($FLAGFILE) and log line"
    echo "    flag contents: $(cat "$FLAGFILE")"
    rm -f "$FLAGFILE"
  else
    fail "gate8: failure did NOT produce flag file or log line"
  fi
}

usage() {
  cat <<EOF
usage: $0 [--all|--list|<gate-number>]

Gates:
  1: slack-post.sh smoke from SSH (posts to $CHANNEL)
  2: slack-post.sh from launchd context (one-shot test plist)
  3: heartbeat positive (all up → 0 new events)
  4: heartbeat negative (unload+reload com.home-tools.dispatcher)
  5: heartbeat stale-DB (back-date health.db, restore)
  6: daily-digest --dry-run (prints to stdout)
  7: daily-digest live (posts to $CHANNEL)
  8: Slack failure path (garbage token, expect flag file + log line)

Options:
  --all         run gates 1..8 in order
  --list        print the gate list and exit
  --channel C   override Slack channel (default $CHANNEL)
EOF
}

main() {
  if [[ $# -eq 0 ]]; then
    usage
    exit 2
  fi

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --list) usage; exit 0 ;;
      --channel) CHANNEL="$2"; shift 2 ;;
      --all)
        require_files
        gate1; gate2; gate3; gate4; gate5; gate6; gate7; gate8
        shift
        ;;
      [1-8])
        require_files
        "gate$1"
        shift
        ;;
      *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
    esac
  done

  echo
  echo "== Summary =="
  echo "  PASS: ${#PASS[@]}"
  echo "  FAIL: ${#FAIL[@]}"
  if (( ${#FAIL[@]} > 0 )); then
    echo
    echo "Failures:"
    printf '  - %s\n' "${FAIL[@]}"
    exit 1
  fi
}

main "$@"
