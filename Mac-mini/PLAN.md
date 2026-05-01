# Mac mini Home Server — Working Plan

Living plan for the ongoing build. Update as phases advance. Pair this with
`Mac-mini/README.md` (state-of-the-project), `Mac-mini/PHASE6.md` (Phase 6
operator runbook), `Mac-mini/history/` (completed-phase porting recipes), and
the `~/.claude/projects/.../memory/` entries (the accumulated lessons).

---

## Quick status (as of 2026-05-01)

Phase 6 monitoring layer LIVE on the mini (commit `a13c41a`, 2026-04-30):
heartbeat (every 30 min) → `incidents.jsonl` → daily Slack digest at 07:00
to `#ian-event-aggregator` + weekly SSH-failure digest. Three new LaunchAgents
(`com.home-tools.heartbeat`, `daily-digest`, `weekly-ssh-digest`). All 8
deploy gates passed; soaking. No new accounts, no Pushover, no modifications
to existing service plists. Operator runbook at `Mac-mini/PHASE6.md`.

Everything Phase 5 and earlier is done; recipes preserved in
`Mac-mini/history/` (5b health-dashboard, 5c service-monitor, 5d NAS mount,
5e nas-intake v1).

The live agent registry is `service-monitor/services.py:SERVICES`. The
service-monitor Streamlit dashboard at `http://homeserver:8502/` shows live
status (PID, last exit, schedule, queue depths, DB sizes, Ollama state, log
tails) for every loaded `com.home-tools.*` and `com.health-dashboard.*` agent.

---

## Resume from here

**Next single action**: Phase 7 — NAS backup. Detailed steps below.

Pre-flight (confirm health before starting new work):

```bash
ssh homeserver@homeserver '
  tailscale status | head -3
  launchctl list | grep -E "com\.(home-tools|health-dashboard)" | head
  curl -sf http://127.0.0.1:11434/api/tags | head -c 80; echo
  ls ~/Home-Tools/run/digest-failed.flag 2>&1 | head -1
  tail -3 ~/Home-Tools/logs/incidents.jsonl
'
```

Expected: tailscale connected, agents registered, Ollama responds,
`digest-failed.flag` missing, incidents.jsonl quiet.

---

## Phase 6 — Minimal monitoring (DONE 2026-04-30)

Daily Slack digest replaces the original Pushover design (user declined to pay
for Pushover; trade-off accepted: failures surface at 07:00 next morning
instead of immediately).

Three new agents — heartbeat (30-min liveness check), daily-digest (07:00),
weekly-ssh-digest (Mon 09:00). Helper scripts in `Mac-mini/scripts/`. Phase 6
touches no existing services; rollback is clean (unload + rm three plists).

Full operator runbook, file inventory, format examples, test gates,
troubleshooting, and rollback at **`Mac-mini/PHASE6.md`**.

---

## Phase 7 — Backup (NEXT — NAS-only locked 2026-05-01)

Goal: 3-2-1 backup so we can recover from disk failure or ransomware. Now
that `health.db` is the authoritative copy (laptop's DB is frozen at the
2026-04-22 cutover), losing it means re-scraping from Intervals + Strava
APIs, which only cover recent data. Protect it.

### What actually matters to protect (priority order)

1. `~/Home-Tools/health-dashboard/data/health.db` (~91MB, authoritative)
2. `~/Home-Tools/event-aggregator/state.json` + `event_log.jsonl`
3. `~/Home-Tools/finance-monitor/db/finance.db`
4. `~/Home-Tools/nas-intake/state.json`
5. `~/Library/Keychains/login.keychain-db` (7+ secrets, painful to re-migrate)
6. `~/Home-Tools/logs/incidents.jsonl` (Phase 6 audit trail)

### Decision: NAS-only for v1 (locked 2026-05-01)

Backup target is the iananny NAS (192.168.4.39) Share1 already mounted at
`~/Share1`. Not an external SSD. Not B2/Wasabi. Rationale:

- It already exists, already credentialed, already mounted.
- 3-2-1 isn't fully met with NAS-only (still on the same LAN as the mini),
  but it's a strong first leg — protects against mini SSD failure, OS
  reinstall, accidental `rm -rf`. **Open to off-site (B2/restic) as a second
  leg in the future**, not in scope for this Phase.
- Phase 5d already proved NAS reachability + TCC + autofs-style remount
  patterns work; Phase 7 doesn't have to re-solve that.

### Scope (v1)

1. **Time Machine to NAS (SMB target)** — system-native, encrypted, hourly.
2. **`restic` to NAS** for the priority-list files at higher cadence — hourly
   for `health.db`, daily for the rest. Repo password in keychain
   (`restic-backup`/`password`).
3. **Test a restore.** Pick one file, restore it to a scratch dir, diff.
   Untested backups aren't backups.
4. **Exclude:** `.venv/` directories, `__pycache__/`, `.git/`,
   `~/.ollama/models/**` (re-pullable).

Off-site leg (B2/Wasabi/restic) is consciously deferred — revisit when
something concrete makes it feel necessary, not as a pre-built option.

---

## Phase 8 — Finance automation (Phases 1 + 2 LIVE)

Work at `~/Home-Tools/finance-monitor/`. Two LaunchAgents on the mini:
KeepAlive Slack DM bot (`com.home-tools.finance-monitor`) + 5-min interval
watcher (`com.home-tools.finance-monitor-watcher`, runs read-only YNAB API
sync at the top of each cycle, then file intake).

- Phase 1 (DONE 2026-04-23): Slack DM Q&A over a local SQLite mirror; PDF
  + image OCR ingestion; query engine via qwen3:14b. DM allowlist locked,
  60s/user rate limit, sender ID audit-logged.
- Phase 2 (DONE 2026-04-24): read-only YNAB API delta sync via `YnabClient.get()`
  (the *only* HTTP method on the client — never add write methods).
  `budget_months` + `sync_state` tables. Cutoff `YNAB_API_CUTOFF=2026-04-24`.

**Phase 3+** (deferred): Amazon order reconciliation via Gmail; daily/weekly
spending digests; anomaly detection.

**Security:** YNAB PAT in `.env` (PAT has full read+write at YNAB's level;
read-only is enforced **client-side**). No LangChain (active critical CVEs).
All data local. Slack bot DM-only.

Comprehensive runbook at `finance-monitor/TROUBLESHOOTING.md`.

---

## Phase 9 — Slack UX split (dispatcher LIVE)

`Home-Tools/dispatcher/` is live on the mini. Long-running Socket Mode bot
listens in `#ian-event-aggregator` (interactive commands) and
`#ian-image-intake` (file uploads). Routes images locally via qwen2.5vl,
drops financial docs into `finance-monitor/intake/`, invokes
`event-aggregator main.py ingest-image` for event-type files. All intake
local-only — cloud fallback was removed; PDF rasterization via `pypdfium2`.

Tier-2 commands (mute/watch, force scan, undo last, changes since) shipped
2026-04-24; ACKs ephemeral 2026-04-27. Health check at
`Mac-mini/scripts/dispatcher-3day-check.sh` (see memory
`reference_dispatcher_health_check.md`).

---

## Phase 12 — Pick 1: Mini Jobs queue + console (next major work after Phase 7)

Architectural foundation — typed `Job` queue, single long-running worker,
new Streamlit GUI at `homeserver:8503` with Decisions / Ask / Intake /
Settings / Jobs tabs. Closes the `state.json` file-lock race in the same
PR. Every future feature becomes a `Job` subclass + a registry line, not a
new LaunchAgent + plist + log + monitoring entry.

Sketch and rationale in
`~/.claude/plans/come-up-with-more-encapsulated-spring.md` §5 Pick 1.
Implementation plan to be authored when Phase 7 lands; will use the gstack
`/plan-eng-review` skill.

## Phase 13 — Meal-planner expansion (joint priority — first feature after backend)

**Decided 2026-05-01.** Anny + Ian agreed this is the most valuable next
feature. Two stated capabilities:

- **Real iPhone actions** — tap a tile, get a result. Adds to weekly meal
  plan, captures a recipe photo, queries the pantry, etc. Likely uses the
  Apple Shortcuts → mini HTTP endpoint pattern (Pick 7's groundwork).
- **Windows-laptop weekly planning collaboration with Claude** — sit down,
  talk through the week's meals with Claude in the loop, end up with a
  populated Sheet + grocery list + Todoist. Not a static UI; a real
  conversation surface.

Architecture is **not yet designed.** When this Phase starts, run the full
gstack review pipeline (`/office-hours` → `/plan-ceo-review` →
`/plan-eng-review`) to lock the design before any code. Existing scaffolding
to lean on: `meal-planner/` (Apps Script frontend + Gemini batch sidecar),
the model-swap pattern from `event-aggregator/worker.py`, and Pick 1's Job
framework.

This Phase subsumes Pick 5 (replace meal-planner Gemini with local mini)
and partially overlaps Pick 7 (Apple Shortcuts → mini). Reference memory
`project_meal_planner_expansion_priority.md` carries the verbatim user ask.

## Long-term future scope (re-evaluate later)

- **Tier-2 LLM orchestrator** — design at `future-architecture-upgrade.md`.
  CEO-approved 2026-04-30 but **demoted to long-term scope on 2026-05-01.**
  Pick 1's `Job` framework is likely to absorb most of its plumbing (typed
  queue, single worker, audit log, console surface, recipe registry).
  Re-evaluate after Pick 1 + meal-planner ship — an orchestrator on top of
  the Jobs framework may still make sense, or the Jobs framework alone may
  be sufficient. Don't pre-build.
- **BlueBubbles iMessage bridge** — requires iCloud sign-in on the mini.
  Defer until we actually want iMessage-based control.
- **Hermes Agent / OpenClaw evaluation** — couldn't verify OpenClaw in 2026
  web searches; both need real-world provenance audit before installing.
  Finance / dispatcher / event-aggregator work fine without an agent framework.
- **Picks 2–10 from the brainstorm** — receipt → YNAB matcher (Pick 2),
  morning brief (Pick 3), document Q&A (Pick 4), trip detector (Pick 6),
  anomaly digest (Pick 10), relationship radar (Pick 9), Recall search
  (Pick 8). All re-rankable in the context of what the meal-planner work
  teaches us; revisit ordering when meal-planner ships.

---

## Reference

- `Mac-mini/README.md` — current state, running services, key decisions
- `Mac-mini/PHASE6.md` — Phase 6 operator runbook
- `Mac-mini/history/` — completed-phase porting recipes
- `~/.claude/plans/i-want-you-to-tranquil-pearl.md` — frozen initial setup
  plan (phases 0–7 as originally scoped); preserved for history
- Memory entries to pull context from at session start:
  - `reference_mac_mini_porting_checklist.md` — **start here** when adding
    a new project on the mini; reproducible order-of-ops
  - `project_mac_mini_keychain_shim.md` — empty-password login keychain +
    `KEYCHAIN_PATH` env var + keyring shim pattern
  - `feedback_macos_afw_python.md` — allow Python through AFW before any
    non-loopback bind or you'll chase a phantom "app is broken" bug
  - `project_mac_mini_path_cleanup.md` — sed rewrites + pycache gotcha +
    the safe `git pull` pattern for the mini's mutated working tree
  - `feedback_macos_tcc_avoid_protected_paths.md` — why code lives at
    `~/Home-Tools`, not `~/Documents`
  - `feedback_mac_mini_readme_upkeep.md` — keep README in sync
  - `project_health_dashboard.md` — current state of the dashboard on
    the mini
  - `project_event_aggregator.md` / `project_setup_state.md` — what the
    event-aggregator expects
  - `feedback_privacy.md` + `feedback_mock_dryrun.md` — never run real data
    through Claude; always `--mock --dry-run`

---

## How to pick up next session

Paste into the opening prompt something like:

> Read `Mac-mini/PLAN.md` and `Mac-mini/README.md` in this repo, then let's
> continue the Mac mini build from where we left off. Next up is Phase 7
> (NAS backup).

That's enough context — the plan points at the memory files and the README,
so Claude will pick up from there.
