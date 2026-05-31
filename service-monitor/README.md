# service-monitor

Mac mini service status dashboard. Shows live status of every loaded `com.home-tools.*` and `com.health-dashboard.*` agent, queue depths, DB health, Ollama availability, and per-service log tails. The agent registry is `services.py:SERVICES` (source of truth).

**URL**: `http://homeserver:8502/` (Tailscale)  
**Auto-refresh**: 30 seconds  
**Port**: 8502

## What it monitors

| Project | Services |
|---|---|
| event-aggregator | fetch (10 min), worker (KeepAlive) |
| finance-monitor | bot (KeepAlive), watcher (5 min) |
| health-dashboard | receiver, collect, intervals-poll, staleness, streamlit |
| nas-intake | watcher (5 min) |
| service-monitor | this dashboard (self) |
| phase6 (monitoring) | heartbeat (30 min), daily-digest (07:00), weekly-ssh-digest (Mon 09:00) |
| phase7 (NAS backup) | restic-hourly (:17), restic-daily (03:30), restic-prune (Sun 04:00) |

Also: Ollama :11434, health.db row counts, finance.db row counts, event-aggregator queue depths, RAM pressure (memory-tracker), Ollama load history (ollama-tracker), Phase 7 backup-lane freshness.

## Deploy (mini)

```bash
git pull
cd ~/Home-Tools/service-monitor
bash install.sh
```

## Add a new service

1. Add a row to `SERVICES` in `services.py`
2. Add a node to the relevant lane in `flowchart.py:render_dataflow`
