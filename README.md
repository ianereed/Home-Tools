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
| [`Mac-mini`](Mac-mini/README.md) | Setup + ops log + cross-cutting LaunchAgents (memory/ollama trackers; the cron-style ones — heartbeat, digests, restic — are now Mini Jobs in `jobs/`) | mini | 🟢 live |
| [`jobs`](Mac-mini/PHASE12.md) | Phase 12 — typed-job framework (huey-backed). 12 cron-style LaunchAgents now run as `@huey.periodic_task` Job kinds in `jobs/kinds/`. HTTP enqueue at `homeserver:8504`. Operator runbook in `Mac-mini/PHASE12.md` | mini | 🟢 live |
| [`console`](Mac-mini/PHASE12.md) | Phase 12 — Mini Ops Streamlit at `homeserver:8503`. Tabs: Jobs / Decisions / Ask / Intake / Plan (Phase 13 placeholder) | mini | 🟢 live |
| [`gateway`](gateway/Caddyfile) | Caddy front door on `homeserver:8503`: console at `/`, health dashboard at `/health/`; old `:8501` links redirect there | mini | 🟢 live |
| [`meal-planner`](meal-planner/README.md) | Google Sheet + Apps Script grocery / recipe automation, with a Python sidecar for Gemini-powered batch jobs | laptop / Apps Script | 🟢 live |
| [`medical-records`](medical-records/README.md) | Local-only PHI handling for an active recovery; writes appointments + medication tapers to GCal / Reminders | laptop only | 🟢 live |
| [`contacts`](contacts/README.md) | Toolbox of one-shot Python scripts maintaining `antora_contacts.xlsx` | laptop | 🟡 ad-hoc |
| [`colorado-trip`](colorado-trip/README.md) | One-shot Python scripts that built a Google Sheet itinerary | laptop | ⚪ archived |

For the live mini status (which agents are loaded, queue depths, Ollama health,
log tails) see the **service-monitor** dashboard at `http://homeserver:8502/`
over Tailscale, or `service-monitor/services.py:SERVICES` for the source-of-truth
agent registry.

## What's next

**Just shipped:** Phase 12 v3 (2026-05-01) — Mini Jobs framework + Mini
Ops console. 12 of the mini's 21 cron-style LaunchAgents now run as
`@huey.periodic_task` Job kinds in `jobs/kinds/`. The `migration_verifier`
runs hourly with auto-rollback on baseline divergence + auto-promote at
72h soak. Console at `homeserver:8503` (Jobs / Decisions / Ask / Intake /
Plan placeholder); HTTP enqueue at `homeserver:8504`. Closes the OPS6
state.json flock invariant. Operator runbook:
[`Mac-mini/PHASE12.md`](Mac-mini/PHASE12.md).

The agreed forward sequence (see [`Mac-mini/PLAN.md`](Mac-mini/PLAN.md) for detail):

1. **Phase 12 cutover + 72h soak.** SSH-driven deploy of the framework on
   the mini, then `bash jobs/install.sh migrate-all` to cut over all 12.
   The verifier handles the rest unattended.
2. **Phase 12.5 — event-aggregator fetch+worker migration.** Held back from
   Phase 12 because the worker's queue + model-swap state machine doesn't
   decouple cleanly from the fetch loop. Migrate as a unit later.
3. **Phase 13 — Meal-planner overhaul: architecting (joint priority, Anny + Ian).**
   First sitting under the new "one Phase = one sitting" rule. Output is
   a locked design (not code), produced via the gstack review pipeline
   (`/office-hours` → `/plan-ceo-review` → `/plan-eng-review`). Targets
   real actions from iPhone via Apple Shortcuts → `:8504`, and meaningful
   weekly planning on the Windows laptop with Claude. Plan tab placeholder
   reserves the slot in the Mini Ops console. See the
   `project_meal_planner_expansion_priority.md` memory for the full ask.
4. **Phase 14+ — Meal-planner overhaul: build (chunks numbered as claimed).**
   Once Phase 13 produces a locked design, the build splits into one-sitting
   chunks. Each chunk gets the next sequential Phase number when it starts.

**Naming convention:** A Phase is confirmed scope, completable in one
sitting, sequentially numbered (no decimals — rule applies starting at
Phase 13; Phase 12.5 is grandfathered). Catalogued suggestions in the
brainstorm (`~/.claude/plans/come-up-with-more-encapsulated-spring.md`)
remain ideas until promoted to a Phase.

**Long-term future scope** (re-evaluate after the meal-planner work ships):
the Tier-2 LLM orchestrator design at `future-architecture-upgrade.md`.
Phase 12's `jobs.adapters` + `requires` + `baseline` machinery already
covers a lot of what an orchestrator needs; revisit whether a separate
orchestrator service is still warranted at that point.

## External docs / memory

- **`Mac-mini/PLAN.md`** — the working plan; current status + next 1–2 phases
- **`Mac-mini/README.md`** — server state, key decisions, gotchas
- **`future-architecture-upgrade.md`** — Tier-2 orchestrator design (with Opus review pattern)
- **Memory files** at `~/.claude/projects/-Users-ianreed-Documents-GitHub-Home-Tools/memory/`
  carry the lessons that are too situational for any README (TCC quirks,
  keychain shim, qwen3 `think:false`, dedup invariants, etc.)
