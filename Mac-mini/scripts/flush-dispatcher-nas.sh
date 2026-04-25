#!/usr/bin/env bash
# Flush ~/Home-Tools/dispatcher/nas-staging/ to /Volumes/Share1, preserving
# the category/year directory structure created by dispatcher/router.py.
#
# Idempotent. Bails quietly if the NAS isn't mounted (lets the agent keep
# firing on schedule without errors).
#
# Why a separate flusher (vs reusing event-aggregator's flush_pending_staged):
# dispatcher's nas-staging tree has no `_metadata.json` files; files are
# dropped directly into category subdirs. A simple rsync-and-prune is enough.

set -uo pipefail

STAGING="$HOME/Home-Tools/dispatcher/nas-staging"
TARGET="/Volumes/Share1"
LOG_PREFIX="$(date -u +%Y-%m-%dT%H:%M:%SZ) flush-dispatcher-nas:"

if ! mount | grep -q "on $TARGET "; then
  echo "$LOG_PREFIX NAS not mounted at $TARGET — idle"
  exit 0
fi

if [[ ! -d "$STAGING" ]]; then
  echo "$LOG_PREFIX no staging dir — nothing to flush"
  exit 0
fi

# Count what's about to move (cheap; a few hundred files at most).
n_files="$(find "$STAGING" -type f ! -name '.gitkeep' 2>/dev/null | wc -l | tr -d ' ')"
if (( n_files == 0 )); then
  echo "$LOG_PREFIX staging empty"
  exit 0
fi

echo "$LOG_PREFIX flushing $n_files file(s) → $TARGET"

# rsync -a preserves dirs/perms/times; --remove-source-files deletes source
# files only after successful transfer (NOT directories).
rsync -a --remove-source-files --exclude='.gitkeep' "$STAGING/" "$TARGET/" 2>&1
rc=$?

# Prune empty dirs left behind by --remove-source-files (it never deletes dirs).
# Keep the top-level $STAGING so the next dispatcher run has a place to land.
find "$STAGING" -mindepth 1 -type d -empty -delete 2>/dev/null || true

if (( rc == 0 )); then
  echo "$LOG_PREFIX done"
else
  echo "$LOG_PREFIX rsync exited $rc — some files may be left in staging" >&2
fi
exit "$rc"
