#!/usr/bin/env bash
# install-phase6.sh — install Phase 6 monitoring (heartbeat + daily-digest +
# weekly-ssh-digest) on the Mac mini.
#
# Idempotent: re-running unloads + re-loads agents and re-copies plists.
# Touches no existing services. Rollback via uninstall-phase6.sh (printed at end).
#
# Run from the mini after `git pull`:
#   bash Mac-mini/install-phase6.sh
#
# Mirrors the install pattern from service-monitor/install.sh.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$HERE/scripts"
PLISTS_DIR="$HERE/LaunchAgents"
LA_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/home-tools"
RUN_DIR="$HOME/Home-Tools/run"
LOGS_DIR="$HOME/Home-Tools/logs"

PLISTS=(
  com.home-tools.heartbeat
  com.home-tools.daily-digest
  com.home-tools.weekly-ssh-digest
)

echo "== Phase 6 install =="
echo "  source: $HERE"

# 1. Sanity checks.
if [[ ! -d "$SCRIPTS_DIR" || ! -d "$PLISTS_DIR" ]]; then
  echo "ERROR: missing $SCRIPTS_DIR or $PLISTS_DIR" >&2
  exit 1
fi

for f in slack-post.sh heartbeat.py daily-digest.py weekly-ssh-digest.sh; do
  if [[ ! -f "$SCRIPTS_DIR/$f" ]]; then
    echo "ERROR: missing $SCRIPTS_DIR/$f" >&2
    exit 1
  fi
done

# 2. pmset sleep posture check (Plan agent gap fix — mini must not sleep at 07:00).
sleep_setting="$(pmset -g 2>/dev/null | awk '/^[[:space:]]*sleep[[:space:]]/ {print $2; exit}')"
if [[ -z "$sleep_setting" ]]; then
  echo "  [pmset] could not read sleep setting; skipping check"
elif [[ "$sleep_setting" == "0" ]]; then
  echo "  [pmset] sleep=0 (mini stays awake) — daily-digest will fire reliably at 07:00"
else
  echo "  [pmset] WARNING: sleep=$sleep_setting (mini may sleep)."
  echo "  [pmset] If the mini is asleep at 07:00, the daily-digest may be delayed or skipped."
  echo "  [pmset] Recommended: sudo pmset -a sleep 0 disksleep 0  # for headless server"
fi

# 3. Make scripts executable.
chmod +x "$SCRIPTS_DIR/slack-post.sh"
chmod +x "$SCRIPTS_DIR/weekly-ssh-digest.sh"
chmod +x "$SCRIPTS_DIR/heartbeat.py"
chmod +x "$SCRIPTS_DIR/daily-digest.py"

# 4. Create state + log dirs.
mkdir -p "$LA_DIR" "$LOG_DIR" "$RUN_DIR" "$LOGS_DIR"

# 5. Install / reinstall each plist.
for label in "${PLISTS[@]}"; do
  src="$PLISTS_DIR/$label.plist"
  dst="$LA_DIR/$label.plist"

  if [[ ! -f "$src" ]]; then
    echo "ERROR: missing $src" >&2
    exit 1
  fi

  if launchctl list 2>/dev/null | grep -q " $label$"; then
    echo "  [$label] unloading previous"
    launchctl unload "$dst" 2>/dev/null || true
  fi

  echo "  [$label] installing $src -> $dst"
  cp "$src" "$dst"

  echo "  [$label] loading"
  launchctl load "$dst"
done

# 6. Verify.
sleep 2
echo
echo "== Status =="
for label in "${PLISTS[@]}"; do
  line=$(launchctl list 2>/dev/null | grep " $label$" || true)
  if [[ -n "$line" ]]; then
    echo "  $line"
  else
    echo "  $label  NOT LISTED"
  fi
done

echo
echo "== Done =="
echo "Verify with:"
echo "  ls -la $LA_DIR/com.home-tools.{heartbeat,daily-digest,weekly-ssh-digest}.plist"
echo "  tail -f $LOG_DIR/heartbeat.log"
echo
echo "Smoke-test now (does NOT send Slack):"
echo "  python3 $SCRIPTS_DIR/heartbeat.py"
echo "  python3 $SCRIPTS_DIR/daily-digest.py --dry-run"
echo
echo "Run all 8 deploy-verification gates:"
echo "  bash $SCRIPTS_DIR/test-phase6.sh --all"
echo
echo "Rollback:"
for label in "${PLISTS[@]}"; do
  echo "  launchctl unload $LA_DIR/$label.plist && rm $LA_DIR/$label.plist"
done
