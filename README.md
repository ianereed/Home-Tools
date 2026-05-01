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

1. **Phase 7 — NAS backup (NAS-only).** Restic + Time Machine to `~/Share1`
   (the iananny SMB share already mounted on the mini) for `health.db`,
   event-aggregator state, `finance.db`, login keychain, Phase 6 incidents
   log. Off-site (B2/Wasabi) explicitly deferred — open to adding it later
   as a second leg, not now.
2. **Pick 1 — Mini Jobs queue + console** at `homeserver:8503`. Architectural
   foundation. Lifts the interactive surface out of Slack onto a GUI on the
   mini; Slack stays for mobile. Closes `state.json` file-lock race in the
   same PR.
3. **Meal-planner expansion (joint priority — Anny + Ian).** First feature
   work after the backend foundation lands. Targets: real actions from
   iPhone (Apple Shortcuts → mini); meaningful weekly meal planning
   collaboration on the Windows laptop with Claude. Architecture will be
   designed when the time comes via the gstack review skills
   (`/office-hours` → `/plan-ceo-review` → `/plan-eng-review`). See the
   `project_meal_planner_expansion_priority.md` memory for the full ask.

**Long-term future scope** (re-evaluate after the meal-planner work ships):
the Tier-2 LLM orchestrator design at `future-architecture-upgrade.md`. Pick
1's `Job` framework is likely to absorb much of its plumbing; revisit
whether a separate orchestrator service is still warranted at that point.

## External docs / memory

- **`Mac-mini/PLAN.md`** — the working plan; current status + next 1–2 phases
- **`Mac-mini/README.md`** — server state, key decisions, gotchas
- **`future-architecture-upgrade.md`** — Tier-2 orchestrator design (with Opus review pattern)
- **Memory files** at `~/.claude/projects/-Users-ianreed-Documents-GitHub-Home-Tools/memory/`
  carry the lessons that are too situational for any README (TCC quirks,
  keychain shim, qwen3 `think:false`, dedup invariants, etc.)
