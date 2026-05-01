# Phase 5c — Service monitor dashboard (DONE 2026-04-27)

**URL**: `http://homeserver:8502/` (Tailscale)
**LaunchAgent**: `com.home-tools.service-monitor` (KeepAlive, port 8502)
**Source**: `~/Home-Tools/service-monitor/`

Streamlit page (auto-refresh 30 s) with:
- HTML/emoji swim-lane visual showing data flow per project with live 🟢/🟡/🔴 status on each service node
- Service table for every loaded LaunchAgent (PID, last exit code, schedule). The agent registry — single source of truth — is `service-monitor/services.py:SERVICES`.
- Queue depths from event-aggregator state.json; health.db + finance.db row counts + mtime
- Ollama model list + response latency
- Per-service log tails (last 20 lines each)

To deploy an update: `ssh homeserver@homeserver 'cd ~/Home-Tools && git pull && cd service-monitor && bash install.sh'`

To add a new service: edit `services.py:SERVICES` (one line) + `flowchart.py:render_dataflow` (one node).
