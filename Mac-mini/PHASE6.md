# Phase 6 — Minimal monitoring (descoped: daily Slack digest)

> **Status (2026-04-30):** Descoped from the original Pushover-based design.
> The original eng-review-locked plan in `Mac-mini/PLAN.md` Phase 6 is partially
> superseded — see "Descope record" below.

## What it is

Three new LaunchAgents that watch the existing 11+ services and post a daily
Slack message at 07:00 summarizing health and any overnight incidents. Zero
new accounts, zero new dependencies, zero modifications to the existing
LaunchAgents. Reuses the dispatcher Slack bot token that's already in the
login keychain.

```
                 com.home-tools.heartbeat (every 30 min)
                          |
                          v
                 heartbeat.py reads:
                   launchctl list | grep <agents>
                   curl :8095, :8501, :8502, :11434
                   stat health.db mtime
                          |
                          v
                 detects state changes vs prior run
                          |
                          v
                 ~/Home-Tools/logs/incidents.jsonl  (NDJSON)
                          |
                          v   (07:00 daily)
                 com.home-tools.daily-digest
                          |
                          v
                 daily-digest.py reads incidents (last 24h)
                 + queries primitives for "now"
                          |
                          v
                 slack-post.sh -> #ian-event-aggregator
                 (uses dispatcher-slack/bot_token from keychain)

                 com.home-tools.weekly-ssh-digest (Mon 09:00)
                          |
                          v
                 log show sshd --last 7d | grep failed
                          |
                          v
                 slack-post.sh -> #ian-event-aggregator
```

## Files

| Path | Purpose |
|---|---|
| `Mac-mini/scripts/slack-post.sh` | Shared helper. Reads `dispatcher-slack/bot_token`, POSTs `chat.postMessage`. Override token via `SLACK_BOT_TOKEN_OVERRIDE`. |
| `Mac-mini/scripts/heartbeat.py` | 30-min liveness check. Writes state-change events to `incidents.jsonl`. No Slack push. |
| `Mac-mini/scripts/daily-digest.py` | 07:00 digest. Supports `--dry-run`. |
| `Mac-mini/scripts/weekly-ssh-digest.sh` | Mon 09:00 sshd-failure digest. |
| `Mac-mini/LaunchAgents/com.home-tools.heartbeat.plist` | StartInterval=1800. |
| `Mac-mini/LaunchAgents/com.home-tools.daily-digest.plist` | StartCalendarInterval Hour=7 Minute=0. |
| `Mac-mini/LaunchAgents/com.home-tools.weekly-ssh-digest.plist` | StartCalendarInterval Weekday=1 (Mon) Hour=9 Minute=0. |
| `Mac-mini/install-phase6.sh` | Idempotent installer. |
| `Mac-mini/scripts/test-phase6.sh` | 8 deploy-verification gates. |

## Install (on the mini)

```bash
ssh homeserver@homeserver
cd ~/Home-Tools
git pull
bash Mac-mini/install-phase6.sh
```

The installer:
1. Validates source files
2. Warns if `pmset -g` shows non-zero sleep (mini may sleep through 07:00)
3. `chmod +x` the scripts
4. Creates `~/Home-Tools/run/`, `~/Home-Tools/logs/`, `~/Library/Logs/home-tools/`
5. Copies the 3 plists to `~/Library/LaunchAgents/`
6. `launchctl unload` then `load` each plist (idempotent)
7. Prints status + rollback commands

After install, run all 8 gates:

```bash
bash Mac-mini/scripts/test-phase6.sh --all
```

## State files

| Path | Owner | Purpose |
|---|---|---|
| `~/Home-Tools/run/heartbeat-state.json` | heartbeat.py | Last observed key→state map. Internal state-change detection. |
| `~/Home-Tools/logs/incidents.jsonl` | heartbeat.py (append) / daily-digest.py (read) | Append-only NDJSON log of state-change events. |
| `~/Home-Tools/run/digest-failed.flag` | slack-post.sh (write on failure) / cleared on success | Marker for Slack delivery failure. Service-monitor surfaces this. |
| `~/Library/Logs/home-tools/heartbeat.log` | launchd | stdout/stderr from heartbeat.py |
| `~/Library/Logs/home-tools/daily-digest.log` | launchd | stdout/stderr from daily-digest.py |
| `~/Library/Logs/home-tools/weekly-ssh-digest.log` | launchd | stdout/stderr from weekly-ssh-digest.sh |
| `~/Library/Logs/home-tools/phase6.log` | slack-post.sh | One line per Slack failure |

## Daily digest format

All-green case (most days):

```
*Mac mini daily digest — 2026-05-01*
:white_check_mark: All 11 services healthy.
0 incidents in last 24h.
DBs: health.db 18m, finance.db 4h, event-aggregator state 3m
```

Issues case:

```
*Mac mini daily digest — 2026-05-01* [ATTENTION]
:bar_chart: 2 state-change event(s) in last 24h:
  • 2026-05-01T03:14:22-07:00 com.home-tools.dispatcher: up → down
  • 2026-05-01T03:42:45-07:00 com.home-tools.dispatcher: down → up
:white_check_mark: Currently green.
DBs: health.db 18m, finance.db 4h, event-aggregator state 3m
```

## Test gates (8)

| # | Name | Destructive? |
|---|---|---|
| 1 | slack-post.sh smoke from SSH | posts to Slack |
| 2 | slack-post.sh from launchd context (one-shot test plist) | posts to Slack |
| 3 | heartbeat positive (all up → 0 new events) | no |
| 4 | heartbeat negative (unload+reload `com.home-tools.dispatcher`) | yes — temporarily unloads dispatcher |
| 5 | heartbeat stale-DB (back-date `health.db`, restore) | yes — temporarily back-dates |
| 6 | daily-digest dry-run (stdout only) | no |
| 7 | daily-digest live (posts to Slack) | posts to Slack |
| 8 | Slack failure path (garbage token, expect flag file + log line) | no |

Run `bash Mac-mini/scripts/test-phase6.sh --all` to run all 8 in order. Run
`bash Mac-mini/scripts/test-phase6.sh 4` to run a single gate.

Gates 1, 2, 7 will produce real Slack messages — expect 3-4 entries in
`#ian-event-aggregator` during a full test run.

## Troubleshooting

### "no bot token" in `phase6.log`

The dispatcher-slack keychain entry isn't accessible from the launchd context.

```bash
# Verify the entry exists:
security find-generic-password -s dispatcher-slack -a bot_token \
  ~/Library/Keychains/login.keychain-db

# Verify the keychain self-unlock works:
security unlock-keychain -p '' ~/Library/Keychains/login.keychain-db
```

If this fails: see memory `feedback_keychain_audit_session_unlock_scope.md` and
`project_mac_mini_keychain_shim.md`. The login keychain on the mini was created
with an empty password specifically so launchd-spawned shells can unlock it.

### Daily digest didn't fire at 07:00

Check `pmset -g | grep -i sleep`. If `sleep != 0`, the mini may have been
asleep at 07:00. macOS launchd fires a missed `StartCalendarInterval` ONCE
on wake, so a delayed digest is normal; multiple-day skips are not. To prevent:

```bash
sudo pmset -a sleep 0 disksleep 0
```

### incidents.jsonl growing too fast

Steady state should be ≤ a few lines/day (only state changes, not every check).
If you see hundreds of lines, something is genuinely flapping. `daily-digest.py`
reads the last 24h, so file size doesn't directly affect it, but you'll want
to investigate the actual flap.

Inspect:

```bash
tail -50 ~/Home-Tools/logs/incidents.jsonl | jq .
```

Rotation is not yet automated; revisit if the file ever exceeds 10MB.

### service-monitor showing red on a Phase 6 agent

`service-monitor/services.py` (Phase 5c) was extended to surface
`digest-failed.flag` and the 3 new agents. If `digest-failed.flag` exists, it
means slack-post.sh failed at least once since the last successful post.

```bash
cat ~/Home-Tools/run/digest-failed.flag
tail -20 ~/Library/Logs/home-tools/phase6.log
```

## Rollback

Phase 6 touches **no existing services**. Rollback is clean:

```bash
for label in com.home-tools.heartbeat com.home-tools.daily-digest com.home-tools.weekly-ssh-digest; do
  launchctl unload ~/Library/LaunchAgents/${label}.plist
  rm ~/Library/LaunchAgents/${label}.plist
done
```

State and log files (`~/Home-Tools/run/`, `~/Home-Tools/logs/incidents.jsonl`,
`~/Library/Logs/home-tools/*.log`) can be left in place — they're write-only
and don't impact anything else.

## Descope record

The original Phase 6 in `Mac-mini/PLAN.md` was eng-reviewed earlier with a
Pushover-based push design. User declined to pay for Pushover and chose
"daily Slack digest only, no urgent pings" as the alert channel.

Removed from original Phase 6:
- `run-agent.sh` LaunchAgent wrapper for the 6+ existing agents
- Per-agent lockfile + 1h time-decay suppression
- Phased rollout pilot of run-agent.sh
- Modifications to existing LaunchAgent plists
- Pushover account / tokens / keychain entries

What carries forward:
- Heartbeat checks (now write incidents.jsonl instead of pushing)
- Weekly SSH-failure digest (now Slack instead of Pushover)
- Notification audit log (now `phase6.log` + `digest-failed.flag`)
- Port-audit reminder (manual calendar event, not automated)

Trade-off: you find out about overnight failures at 07:00 instead of
immediately. Acceptable for a hobby home server.

## Future evolution

If "daily digest is too slow when something breaks badly," the upgrade path is
to add a per-failure Slack DM in heartbeat.py (gated on a "severity" classifier
that fires only on multi-agent or extended outages). This avoids alert-storm
without bringing back the full Pushover-style wrapper. Defer until you've
collected real `incidents.jsonl` data for ≥ 30 days.
