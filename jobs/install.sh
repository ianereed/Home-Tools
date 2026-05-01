#!/bin/bash
# Install / update the jobs LaunchAgents (consumer + http) on the mini.
# Idempotent: safe to re-run after `git pull`.
#
# Usage:
#   bash jobs/install.sh                    install consumer + http
#   bash jobs/install.sh migrate <kind>     begin migration of <kind>
#   bash jobs/install.sh migrate-all        cut over all 12 known migrations
#   bash jobs/install.sh cleanup-soaked     remove .disabled plists for promoted

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHAGENTS="$HOME/Library/LaunchAgents"
JOBS="$REPO/jobs"
LOGS="$REPO/logs"
RUN="$REPO/run"

mkdir -p "$LAUNCHAGENTS" "$LOGS" "$RUN"

# Make wrappers executable (idempotent).
chmod +x "$JOBS/run-consumer.sh" "$JOBS/run-http.sh"

ACTION="${1:-install}"

case "$ACTION" in
    install)
        # Ensure venv + huey present.
        if [ ! -d "$JOBS/.venv" ]; then
            echo "creating venv at $JOBS/.venv …"
            /opt/homebrew/bin/python3.12 -m venv "$JOBS/.venv"
        fi
        "$JOBS/.venv/bin/pip" install -q -r "$JOBS/requirements.txt"

        # Drop plists into ~/Library/LaunchAgents and load them.
        for plist in com.home-tools.jobs-consumer com.home-tools.jobs-http; do
            cp "$JOBS/config/$plist.plist" "$LAUNCHAGENTS/$plist.plist"
            launchctl unload "$LAUNCHAGENTS/$plist.plist" 2>/dev/null || true
            launchctl load "$LAUNCHAGENTS/$plist.plist"
            echo "loaded: $plist"
        done

        # Smoke test the consumer.
        sleep 2
        "$JOBS/.venv/bin/python" -m jobs.cli doctor || {
            echo "warn: doctor failed — check logs/jobs-consumer.err.log"
            exit 1
        }
        echo "OK: jobs framework installed and consumer answered doctor"
        ;;

    migrate)
        kind="${2:-}"
        if [ -z "$kind" ]; then
            echo "usage: bash jobs/install.sh migrate <kind>"
            exit 2
        fi
        "$JOBS/.venv/bin/python" -m jobs.cli migrate "$kind"
        ;;

    migrate-all)
        # Each migration is launchctl unload-old + record-baseline. The new
        # Job kind is already running in the consumer (committed in code).
        #
        # Order: simplest first; verifier watches each.
        for kind in heartbeat daily_digest weekly_ssh_digest dispatcher_3day_check \
                    finance_monitor_watch nas_intake_scan health_collect \
                    health_intervals_poll health_staleness \
                    restic_hourly restic_daily restic_prune ; do
            echo "--- migrating $kind ---"
            "$JOBS/.venv/bin/python" -m jobs.cli migrate "$kind" || {
                echo "warn: migration of $kind failed (already migrated? skipping)"
            }
        done
        ;;

    cleanup-soaked)
        "$JOBS/.venv/bin/python" -m jobs.cli cleanup-soaked
        ;;

    *)
        echo "unknown action: $ACTION"
        echo "usage: bash jobs/install.sh [install|migrate <kind>|migrate-all|cleanup-soaked]"
        exit 2
        ;;
esac
