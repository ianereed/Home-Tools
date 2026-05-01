# Home-Tools

Personal tooling for one user (Ian). Most of it runs on a headless Mac mini M4
home server (`homeserver`) sitting next to the router; a couple of pieces stay
on the laptop or live entirely in Google Sheets / Apps Script. All LLM
inference is local (Ollama on `127.0.0.1:11434`) — sensitive content never
leaves the house.

## Projects

| Project | What it does | Where it runs | README |
|---|---|---|---|
| [`event-aggregator`](event-aggregator/README.md) | Watches Gmail / Slack / iMessage / WhatsApp / Discord for events and writes them to Google Calendar; also extracts todos to Todoist | mini | 🟢 live |
| [`dispatcher`](dispatcher/README.md) | Slack Socket Mode router. Drop an image in `#ian-image-intake`, it classifies + routes locally (qwen2.5vl); interactive commands in `#ian-event-aggregator` | mini | 🟢 live |
| [`finance-monitor`](finance-monitor/README.md) | Local Q&A over YNAB + receipts via a Slack DM bot; read-only YNAB API sync | mini | 🟢 live |
| [`health-dashboard`](health-dashboard/README.md) | HRV / sleep / training-load Streamlit dashboard fed by Apple Health, Strava, Intervals, Garmin | mini | 🟢 live |
| [`nas-intake`](nas-intake/README.md) | Watches `~/Share1/**/[Ii]ntake/` on the mini, OCRs + classifies docs via the event-aggregator pipeline, files them under `<parent>/<year>/<doc-type>/...` | mini | 🟢 live |
| [`service-monitor`](service-monitor/README.md) | Streamlit dashboard at `homeserver:8502` showing all loaded LaunchAgents, queue depths, DB sizes, Ollama state, log tails | mini | 🟢 live |
| [`Mac-mini`](Mac-mini/README.md) | Setup + ops log + cross-cutting LaunchAgents (heartbeat, daily Slack digest, weekly SSH digest, memory/ollama trackers) | mini | 🟢 live |
| [`meal-planner`](meal-planner/README.md) | Google Sheet + Apps Script grocery / recipe automation, with a Python sidecar for Gemini-powered batch jobs | laptop / Apps Script | 🟢 live |
| [`medical-records`](medical-records/README.md) | Local-only PHI handling for an active recovery; writes appointments + medication tapers to GCal / Reminders | laptop only | 🟢 live |
| [`contacts`](contacts/README.md) | Toolbox of one-shot Python scripts maintaining `antora_contacts.xlsx` | laptop | 🟡 ad-hoc |
| [`colorado-trip`](colorado-trip/README.md) | One-shot Python scripts that built a Google Sheet itinerary | laptop | ⚪ archived |

For the live mini status (which agents are loaded, queue depths, Ollama health,
log tails) see the **service-monitor** dashboard at `http://homeserver:8502/`
over Tailscale, or `service-monitor/services.py:SERVICES` for the source-of-truth
agent registry.

## What's next

The agreed forward sequence (see `Mac-mini/PLAN.md` for detail):

1. **Phase 7 — NAS backup.** Restic + Time Machine to `~/Share1` (mini) for
   `health.db`, event-aggregator state, `finance.db`, login keychain, Phase 6
   incidents log. Off-site (B2/Wasabi) deferred.
2. **Pick 1 — Mini Jobs queue + console** at `homeserver:8503`. Lift the
   interactive surface out of Slack onto a GUI on the mini; Slack stays for
   mobile.
3. **Tier-2 orchestrator P0+P1.** Audit log skeleton + deterministic supervisor
   recipes. Strangler-fig path; CEO-approved 2026-04-30. See
   `future-architecture-upgrade.md` for the full design.

## External docs / memory

- **`Mac-mini/PLAN.md`** — the working plan; current status + next 1–2 phases
- **`Mac-mini/README.md`** — server state, key decisions, gotchas
- **`future-architecture-upgrade.md`** — Tier-2 orchestrator design (with Opus review pattern)
- **Memory files** at `~/.claude/projects/-Users-ianreed-Documents-GitHub-Home-Tools/memory/`
  carry the lessons that are too situational for any README (TCC quirks,
  keychain shim, qwen3 `think:false`, dedup invariants, etc.)
