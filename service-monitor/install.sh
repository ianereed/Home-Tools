#!/usr/bin/env bash
# Install the service-monitor LaunchAgent on the mini.
# Run from the mini after: git pull
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$HERE/config/com.home-tools.service-monitor.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.home-tools.service-monitor.plist"

echo "== service-monitor install =="
echo "  project: $HERE"

if [[ ! -d "$HERE/.venv" ]]; then
  echo "  creating venv (.venv) with Python 3.12"
  cd "$HERE"
  uv venv --python 3.12 || python3.12 -m venv .venv
fi

echo "  installing requirements"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$HERE/.venv/bin/python" -q -r "$HERE/requirements.txt"
else
  "$HERE/.venv/bin/pip" install -q -r "$HERE/requirements.txt"
fi

mkdir -p "$HOME/Library/Logs/home-tools"

echo "  copying plist to $PLIST_DST"
mkdir -p "$(dirname "$PLIST_DST")"
cp "$PLIST_SRC" "$PLIST_DST"

if launchctl list 2>/dev/null | grep -q com.home-tools.service-monitor; then
  echo "  unloading previous LaunchAgent"
  launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

echo "  loading LaunchAgent"
launchctl load "$PLIST_DST"

sleep 3
echo
echo "  status:"
launchctl list 2>/dev/null | grep com.home-tools.service-monitor || echo "    (not listed yet)"

echo
echo "Done. Verify with:"
echo "  curl -s --max-time 10 http://127.0.0.1:8502/_stcore/health"
echo "  open http://homeserver:8502/"

# ── Ollama load-history tracker (Tier 2) ─────────────────────────────
TRACKER_PLIST_SRC="$HERE/../Mac-mini/LaunchAgents/com.home-tools.ollama-tracker.plist"
TRACKER_PLIST_DST="$HOME/Library/LaunchAgents/com.home-tools.ollama-tracker.plist"

if [[ -f "$TRACKER_PLIST_SRC" ]]; then
  echo
  echo "== ollama-tracker install =="
  echo "  copying plist to $TRACKER_PLIST_DST"
  cp "$TRACKER_PLIST_SRC" "$TRACKER_PLIST_DST"

  if launchctl list 2>/dev/null | grep -q com.home-tools.ollama-tracker; then
    echo "  unloading previous LaunchAgent"
    launchctl unload "$TRACKER_PLIST_DST" 2>/dev/null || true
  fi

  echo "  loading LaunchAgent"
  launchctl load "$TRACKER_PLIST_DST"

  sleep 2
  echo "  status:"
  launchctl list 2>/dev/null | grep com.home-tools.ollama-tracker || \
    echo "    (not listed yet)"
fi

# ── Memory/RAM tracker ───────────────────────────────────────────────
MEM_PLIST_SRC="$HERE/../Mac-mini/LaunchAgents/com.home-tools.memory-tracker.plist"
MEM_PLIST_DST="$HOME/Library/LaunchAgents/com.home-tools.memory-tracker.plist"

if [[ -f "$MEM_PLIST_SRC" ]]; then
  echo
  echo "== memory-tracker install =="
  echo "  copying plist to $MEM_PLIST_DST"
  cp "$MEM_PLIST_SRC" "$MEM_PLIST_DST"

  if launchctl list 2>/dev/null | grep -q com.home-tools.memory-tracker; then
    echo "  unloading previous LaunchAgent"
    launchctl unload "$MEM_PLIST_DST" 2>/dev/null || true
  fi

  echo "  loading LaunchAgent"
  launchctl load "$MEM_PLIST_DST"

  sleep 2
  echo "  status:"
  launchctl list 2>/dev/null | grep com.home-tools.memory-tracker || \
    echo "    (not listed yet)"
fi
