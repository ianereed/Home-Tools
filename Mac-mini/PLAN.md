# Mac mini Home Server — Working Plan

Living plan for the ongoing build. Update as phases advance. Pair this with
`Mac-mini/README.md` (state-of-the-project), `Mac-mini/PHASE6.md` (Phase 6
operator runbook), `Mac-mini/history/` (completed-phase porting recipes), and
the `~/.claude/projects/.../memory/` entries (the accumulated lessons).

---

## Naming convention

A **Phase** is confirmed scope, completable in one sitting, sequentially
numbered (no decimals — rule applies starting at Phase 13; Phase 12.5 is
grandfathered). A **Pick** is a catalogued suggestion (see brainstorm at
`~/.claude/plans/come-up-with-more-encapsulated-spring.md`); Picks remain
suggestions until explicitly promoted to a Phase, at which point the Pick
number is retired and only the new Phase number is used.

---

## Quick status (as of 2026-05-01)

**Phase 7 NAS backup LIVE on the mini** (commit `5f806aa`, 2026-05-01):
two restic repos at `~/Share1/mac-mini-backups/{restic-hourly,restic-daily}`
on the iananny NAS. Hourly agent backs up `health.db` at every :17;
daily agent at 03:30 backs up state.json + event_log.jsonl + .env +
finance.db + nas-intake/state.json + login.keychain-db + incidents.jsonl;
weekly prune Sunday 04:00. All 13 deploy gates passed including bare-metal
recovery dry-run. Recovery secrets in 1Password "Mac mini home server
recovery" Secure Note. Operator runbook at `Mac-mini/PHASE7.md`; bootstrap
recovery doc at `Mac-mini/RECOVERY.md`. Time Machine + off-site (B2) deferred.

Phase 6 monitoring layer LIVE on the mini (commit `a13c41a`, 2026-04-30):
heartbeat (every 30 min) → `incidents.jsonl` → daily Slack digest at 07:00
to `#ian-event-aggregator` + weekly SSH-failure digest. Three new LaunchAgents
(`com.home-tools.heartbeat`, `daily-digest`, `weekly-ssh-digest`). Operator
runbook at `Mac-mini/PHASE6.md`. (Heartbeat extended in Phase 7 with a
backup_health probe that ignores in-flight runs.)

Everything Phase 5 and earlier is done; recipes preserved in
`Mac-mini/history/` (5b health-dashboard, 5c service-monitor, 5d NAS mount,
5e nas-intake v1).

The live agent registry is `service-monitor/services.py:SERVICES`. The
service-monitor Streamlit dashboard at `http://homeserver:8502/` shows live
status (PID, last exit, schedule, queue depths, DB sizes, Ollama state, log
tails) for every loaded `com.home-tools.*` and `com.health-dashboard.*` agent.

---

## Resume from here

**Phase 12 v3 LANDED 2026-05-01** (commits f2df5e9 → eb4d3bf → 814961c
→ d83f74d → this commit). 12 cron-style LaunchAgents migrated to
`@huey.periodic_task` Job kinds in `jobs/kinds/`. Mini Ops console live
at `homeserver:8503`. HTTP enqueue at `homeserver:8504`. The
`migration_verifier` Job runs hourly with auto-rollback on baseline
divergence. Operator runbook at `Mac-mini/PHASE12.md`.

**Next single action**: deploy + cutover on the mini.

```bash
ssh homeserver@homeserver '
  cd ~/Home-Tools && git pull
  bash jobs/install.sh                # consumer + http
  bash console/install.sh             # :8503
  bash jobs/install.sh migrate-all    # cut over all 12
  python3 Mac-mini/scripts/preflight.py
'
```

After that, the verifier auto-soaks for 72h. Phase 6's daily-digest at
07:00 surfaces `migration_promoted` and `migration_rollback` incidents.
After all 12 promote: `bash jobs/install.sh cleanup-soaked`.

Then Phase 12.5 (event-aggregator fetch+worker migration) and Phase 13
(meal-planner expansion).

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

## Phase 7 — Backup (DONE 2026-05-01 — NAS-only)

Goal: 3-2-1 backup so we can recover from disk failure or ransomware. Now
that `health.db` is the authoritative copy (laptop's DB is frozen at the
2026-04-22 cutover), losing it means re-scraping from Intervals + Strava
APIs, which only cover recent data. Protect it.

### What actually matters to protect (priority order)

1. `~/Home-Tools/health-dashboard/data/health.db` (~91MB, authoritative)
2. `~/Home-Tools/event-aggregator/state.json` + `event_log.jsonl`
3. `~/Home-Tools/finance-monitor/data/finance.db`
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

### What shipped

- **Two restic repos** at `~/Share1/mac-mini-backups/restic-hourly/` and
  `~/Share1/mac-mini-backups/restic-daily/` (encrypted, content-defined
  chunked, deduplicated). Independent retention per repo.
- **Three LaunchAgents**:
  - `com.home-tools.restic-hourly` — every :17, backs up `health.db`
  - `com.home-tools.restic-daily` — 03:30 daily, backs up state.json +
    event_log.jsonl + .env + finance.db + nas-intake/state.json +
    login.keychain-db + incidents.jsonl
  - `com.home-tools.restic-prune` — Sun 04:00 weekly, runs
    `restic prune` against both repos
- **Recovery secrets** in 1Password Secure Note "Mac mini home server
  recovery" — 5 fields: 2 restic passwords + NAS_USER/PASSWORD/IP. The
  in-repo `Mac-mini/RECOVERY.md` is the bootstrap walkthrough; it points
  at 1Password but never contains the live passwords.
- **Heartbeat extended** with a `backup_health` probe that emits stale
  incidents into the Phase 6 daily-digest pipeline. Ignores logs <60 s
  old to avoid in-flight false-positives.
- **service-monitor** registry now shows the 3 backup agents in a "Backup"
  swim-lane on the dashboard.

### Decisions made (locked)

- **Time Machine dropped from v1.** TM-on-NAS encryption from CLI is
  fragile and unencrypted-on-NAS is a privacy regression. Phase 7.5 if
  ever wanted = USB SSD with TM-via-GUI.
- **Off-site (B2/Wasabi) deferred.** Phase 7.5 if/when something makes
  it feel necessary.

### Files

Implementation plan (with all 8 open questions resolved + outside-voice
findings folded in) at `~/.claude/plans/phase-7-nas-backup.md`. Operator
runbook at `Mac-mini/PHASE7.md`. Recovery doc at `Mac-mini/RECOVERY.md`.

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

## Phase 12 — Mini Jobs framework + Mini Ops console (DONE 2026-05-01 ✅)

Replaced 12 cron-style LaunchAgents with `@huey.periodic_task` Job kinds
in `jobs/kinds/`. Operator runbook: **`Mac-mini/PHASE12.md`**.

- `jobs/` — huey foundation, adapters (slack/gcal/todoist/card/nas/sheet),
  `migration_verifier` (hourly auto-rollback on baseline divergence),
  CLI (`enqueue/status/kinds/new/doctor/migrate/rollback/cleanup-soaked`),
  HTTP enqueue at `homeserver:8504` (Tailscale-bound, bearer-token auth).
- `console/` — Streamlit "Mini Ops" at `homeserver:8503`. Tabs:
  Jobs, Decisions, Ask, Intake, Plan (placeholder for Phase 13).
  Sidebar: Settings status panel.
- 12 migrations land in one commit; cutover per-kind via
  `bash jobs/install.sh migrate-all`. Each migration's `@baseline` metric
  is checked hourly; 72 consecutive successes → auto-promote (delete
  `.plist.disabled`); divergence → auto-rollback (rename back, kickstart
  old plist). Net plist count: 21 → 10 in service-monitor's `SERVICES`.
- Closes OPS6: `event-aggregator/state.py:save()` now requires an active
  `state.locked()` block (RuntimeError otherwise); 32+ callsites wrapped.

Plan source (v3): `~/.claude/plans/phase-12-mini-jobs-queue.md`
Deferred to Phase 12.5: `event-aggregator.fetch` + worker (the queue +
model-swap state machine doesn't decouple cleanly from fetch).

## Phase 12.5 — Event-aggregator on the Jobs framework (IN PROGRESS — 12.8b is next)

**Status as of 2026-05-03 evening:**
- ✅ **12.5** — fetch migrated to `@huey.periodic_task` (commit `028dd0d`, soak ran)
- ✅ **12.6** — `@requires_model("text"|"vision", batch_hint=...)` primitive in `jobs/lib.py`; worker.py shimmed (commit `95e3d0a`)
- ✅ **12.7** — worker decomposed into `event_aggregator_text` / `event_aggregator_vision` / `event_aggregator_decision_poller` huey kinds; `state.text_queue` / `state.ocr_queue` are now transient staging buffers (commits `0cf1b7b` + `df12304`)
- ✅ **12.8a** — 18 pre-promote bug fixes (commit `4873d8f`)
- ✅ **12.8a follow-up** — 4 additional fixes from independent review: fetch.py crash-loss (schedule-before-save), fetch.py TimeoutExpired handler, `jobs/lib.py` concurrency invariant doc, `slack_notifier.py` direct-`_data`-access cleanup (commit `7927bf0`)
- ⏭️ **12.8b — manual promote + cleanup** — next session. Plan file: `~/.claude/plans/lets-scope-phase-12-5-groovy-whale.md`. Verbatim kickoff prompt is in `project_next_steps.md`.

**Why no 72h soak before promote:** the verifier signal between 12.7 and 12.8a was forged — `event_aggregator_fetch` was calling `record_fire("event_aggregator_text")` unconditionally every 10 min, satisfying the verifier's no-fire check regardless of whether any text task ran. Fix 7 removed the proxy; the file-mtime baseline (`run/event-aggregator-text-or-vision.last`) is now the honest signal. Manual promote in 12.8b is the right call.

**Deferred until after 12.8b** (logged in `project_next_steps.md`): F (importlib leak), G (decision_poller cross-lock), H (3 pre-existing `test_proposals.py` failures).

## Phase 13 — Meal-planner overhaul: architecting (one sitting via gstack review pipeline)

**Decided 2026-05-01.** Anny + Ian agreed the meal-planner overhaul is the
most valuable next feature after Phase 12.5. Two stated capabilities the
build will need to deliver:

- **Real iPhone actions** — tap a tile, get a result. Adds to weekly meal
  plan, captures a recipe photo, queries the pantry, etc. Likely uses the
  Apple Shortcuts → mini HTTP endpoint pattern (the `:8504` jobs-http
  endpoint from Phase 12 is the door).
- **Windows-laptop weekly planning collaboration with Claude** — sit down,
  talk through the week's meals with Claude in the loop, end up with a
  populated Sheet + grocery list + Todoist. Not a static UI; a real
  conversation surface.

**This Phase is the architecting sitting only.** Output is a plan, not
code. Run the full gstack review pipeline (`/office-hours` →
`/plan-ceo-review` → `/plan-eng-review` or `/autoplan`) to lock the design
before any build chunks start.

Existing scaffolding to lean on: `meal-planner/` (Apps Script frontend +
Gemini batch sidecar), the model-swap pattern from
`event-aggregator/worker.py`, and Phase 12's Job framework + adapters
layer (`jobs/adapters/{slack,gcal,todoist,card,nas,sheet}.py`). The `sheet`
adapter is currently a strict NotImplementedError stub — Phase 14+ will
fill it in.

This Phase subsumes the meal-planner Gemini → local migration and reuses
the Apple Shortcuts → mini HTTP endpoint groundwork. Reference memory
`project_meal_planner_expansion_priority.md` carries the verbatim user ask.

## Phase 14+ — Meal-planner overhaul: build (numbered as each chunk is claimed)

Once Phase 13 produces a locked plan, the build splits into one-sitting
chunks. Each chunk gets the next sequential Phase number when claimed.
Numbers are not pre-allocated — if Phase 13 outputs four chunks, they
become Phases 14, 15, 16, 17 as each is started.

The "one sitting" rule keeps each Phase scope tight: a chunk that doesn't
finish in a sitting either gets reduced or split before the next Phase
starts.

## Long-term future scope (re-evaluate later)

- **Tier-2 LLM orchestrator** — design at `future-architecture-upgrade.md`.
  CEO-approved 2026-04-30 but **demoted to long-term scope on 2026-05-01.**
  Phase 12's `Job` framework already absorbs most of its plumbing (typed
  queue, single worker, audit log, console surface, recipe registry).
  Re-evaluate after the meal-planner work ships — an orchestrator on top
  of the Jobs framework may still make sense, or the Jobs framework alone
  may be sufficient. Don't pre-build.
- **BlueBubbles iMessage bridge** — requires iCloud sign-in on the mini.
  Defer until we actually want iMessage-based control.
- **Hermes Agent / OpenClaw evaluation** — couldn't verify OpenClaw in 2026
  web searches; both need real-world provenance audit before installing.
  Finance / dispatcher / event-aggregator work fine without an agent framework.
- **Brainstorm backlog (suggestions, no fixed ranking)** —
  `~/.claude/plans/come-up-with-more-encapsulated-spring.md` carries ~55
  ideas grouped by domain. Examples: receipt → YNAB matcher, morning
  brief, document Q&A, trip detector, anomaly digest, relationship radar,
  cross-corpus Recall search. None are committed scope; the user picks
  one in context when ready and it becomes a Phase at that moment.

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
