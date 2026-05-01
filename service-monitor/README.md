# service-monitor

Mac mini service status dashboard. Shows live status of every loaded `com.home-tools.*` and `com.health-dashboard.*` agent, queue depths, DB health, Ollama availability, and per-service log tails. The agent registry is `services.py:SERVICES` (source of truth).

**URL**: `http://homeserver:8502/` (Tailscale)  
**Auto-refresh**: 30 seconds  
**Port**: 8502

## What it monitors

| Project | Services |
|---|---|
| event-aggregator | fetch (10 min), worker (KeepAlive) |
| dispatcher | bot (KeepAlive) |
| finance-monitor | bot (KeepAlive), watcher (5 min) |
| health-dashboard | receiver, collect, intervals-poll, staleness, streamlit |
| service-monitor | this dashboard (self) |

Also: Ollama :11434, health.db row counts, finance.db row counts, event-aggregator queue depths.

## Deploy (mini)

```bash
git pull
cd ~/Home-Tools/service-monitor
bash install.sh
```

## Add a new service

1. Add a row to `SERVICES` in `services.py`
2. Add a node to the relevant lane in `flowchart.py:render_dataflow`
