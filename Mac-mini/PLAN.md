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

## Quick status (as of 2026-05-04)

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

**Phase 12.5 DONE 2026-05-04 ✅** (commits `028dd0d` → `95e3d0a` →
`0cf1b7b` → `df12304` → `4873d8f` → `7927bf0` → `79fdfef` → `a242e30`
→ `748cb09`). Event-aggregator fetch + worker migrated to huey kinds;
legacy worker loop retired. Both migrations promoted. Full record in
`Mac-mini/PHASE12.md` "Phase 12.5 follow-up" section.

**Phase 13 architecting DONE 2026-05-01 ✅** — approved design at
`~/.gstack/projects/ianereed-Home-Tools/ianereed-main-design-20260501-132248.md`.
Approach C2 (fills Phase 12's reserved Plan slot). Locked decisions in
`project_meal_planner_expansion_priority.md` memory.

**Phase 14 DONE 2026-05-04 ✅** — Meal-planner V0 shipped. Recipes tab live
at `http://homeserver:8503/?tab=recipes`. SC6 verified by Ian dogfood
(11 consolidated tasks landed in Todoist correctly); Anny walkthrough still
pending.

**Phase 14.8 DONE 2026-05-05 ✅** — Clear-all Todoist button shipped (commit
`952f4a2`). Dogfood: 12 tasks created → clear job fired → 0 meal-planner
tasks remain, 40 event-aggregator tasks untouched.

**Phase 14.9 DONE 2026-05-04 ✅** — Multi-recipe grid shipped (commit
`94c2259`). Dogfood: 2 recipes selected → 18 consolidated grocery tasks
created → 0 after clear. Event-aggregator 40 tasks untouched. All 142 tests
pass.

**Phase 14.10 DONE 2026-05-05 ✅** — Bypass consolidation; raw scaled lines
per recipe (commit `090bb69`). Kind no longer calls Gemini. Each ingredient
emits as a separate Todoist task with `(Recipe Name)` suffix. 143/143 tests
pass. **Live-verified 2026-05-05 11:30** — task `4d66f94b` produced 14
Todoist tasks with `(Broccoli & Lemon Risotto)` suffix on every line.
First dogfood (task `4d267f5b`) ran on a stale consumer process from
before the merge — required `launchctl bootout`/`bootstrap` on
`com.home-tools.jobs-consumer` to reload the kind module. Lesson:
git pull is not deploy; the huey worker must be kickstarted/restarted
after merging changes to any `jobs/kinds/*.py` file.

**Phase 14.11 DONE 2026-05-05 ✅** — Tag filter on Recipes tab (commit
`1c165e8`). `st.pills` multi-select + AND/OR radio above the grid. `list_all_tags()`
helper added; `search_recipes()` extended with `tag_logic` param. 148/148 tests
pass.

**Phase 15 DONE 2026-05-06** — bake-off ran on llama3.2-vision:11b; production
prompt baseline + warm-reuse harness. Output: `meal_planner/eval/PHASE15_NOTES.md`.

**Phase 16 DONE 2026-05-07** — Recipe-photo intake live. NAS folder watched by
`meal_planner_photo_intake_scan` enqueues `meal_planner_ingest_photo`, which runs
llama3.2-vision via Ollama on the mini, validates schema, retries once on
malformed output, runs `_normalize.py` to fix qty/unit fusion bugs, inserts the
recipe + ingredients + tags into `recipes.db`, and renames the photo into `_done/`
with a sidecar JSON. Chunk F (`cce769c`) added the deterministic post-extraction
normalizer; review-fixes pass (`4b38f10`) hardened multi-token units, Pattern 2
over-fire guards, retry-path normalization, and DB-persisted normalize warnings.
273/273 tests pass.

**Phase 17 — UI polish (in progress).**

**Chunk A DONE 2026-05-07** (`b560a6d`) — Categorized tag pills: split the flat
`st.pills` row into three labeled groups (Cuisine / Meat+diet / Other) driven by
`meal_planner/tag_categories.py:CATEGORY_MAP`. 6 new tests; 279/279 pass.

**Chunk B DONE 2026-05-07** (`e590aa6`) — Alpha-sort toggle: `st.toggle("Alphabetical", value=True)`
above the grid. Default on = alpha by title; off = id DESC (most-recently-added). `sort` param
added to `search_recipes()`; validated before SQL composition. 5 new tests; 566 pass.

**Chunk C DONE 2026-05-07** (`9b065c9` + re-fix `39d53bc`) — Todoist-success indicator: replaced immediate toast
with `@st.fragment(run_every="2s")` polling `huey.result(task_id, blocking=False)`. Spinner while
pending ("Send to Todoist… (Ns)"); green/yellow/red on terminal state. Applies to both Send and
Clear buttons. Result-dict contract locked: `{items_sent, items_attempted, consolidate_failed,
consolidate_dropped, error}`; `consolidate_*` keys default to `None`/`0` (the never-built Consolidate+Send was moved to the future-ideas catalogue as M9 on 2026-05-07). `clear_todoist`
contract updated to `{items_cleared, error}`. +11 tests; 577 pass. Re-fix `39d53bc` (Opus review caught
on Check #9): wrapped `_huey.result()` in try/except via new `_read_result_or_synthesize_error` helper
in `console/tabs/_job_status.py` — huey 3.0.0 re-raises `TaskException` on failed tasks; without the guard
the fragment exception-looped every 2s and never cleared `session_state`. +4 integration tests; 581 pass.
Live failure-path verified end-to-end via `TODOIST_SECTIONS` corruption: red banner reading
`Send to Todoist: failed: task crashed: TaskException: JSONDecodeError(...) (sent 0/0)`.

**Chunk D DONE 2026-05-07** — Recipe-header tasks: `meal_planner_send_to_todoist` now emits one
extra Todoist task per recipe in the "Meals" section (`id=6g34CGWFCmJjQrgr`), titled
`<recipe.title> (<N> servings)` with the `meal-planner` label. Counts toward `items_sent` /
`items_attempted` (e.g. 12-ingredient recipe → banner shows "13/13 items"). Validation raises
`RuntimeError` if `TODOIST_SECTIONS` is missing the `"Meals"` key; `_format_status` converts this
to a red banner. +6 tests; 587 pass. Live-verified: header tasks appear in Meals section; failure
path confirmed via consumer stderr (`RuntimeError: TODOIST_SECTIONS is missing 'Meals' section`);
clear scope unchanged (header tasks carry `meal-planner` label, swept by existing clear job). Phase 18 WAL-fd bug blocked the UI error banner; confirmed working at consumer layer.

**Then: Phase 18 — Edit recipes via web GUI + Sheet decommission +
jobs-queue bug fix.** Two workstreams bundled in one phase:

1. **Web-edit recipes + decommission the Apps Script Sheet fallback** —
   move recipe edits into `console/tabs/plan.py`; the Sheet stops being
   the source-of-truth (it's been a read-only fallback since Phase 14).
2. **Jobs-queue bug fix** — two bugs surfaced 2026-05-07 (see
   `memory/project_nas_intake_worker_wedge_bug.md` and `journal-135.md`
   + `journal-136.md`): nas_intake_scan starved the shared huey worker
   for ~25 min, and the long-running streamlit held an orphan WAL fd
   that silently dropped two send-to-Todoist enqueues.

Both workstreams land on `fix/phase18-recipe-edit-and-jobs-queue` (or
two sibling branches that merge together), separate from Phase 17 so
the Phase 17 UI work isn't destabilized. Detail for each in the Phase
18 section below.

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
    login.keychain-db + incidents.jsonl + meal_planner/recipes.db +
    meal_planner/seed_progress.json
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

**Health migrations deferred (2026-05-04):** `health_collect`,
`health_intervals_poll`, `health_staleness` huey kinds exist and fire on
schedule, but `jobs.cli migrate` was never run on them — no
`migrations.json` entry, no verifier baseline, original plists unloaded
but not renamed to `.disabled`. Operationally fine. Punted to a later
phase; close out with `jobs.cli migrate <kind>` then `promote` for each.

## Phase 12.5 — Event-aggregator on the Jobs framework (DONE 2026-05-04 ✅)

All sub-phases complete. Full record in `Mac-mini/PHASE12.md` "Phase 12.5
follow-up" section.

- ✅ **12.5** — fetch → `@huey.periodic_task` (commit `028dd0d`)
- ✅ **12.6** — `@requires_model` primitive in `jobs/lib.py` (commit `95e3d0a`)
- ✅ **12.7** — worker decomposed into 3 huey kinds (commits `0cf1b7b`, `df12304`)
- ✅ **12.8a** — 22 pre-promote fixes (commits `4873d8f`, `7927bf0`)
- ✅ **12.8b** — promote + worker loop retired (commits `79fdfef`, `a242e30`, `748cb09`)

**Deferred** (logged in PHASE12.md): F (importlib exec_module leak), G
(decision_poller cross-lock), H (3 pre-existing `test_proposals.py` failures).

## Phase 13 — Meal-planner overhaul: architecting (DONE 2026-05-01 ✅)

Design approved via /office-hours + gstack review pipeline. Approach C2
(meal-planner fills Phase 12's reserved Plan slot). Locked decisions in
memory: `project_meal_planner_expansion_priority.md`. Approved design doc:
`~/.gstack/projects/ianereed-Home-Tools/ianereed-main-design-20260501-132248.md`.

## Phase 14 — Meal-planner V0 (DONE 2026-05-04 ✅)

Recipe DB + Recipes tab in console + send-to-Todoist Job kind + sheet seed +
`console/app.py` deep-link refactor. Phases 14.1–14.7 all landed on
`phase14/meal-planner-v0`. Key commits: package cutover (14.1), read API +
Recipes tab (14.2), Sheet seeder (14.3), Todoist adapter (14.4),
consolidation + send-to-Todoist kind (14.5), deep-link refactor + rename
(14.6), V0 ship + infra fixes (14.7).

Infra fixes shipped in 14.7: `jobs/run-consumer.sh` now sources
`meal_planner/.env`; `requests` added to `jobs/requirements.txt`.

Success Criteria status: SC1 ✅ deep-link works, SC2 16 recipes (dataset
ceiling), SC3 tags TBD post-Anny-walkthrough, SC4 ✅ dropdown+slider+button
render, SC5 ✅ kind registered in huey, SC6 deferred to Anny walkthrough.

## Phase 14.8 — Recipes tab: "Clear all meal-planner items from Todoist" button (DONE 2026-05-05 ✅)

V0 polish. "Clear all meal-planner items from Todoist" button below Send-to-Todoist.

Shipped in commit `952f4a2`:
- `jobs/kinds/meal_planner_clear_todoist.py` — lists via `GET /api/v1/tasks?label=meal-planner`
  (paginated via next_cursor), deletes per-task, collects failures, returns
  `{"deleted": N, "failed": M, "failed_ids": [...]}`. `LABEL = "meal-planner"` is
  a module-level constant (safety boundary).
- `jobs/tests/test_meal_planner_clear_todoist.py` — 7 tests; all pass.
- `console/tabs/plan.py` — two-click confirm button (st.session_state timestamp pattern).
- `meal_planner/README.md` — note that label is a code constant, not an env var.

Dogfood 2026-05-05: 12 meal-planner tasks created, clear job fired, 0 tasks remaining,
40 event-aggregator tasks untouched. Exit gate: all 10 items passed.

## Phase 14.9 — Recipes tab: multi-recipe grid (DONE 2026-05-04 ✅)

Replaced single-recipe selectbox + slider + ingredient table with a `st.data_editor`
multi-recipe grid. Three columns: Send (CheckboxColumn), Recipe (TextColumn,
disabled), Servings (NumberColumn, min=1, max=20, step=1, default=base_servings).

Shipped in commit `94c2259`:
- `console/tabs/plan.py` — `_render_inner()` replaced with grid + Send button.
  `_render_clear_button()` unchanged.

Dogfood 2026-05-04: 2 recipes selected (Anny's Ji dan ×4 + Broccoli & Lemon Risotto ×6),
18 consolidated grocery tasks created. Clear confirmed 0 remaining.
Event-aggregator count 40 — untouched. 142/142 tests pass.

Next UI iterations (not yet scoped):

- **Ingredient-edit view** — adjust per-recipe quantities from the console
  before sending.
- **End-to-end success indication on the Recipes tab.** Today the
  "Job enqueued — task ID: …" toast paints green regardless of what happens
  inside the kind. The 2026-05-04 quota incident produced a green toast even
  though 0 Todoist tasks were created. The UI should show the actual outcome
  of the run: full success (N items sent), partial (M of N), or failure
  (consolidation 429, Todoist auth, etc.). Likely needs the kind to write
  its result where the tab can poll (huey result store, or a
  `meal_planner_runs` table), then a status block on the Recipes tab.

## Phase 14.10 — Recipes tab: bypass consolidation (DONE 2026-05-05 ✅)

`meal_planner_send_to_todoist` no longer calls Gemini consolidation.
Shipped in commit `090bb69`:
- `jobs/kinds/meal_planner_send_to_todoist.py` — removed `consolidate_for_grocery`
  import and `GEMINI_API_KEY` env read. For each `(recipe, target_servings)` pair,
  calls `scale_ingredients()` and emits one Todoist task per ingredient.
  Title format: `"{qty:.4g} {unit} {name} ({Recipe.title})"` (unit omitted if None,
  qty omitted if None). Section routing uses `Ingredient.todoist_section` directly;
  unknown/None section falls back to first key in `TODOIST_SECTIONS`.
  `source_id` per task is `f"recipes:{recipe.id}"`.
- `jobs/tests/test_meal_planner_send_to_todoist.py` — rewritten with 5 tests,
  no Gemini mocks. 143/143 tests pass.
- `consolidation.py` left on disk untouched.

## Phase 14.11 — Recipes tab: tag filter (DONE 2026-05-05 ✅)

Tag filter above the multi-recipe grid on the Recipes tab. Resolves SC3.

- `meal_planner/queries.py` — added `list_all_tags(*, path)` helper (returns
  sorted distinct tags linked to ≥1 recipe, orphan tags excluded via JOIN).
- `meal_planner/queries.py` — extended `search_recipes()` with
  `tag_logic: str = "and"` param. OR mode uses EXISTS/IN subquery. Raises
  `ValueError` on unrecognized logic value.
- `console/tabs/plan.py` — renders `st.pills` (Streamlit 1.57 on mini, well above
  the 1.40 requirement) + `st.radio("Match", ["AND","OR"])` above the grid.
  Empty selection = all recipes. Filter-active empty-state message distinguished
  from DB-empty message.
- `meal_planner/tests/test_queries.py` — 5 new tests: orphan exclusion, OR union,
  AND intersection, empty tags = all, invalid logic raises. 148/148 tests pass.

## Phase 15 — Recipe-photo-LLM bake-off (DONE 2026-05-06)

Research only — no production code. Output: `meal_planner/eval/PHASE15_NOTES.md`.
Picked **llama3.2-vision:11b** via Ollama on the mini (local; no API quota
exposure). Bake-off harness in `meal_planner/eval/bake_off.py` with
warm-reuse + relaxed F1 + per-call `keep_alive_override`. Synonyms +
unicode-fraction expansion in `meal_planner/eval/synonyms.yml`.

Entry gate (Anny's full SC walkthrough SC1–SC6) passed during Phase 14.11.

**Open question — API quota visibility.** Google's Gemini API has no
programmatic quota-check endpoint; the only signals are 429s at request time,
the AI Studio dashboard (manual UI), or GCP Console (if linked). On 2026-05-04
a `meal_planner_send_to_todoist` run silently produced 0 Todoist tasks because
free-tier `gemini-2.5-flash-lite` RPD (20/day) was exceeded — AI Studio
showed 24/20. The retry loop in `consolidation.py:_call_gemini` ran 4 attempts,
each got 429, and RPD is a 24h rolling window so retries can't recover. The
kind then returned an empty grocery list, the UI showed a green "Job enqueued"
toast, and we only noticed because Todoist was empty. If Gemini wins the
bake-off, we need a local API counter (per-key, per-day, per-model) so we can
tell when the daily limit is closing in before users hit silent failures.
Whichever provider wins should get the same treatment.

## Phase 16 — Recipe-photo intake (DONE 2026-05-07)

Pipeline: NAS folder `Share1/Documents/Recipes/photo-intake/` →
`meal_planner_photo_intake_scan` (every 60s, computes sha, dedups against
`photos_intake` table, moves to `_processing/`) → `meal_planner_ingest_photo`
(preprocess image, call Ollama vision, normalize, insert recipe + tags +
ingredients, rename to `_done/<sha>.jpg` + `<sha>.json` sidecar).

**Chunks shipped:**

- **Chunk 1** — Schema add: `photos_intake` table with sha-keyed dedup;
  `meal_planner.db.init_db` extended.
- **Chunk 2** — `meal_planner_photo_intake_scan` + `meal_planner_ingest_photo`
  job kinds; `_processing/` / `_done/` directory state machine; status row per
  photo (pending → extracting → ok / ok_partial / ollama_error).
- **Chunk 2.5** — Stuck-extracting recovery, rename split-brain fixes, scan
  self-heal (`5793488`).
- **Chunk 2.6** — Never-drop ingestion: prompt tightening, sidecar JSON,
  tag persistence (`f55f8a8`).
- **Chunk F** — Post-extraction normalizer `_normalize.py` (`cce769c`):
  three deterministic patterns fix LLM qty/unit output bugs without touching
  the prompt. Replay validation: scale_ok 76.7% → 97.7%, F1 0.754 → 0.761.
- **Review-fix pass** (`4b38f10`): multi-token units (`fl oz`,
  `fluid ounces`), Pattern 2 over-fire guards (`slice of bread`, single-word
  unit names), retry-path always normalizes, Pattern 3 emits a "discarded
  unit content" warning, replay dedup gates on scoreable status, DB
  persists `normalize_warnings`. 273/273 tests pass.

Production `meal_planner_ingest_photo` runs on the mini's
`com.home-tools.jobs-consumer` LaunchAgent. Live test: Nanaimo Bars PDF
processed end-to-end 2026-05-06.

## Phase 18 — Edit recipes via web GUI + Sheet decommission + jobs-queue bug fix (SCOPED 2026-05-07; chunked 2026-05-07 for Sonnet 200k execution)

Two workstreams (A = recipe edit + Sheet retire, B = jobs-queue bug
fixes), decomposed into **5 chunks** sized for a Sonnet 200k context
window. Each chunk is a single shippable deliverable with green tests
and an explicit `/ship` gate. **Between chunks: `/compact`, then Opus
reviews the diff before the next Sonnet kickoff.**

### Why chunked?

Auto-mode Sonnet at 200k can do ~150 LOC of new code + ~10 file reads +
pytest output noise comfortably. Past 200k the working memory degrades
and tool outputs (pytest tracebacks, lsof, launchctl) start to push
context. The chunks below each fit ≤200k by construction; A2 (UI
rewrite) is the largest and most fragile.

### Bug context (so each chunk's prompt can stay tight)

1. **`nas_intake_scan` starves the shared huey worker.** Multi-page
   healthcare PDF held Worker-1 ~25 min; v1.1 large-file escalation
   never armed because the small-file path completed in one shot
   (escalation only triggers on `subprocess.TimeoutExpired`). Single-
   worker consumer means any slow non-model kind starves every other
   kind. **Primary fix (1A):** `nas-intake/config.py
   SUBPROCESS_TIMEOUT_S = 600 → 90`. After 3×90s timeouts (~5 min)
   escalation arms and the file routes to `ingest-image-large`. Fits
   in chunk B1.
2. **Streamlit holds orphan SQLite WAL fd, silently drops enqueues.**
   Long-running console held WAL/shm fds on inodes that no longer
   existed; `INSERT INTO task` succeeded into a deleted WAL the
   consumer could never read. Two clicks fell into the hole. The fix
   is to remove every `from jobs import huey` from `console/` and
   route both **enqueue** *and* **result polling** through the HTTP
   server (`jobs/enqueue_http.py`, port 8504, already running but
   dormant — token missing from keychain). Originally scoped as
   "enqueue only" — extended in chunking review because
   `console/tabs/plan.py` *also* polls `_huey.result(task_id)` every
   2s in `_render_job_status`, which is the same WAL-fd vector.
   Greppable post-condition after chunk B2:
   `grep -r "from jobs import huey" console/` returns ZERO lines.

### Chunk ordering and dependencies

```
B1 (bug fixes infra) ──┬──> B2 (console route-through) ──┐
                       │                                 │
                       └──> A1 (CRUD backend) ──> A2 ────┴──> A3 (Sheet decom)
                                                  (UI)
```

- B1 ships first (smallest, clears infra prereqs for B2).
- B2 second (closes WAL-fd silent-drop window before any new console code lands).
- A1, A2, A3 sequential (A2 needs A1's CRUD, A3 needs both).
- B1+B2 are independent of A1/A2/A3 — they could be reordered if Anny
  reports an urgent edit need, but B-then-A is the recommended path.

Each chunk lands on its own branch and merges to `main` independently.
Branch names below.

---

### Phase 18 — DONE 2026-05-08

All 5 chunks merged to `main`.

| Chunk | Branch | Merged SHA | Summary |
|-------|--------|-----------|---------|
| B1 | `fix/phase18-b1-jobs-http-infra` | `2e340bf` | nas-intake timeout 600→90; jobs-http token; `/queue-size` + `/jobs/<id>` endpoints |
| B2 | `fix/phase18-b2-console-http` | `4b54402` | console routed through enqueue_http; `from jobs import huey` gone from console/ |
| A1 | `feat/phase18-a1-recipe-crud-backend` | `921e014` | `queries.py` CRUD (create/update/delete recipe+ingredient+tags); 431 tests |
| A2 | `feat/phase18-a2-recipe-edit-ui` | `b738804` | recipe edit/new/delete web UI in console; `_recipe_form.py` pure helpers |
| A3 | `feat/phase18-a3-sheet-decommission` | `a82db46` | `export_sheet_to_db.py` diff+import script; Sheet archived read-only |

Done criteria met:
- `grep -r "from jobs import huey" console/` → 0 lines ✓
- `recipes.db` is sole source-of-truth; Sheet archived read-only ✓
- Apps Script SETUP.md prepended with ARCHIVED block ✓
- nas-intake `SUBPROCESS_TIMEOUT_S = 90` ✓

**Operator steps (run on mini after merge — see also journal-164.md):**
1. `cd ~/Home-Tools && python -m meal_planner.scripts.export_sheet_to_db` (dry-run; review diff)
2. If diff is acceptable: `python -m meal_planner.scripts.export_sheet_to_db --apply`
3. In Google Drive UI: change Sheet permissions to "Viewer (Anyone with link)"; move to `_Archive/` folder.
4. In `meal_planner/.env` on the mini: comment out `MEAL_PLANNER_SHEET_ID` and `GOOGLE_SERVICE_ACCOUNT_PATH`.
5. Restart `com.home-tools.console` to pick up the updated console code.

---

### Chunk B1 — Bug-fix infra: nas-intake timeout, jobs-http token, queue-size + result endpoints — DONE 2026-05-07 @e34f45b

**Branch:** `fix/phase18-b1-jobs-http-infra`
**Scope:** ~80 LOC across 4 files + 1 install.sh edit. No console changes.

**Deliverables:**
1. `nas-intake/config.py`: `SUBPROCESS_TIMEOUT_S = 600 → 90`.
2. `jobs/install.sh`: idempotent `add-generic-password` step that creates
   `home-tools/jobs_http_token` (random 32-byte hex via `openssl rand -hex 32`)
   ONLY if `find-generic-password` doesn't already return one. Never
   regenerates an existing token (would invalidate every running consumer).
3. `jobs/enqueue_http.py`: add `GET /queue-size` (returns
   `{"size": int}`); promote `GET /jobs/<id>` from its 501-stub to
   functional — calls `huey.result(id, blocking=False)`, catches
   `TaskException`, returns `{"status": "pending|success|error",
   "result": ..., "error": ...}` (server-side synthesize-error so
   the client doesn't need to import huey).
4. `jobs/tests/test_enqueue_http.py`: cases for `/queue-size` (200,
   integer body), `/jobs/<id>` pending (200, status=pending), success
   (200, status=success, result echoes), error (200, status=error,
   error string), missing id (404). Use the existing `FakeRequest`
   harness.
5. `Mac-mini/PLAN.md` Phase 18 marker: change Chunk B1 status line to
   "DONE <date>" with the commit short SHA.

**Test gate:**
```
cd ~/Home-Tools && jobs/.venv/bin/pytest jobs/tests/test_enqueue_http.py -q
```
All previously passing tests still pass. New endpoint tests pass.

**Validation on mini (after merge, before declaring done):**
```
ssh homeserver@homeserver
bash ~/Home-Tools/jobs/install.sh    # idempotent; creates token if missing
launchctl kickstart -kp gui/501/com.home-tools.jobs-http
curl -s -H "Authorization: Bearer $(security find-generic-password -a home-tools -s jobs_http_token -w)" http://100.66.241.126:8504/queue-size
# expect: {"size": <int>}
```

**Sonnet kickoff prompt — paste verbatim into a fresh session:**
```
We are starting Phase 18 Chunk B1 of meal_planner — bug-fix infra
groundwork. Read the following to orient (do NOT read the whole
codebase):

  1. Mac-mini/PLAN.md — find the "Phase 18" section and read just
     "Chunk B1" (Bug-fix infra) plus the bug-context block above it.
  2. jobs/enqueue_http.py — full file.
  3. jobs/tests/test_enqueue_http.py — full file (FakeRequest harness).
  4. jobs/install.sh — full file.
  5. nas-intake/config.py — line 27 only (SUBPROCESS_TIMEOUT_S).
  6. Memory: project_event_aggregator.md, project_meal_planner.md,
     feedback_keychain_audit_session_unlock_scope.md.

Goal: ship the 5 deliverables listed in Chunk B1. Branch name:
fix/phase18-b1-jobs-http-infra.

Constraints:
  - Do NOT touch console/ in this chunk. Bug-2 fix is Chunk B2.
  - Do NOT regenerate an existing keychain token; install.sh must be
    safe to re-run.
  - GET /jobs/<id> must do server-side synthesize-error so the client
    never needs to import huey.

When done:
  1. All tests in jobs/tests/test_enqueue_http.py pass.
  2. Update PLAN.md Chunk B1 status to "DONE <YYYY-MM-DD> @<sha>".
  3. Append a journal entry summarizing the diff + verbatim pytest
     output.
  4. Run /ship with branch fix/phase18-b1-jobs-http-infra.
  5. Hand off: print "B1 complete — ready for /compact + Opus review."
     Stop. Do not start B2.

Status: NOT STARTED.
```

---

### Chunk B2 — Route all console huey access through HTTP (kills WAL-fd silent-drop) — DONE 2026-05-07 @ad0718e

**Branch:** `fix/phase18-b2-console-http`
**Scope:** ~120 LOC across 5 files + 1 new file + 1 new test file.
**Depends on:** Chunk B1 must be merged + deployed on mini.

**Deliverables:**
1. New `console/jobs_client.py` — bearer-auth HTTP client with
   `enqueue(kind: str, params: dict) -> str` (returns task_id),
   `queue_size() -> int | None`, and `result(task_id: str) -> dict |
   None` (None=pending, dict=terminal). Reads token from env
   `HOME_TOOLS_HTTP_TOKEN`; base URL from env
   `HOME_TOOLS_HTTP_URL` (default `http://100.66.241.126:8504`). All
   network errors caught and surfaced as a synthesized error dict
   shaped for `_format_status`. ~80 LOC.
2. `console/tests/test_jobs_client.py` — new file. Mock `httpx`/
   `requests` (whichever the client uses; prefer stdlib `urllib` to
   avoid a new dep). Test enqueue success, enqueue 5xx → error dict,
   queue_size 200, queue_size network error → returns None,
   result-pending, result-terminal, result-error.
3. `console/tabs/plan.py` — replace:
   - `from jobs.kinds.meal_planner_send_to_todoist import meal_planner_send_to_todoist` + the `meal_planner_send_to_todoist(checked)` call (~line 155–164)
   - `from jobs.kinds.meal_planner_clear_todoist import meal_planner_clear_todoist` + the `meal_planner_clear_todoist()` call (~line 207–215)
   - `from jobs import huey as _huey` (top of file) — DELETE
   - `_read_result_or_synthesize_error(_huey, task_id)` (~line 50)
     → `_read_result_or_synthesize_error(jobs_client, task_id)` *or*
     refactor `_read_result_or_synthesize_error` to take a `result_fn`
     callable instead of a huey module. The latter is cleaner.
4. `console/tabs/_job_status.py` — refactor
   `_read_result_or_synthesize_error` to accept a callable
   `result_fn(task_id) -> dict | None | raises`. Update its tests
   in `meal_planner/tests/test_read_result_or_synthesize_error.py`
   to pass a fake fn instead of a fake huey module.
5. `console/tabs/jobs.py` — replace `huey.storage.queue_size()` (~line 42–43) with `jobs_client.queue_size()`; remove `from jobs import huey` import.
6. `console/sidebar/settings.py` — replace `huey.storage.queue_size()` (~line 17–19) with `jobs_client.queue_size()`; the `jobs db · …/jobs.db` line becomes `jobs http · {url}` (or remove entirely — your call). Remove `from jobs import huey` import.
7. `Mac-mini/PLAN.md` Phase 18 Chunk B2 status update.

**Greppable post-condition (must enforce in tests + manual check):**
```
grep -r "from jobs import huey" console/   # → 0 lines
```

**Test gate:**
```
cd ~/Home-Tools && jobs/.venv/bin/pytest console/tests/test_jobs_client.py meal_planner/tests/test_read_result_or_synthesize_error.py jobs/tests/test_enqueue_http.py -q
```
Plus full suite:
```
jobs/.venv/bin/pytest -q
```

**Validation on mini (after merge):**
```
launchctl kickstart -kp gui/501/com.home-tools.console
sleep 5
lsof -p $(pgrep -f streamlit | head -1) | grep -E "jobs\.db|home-tools-jobs" || echo "OK: no jobs.db fd in streamlit"
# Open http://homeserver:8503/?tab=recipes → check a recipe → click Send
# → confirm Todoist receives within 30s.
# Then: launchctl bootout gui/501/com.home-tools.jobs-http
# Click Send again → verify visible error toast (failure-path).
# Then: launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.home-tools.jobs-http.plist
```

**Sonnet kickoff prompt — paste verbatim:**
```
We are starting Phase 18 Chunk B2 of meal_planner — route the console's
huey access through HTTP so the streamlit process no longer holds a
WAL fd. Chunk B1 must be DONE on main before you begin (verify with
`grep "DONE" Mac-mini/PLAN.md` for Chunk B1).

Read to orient:
  1. Mac-mini/PLAN.md — Phase 18 "Chunk B2" subsection plus the bug
     context.
  2. console/tabs/plan.py — full file. Note the two enqueue sites
     (~line 155 send, ~line 207 clear) and the result-poll fragment
     (~line 37 _render_job_status calling _read_result_or_synthesize_error
     at ~line 50).
  3. console/tabs/_job_status.py — full file (the helper to refactor).
  4. console/tabs/jobs.py — line 42 only.
  5. console/sidebar/settings.py — full file.
  6. jobs/enqueue_http.py — full file (B1 added /queue-size and
     functional /jobs/<id>).
  7. meal_planner/tests/test_read_result_or_synthesize_error.py — full
     file (so you know what tests need to migrate).
  8. Memory: feedback_streamlit_fragment_huey_polling.md,
     feedback_sqlite_wal_copy_sidecars.md.

Goal: ship the 7 deliverables listed in Chunk B2. Branch name:
fix/phase18-b2-console-http.

Hard invariants:
  - `grep -r "from jobs import huey" console/` returns 0 lines after
    your change. Add this as an automated test in
    console/tests/test_jobs_client.py (subprocess grep + assert empty).
  - Prefer stdlib `urllib.request` over a new dep. If you reach for
    `requests`, justify it in the journal.
  - Refactor `_read_result_or_synthesize_error` to take a callable, not
    a huey module — keeps it pure and testable without mocking imports.
  - The `@st.fragment(run_every="2s")` polling cadence stays unchanged.

When done:
  1. Full test suite green: jobs/.venv/bin/pytest -q.
  2. PLAN.md Chunk B2 status → "DONE <date> @<sha>".
  3. Journal entry with diff summary + verbatim pytest tail.
  4. /ship with branch fix/phase18-b2-console-http.
  5. After /ship lands and CI is green, write the post-merge mini
     validation checklist to the journal as a TODO for the user (do
     NOT ssh into the mini yourself — that's an operator step).
  6. Print "B2 complete — ready for /compact + Opus review.
     Mini-validation checklist is in the journal." Stop.

Status: NOT STARTED.
```

---

### Chunk A1 — Recipe CRUD backend — DONE 2026-05-07 @feat/phase18-a1-recipe-crud-backend

**Branch:** `feat/phase18-a1-recipe-crud-backend`
**Scope:** ~200 LOC across 3 files. No console changes; backend only.

**Deliverables:**
1. `meal_planner/queries.py` (or split into `meal_planner/mutations.py`
   if the file grows past ~250 lines — your call): add
   - `update_recipe(recipe_id: int, *, title: str | None = None, base_servings: int | None = None, instructions: str | None = None, cook_time_min: int | None = None, source: str | None = None) -> None` — partial update; only non-None fields are written; bumps `updated_at`. Raises `KeyError` on missing id.
   - `delete_recipe(recipe_id: int) -> None` — single `DELETE`; cascades ingredients + recipe_tags via existing FK ON DELETE CASCADE (`foreign_keys=ON` already set). Raises `KeyError` on missing id.
   - `add_ingredient(recipe_id: int, *, name: str, qty_per_serving: float | None, unit: str | None, notes: str | None, todoist_section: str | None, sort_order: int) -> int` — returns new ingredient id; bumps recipe `updated_at`.
   - `update_ingredient(ingredient_id: int, *, name=None, qty_per_serving=None, unit=None, notes=None, todoist_section=None, sort_order=None) -> None` — partial update; bumps parent recipe `updated_at`. Raises `KeyError`.
   - `delete_ingredient(ingredient_id: int) -> None` — bumps parent recipe `updated_at` BEFORE deleting (read-then-write in one tx).
   - `set_recipe_tags(recipe_id: int, tags: list[str]) -> None` — replace-style: deletes all existing recipe_tags rows for this recipe, then inserts fresh ones (lowercased, deduped). Garbage-collects orphan tag rows not linked to any recipe (optional; add a `_gc_orphan_tags(conn)` helper). Bumps recipe `updated_at`.
   - `create_recipe(*, title: str, base_servings: int = 4, instructions: str | None = None, cook_time_min: int | None = None, source: str | None = None) -> int` — wraps existing `db.insert_recipe`; this is the public/UI-facing name. (Don't rename insert_recipe — photo intake uses it.)
2. `meal_planner/tests/test_queries.py` (or `test_mutations.py`): one
   test per CRUD fn covering happy path + sad path (KeyError on bad
   id, validation on empty title, etc.). Total ~15 new test cases.
3. `meal_planner/db.py`: only if needed — add an `_update_recipe_updated_at(conn, recipe_id)` helper if multiple call sites converge on the same one-liner. Don't add a trigger; explicit > implicit for V1.

**Test gate:**
```
jobs/.venv/bin/pytest meal_planner/tests/ -q
```
Plus full suite stays green.

**Sonnet kickoff prompt — paste verbatim:**
```
We are starting Phase 18 Chunk A1 of meal_planner — recipe CRUD
backend. This adds the database mutation primitives the Chunk A2 web
UI will call. No UI changes in A1.

Read to orient:
  1. Mac-mini/PLAN.md — Phase 18 "Chunk A1" subsection.
  2. meal_planner/db.py — full file (schema + insert_recipe +
     insert_ingredient + add_recipe_tag).
  3. meal_planner/queries.py — full file.
  4. meal_planner/models.py — full file (Recipe, Ingredient,
     GroceryLine).
  5. meal_planner/tests/test_queries.py — full file (so you know the
     test patterns).
  6. Memory: feedback_no_abstraction_for_simple_fixes.md,
     feedback_insert_or_ignore_silent_failure.md.

Goal: ship the 7 CRUD functions listed in Chunk A1 plus tests. Branch:
feat/phase18-a1-recipe-crud-backend.

Hard invariants:
  - Every mutation that changes a recipe OR its children (ingredients,
    tags) bumps `recipes.updated_at` to NOW. Test it.
  - delete_recipe is one DELETE; rely on FK cascade. Verify
    `foreign_keys=ON` is in db._PRAGMAS — it is.
  - update_* are partial: pass only the fields you want to change.
    None means "do not change."
  - set_recipe_tags is replace-style. Lowercase + dedup. Don't break
    existing add_recipe_tag callers (photo intake) — additive only.
  - Every fn raises KeyError on a non-existent id. Use cur.rowcount
    check after the UPDATE/DELETE to detect (memory:
    feedback_insert_or_ignore_silent_failure.md).
  - Don't import streamlit, pandas, or anything UI-y in this chunk.

When done:
  1. meal_planner/tests/* green; full suite green.
  2. PLAN.md Chunk A1 status → "DONE <date> @<sha>".
  3. Journal entry summarizing fns added + verbatim pytest tail.
  4. /ship with branch feat/phase18-a1-recipe-crud-backend.
  5. Print "A1 complete — ready for /compact + Opus review." Stop.

Status: NOT STARTED.
```

---

### Chunk A2 — Recipe-edit web UI

**Branch:** `feat/phase18-a2-recipe-edit-ui`
**Scope:** ~250 LOC. The largest chunk; pre-factor `_recipe_form.py` to keep `plan.py` readable.
**Depends on:** A1 merged (CRUD primitives must exist) + B2 merged (no in-process huey in console).

**Deliverables:**
1. New `console/tabs/_recipe_form.py` — pure-fn helpers for form state
   serialization/validation (no streamlit at top level so it's unit-
   testable). `validate_recipe_form(payload: dict) -> tuple[bool, list[str]]`,
   `diff_ingredients(before: list, after: list) -> dict[str, list]` (adds/
   updates/deletes).
2. `console/tabs/plan.py`:
   - Add a "selected recipe" panel below the grid: when exactly one row
     is checked AND user clicks "Edit selected", expand a form with
     fields: title (text), base_servings (number), instructions
     (textarea), tags (`st.pills` multi or `st.multiselect` over
     `list_all_tags()` + free-text tag-add input), and an editable
     ingredients sub-grid (`st.data_editor` with `num_rows="dynamic"`,
     columns: name, qty_per_serving, unit, notes, todoist_section,
     sort_order).
   - "Save changes" button → calls `queries.update_recipe`,
     `queries.set_recipe_tags`, and the diffed ingredient mutations
     (`add_ingredient` / `update_ingredient` / `delete_ingredient`)
     in a single transaction (open one conn, pass through). Show a
     success toast.
   - "Delete this recipe" two-click confirm (mirror the existing
     `_render_clear_button` pattern), → `queries.delete_recipe`.
   - "+ New recipe" button (above grid) → blank form path; on save,
     calls `queries.create_recipe`, then redirects to edit-mode for
     the new id.
3. `meal_planner/tests/test_recipe_form_helpers.py` — new file. Tests
   for `validate_recipe_form` and `diff_ingredients` (pure fns).
4. `Mac-mini/PLAN.md` Chunk A2 status update.

**UX guardrails:**
- Edit mode is only available when **exactly one** row is checked.
  Multi-checked rows go to Send-to-Todoist as before.
- Concurrent-edit risk (Anny + Ian both editing the same recipe at
  once) is accepted as last-write-wins for V1. Don't add optimistic
  locking.
- Tag pill UI: existing tag pills above the grid are FILTER. The edit
  form's tag selector is SEPARATE — different widget keys to avoid
  state collision.

**Test gate:**
```
jobs/.venv/bin/pytest meal_planner/tests/ -q
```
Plus a manual gstack /browse smoke test (see validation below).

**Validation on mini (after merge):**
```
# In your local Claude Code session, NOT on the mini:
/browse http://homeserver:8503/?tab=recipes
# Manually:
#   1. Click ☐ on one recipe → click "Edit selected".
#   2. Change a tag, change one ingredient qty, click "Save changes".
#   3. Re-load the page → confirm change persisted.
#   4. Click "+ New recipe", fill in a test recipe "DELETE_ME_TEST",
#      save, then click delete-confirm. Verify it's gone.
#   5. Hit Send-to-Todoist on a different recipe → confirm B2 plumbing
#      still works (regression check).
```

**Sonnet kickoff prompt — paste verbatim:**
```
We are starting Phase 18 Chunk A2 of meal_planner — recipe-edit web
UI. This is the largest chunk in Phase 18; A1 (CRUD backend) and B2
(console-http route-through) MUST be merged on main before you start.
Verify with `grep -E "Chunk A1|Chunk B2" -A1 Mac-mini/PLAN.md` that
both show DONE.

Read to orient:
  1. Mac-mini/PLAN.md — Phase 18 "Chunk A2" subsection.
  2. console/tabs/plan.py — full file (POST B2 state — no `from jobs
     import huey`; uses jobs_client).
  3. console/tabs/_job_status.py — full file (style pattern for
     pure-fn helpers).
  4. meal_planner/queries.py — full file (the CRUD fns A1 added).
  5. meal_planner/models.py — full file.
  6. meal_planner/tag_categories.py — quick scan.
  7. README at meal_planner/README.md.
  8. Memory: feedback_streamlit_fragment_huey_polling.md.

Goal: ship the 4 deliverables in Chunk A2. Branch:
feat/phase18-a2-recipe-edit-ui.

Hard invariants:
  - Pure-fn form helpers live in _recipe_form.py — NO `import
    streamlit` at top level of that file. Streamlit imports go in
    plan.py only.
  - Edit mode requires exactly-one-row checked. Document this in a
    short st.caption.
  - Save path runs as a single SQLite transaction — open one conn via
    db._get_conn, pass it to the mutation fns (A1's signatures should
    accept conn=None or you'll need to extend them; if extension is
    needed, do it minimally and update A1's tests in the same chunk).
  - "+ New recipe" creates the row first, then redirects via
    st.session_state to edit mode for the new id.
  - Delete uses the two-click confirm pattern from
    _render_clear_button. 10s TTL reset.

When done:
  1. meal_planner/tests/test_recipe_form_helpers.py + full suite green.
  2. Manual /browse walkthrough recorded in journal with one screenshot
     per scenario (5 scenarios). Use the gstack /browse skill — do NOT
     use mcp__claude-in-chrome__*.
  3. PLAN.md Chunk A2 status → "DONE <date> @<sha>".
  4. /ship with branch feat/phase18-a2-recipe-edit-ui.
  5. Print "A2 complete — ready for /compact + Opus review." Stop.

Status: NOT STARTED.
```

**Chunk A2 status: DONE 2026-05-08 @6cbce5c**

Commits (stacked on A1, PR targeting feat/phase18-a1-recipe-crud-backend):
- 85c22a6 — (a) extend A1 mutations to accept conn=None (47 tests)
- 2c07712 — (b) _recipe_form.py pure helpers + 26 tests
- 98035a6 — (c) plan.py UI wiring + list_ingredients/get_recipe_tags
- 408a35c — A1 polish (Opus review nits, rebased onto A2 branch)
- 185e82a — pre-landing adversarial fixes (F8 wrong-recipe delete, F4 ghost row, F3 pop, F1 textarea clear)
- c8ca0af — Opus self-review fix 1 (F-N1 cook_time clear, F-N2 range crash, F-N3+F-N4 stale session keys; 421 tests)
- 29f9e17 — Opus self-review fix 2 (F-N5 get_recipe_tags collation NOCASE)
- 6cbce5c — Opus pre-merge review fixes #1-#6 (stale widget state bulk-pop, clear-field no-ops, NaN ingestion, KeyError UX, delete_recipe orphan tag GC; 431 tests)

---

### Chunk A3 — Sheet→DB sync script + Apps Script Sheet decommission

**Branch:** `feat/phase18-a3-sheet-decommission`
**Scope:** ~150 LOC + docs. New script + README/docs updates only.
**Depends on:** A1 + A2 merged.

**Deliverables:**
1. New `meal_planner/scripts/export_sheet_to_db.py`:
   - Pulls live Sheet via gspread (re-uses `seed_from_sheet._open_sheet`).
   - For each recipe in the Sheet: looks up by title in recipes.db. Reports:
     - in-Sheet-not-in-DB (count + titles)
     - in-DB-not-in-Sheet (count + titles, e.g. photo-intake recipes that
       were never in the Sheet — informational)
     - title-match-but-ingredient-mismatch (DB count vs Sheet count;
       prints first 3 differences)
   - Default mode: dry-run, prints diff report only.
   - With `--apply`: imports new-from-Sheet recipes via
     `queries.create_recipe` + `queries.add_ingredient` + the existing
     Gemini categorization from `seed_from_sheet`. Logs each insert.
   - Logs go to stdout AND a file at `~/Home-Tools/logs/export-sheet-<utc>.log`.
2. `meal_planner/scripts/__init__.py` (empty if missing).
3. `meal_planner/tests/test_export_sheet_to_db.py`: gspread mocked.
   Test the diff function with synthetic before/after states.
4. `meal_planner/README.md`:
   - Remove "kept as fallback through Phase 18" language.
   - Mark Apps Script Sheet as ARCHIVED (read-only) effective the
     deploy date.
   - Update the ASCII flow diagram to show recipes.db as the sole
     source-of-truth.
5. `meal_planner/legacy/apps-script/SETUP.md` — prepend a `## ARCHIVED`
   block explaining "this fallback was decommissioned in Phase 18 on
   <date>; the Sheet is read-only; edits go through
   http://homeserver:8503/?tab=recipes".
6. `Mac-mini/PLAN.md`: Phase 18 Chunk A3 status update + add a
   "Phase 18 — DONE <date>" summary block at the top of the section.

**Operator steps (manual, NOT in code; document in PLAN.md and journal):**
- Run `python -m meal_planner.scripts.export_sheet_to_db` (dry-run);
  review diff report.
- If diff is acceptable, run with `--apply`; verify recipe count in DB.
- In Google Drive UI, change the meal-planner Sheet's permissions to
  "Anyone with the link → Viewer" (no editors). Owner: Ian. Move it
  to a `_Archive/` folder.
- In `meal_planner/.env` on the mini, comment out `MEAL_PLANNER_SHEET_ID`
  and `GOOGLE_SERVICE_ACCOUNT_PATH` (or remove). Document the change
  in the journal.

**Test gate:**
```
jobs/.venv/bin/pytest meal_planner/tests/test_export_sheet_to_db.py -q
```
Plus full suite green.

**Sonnet kickoff prompt — paste verbatim:**
```
We are starting Phase 18 Chunk A3 of meal_planner — Sheet decommission.
A1 + A2 must both be merged on main before you start.

Read to orient:
  1. Mac-mini/PLAN.md — Phase 18 "Chunk A3" subsection.
  2. meal_planner/seed_from_sheet.py — full file (the Sheet read path
     to reuse).
  3. meal_planner/queries.py — full file (CRUD fns from A1).
  4. meal_planner/README.md — full file.
  5. meal_planner/legacy/apps-script/SETUP.md — full file (to prepend
     archive notice).
  6. Memory: feedback_privacy.md, feedback_mock_dryrun.md (the
     export script must default to dry-run; --apply is opt-in).

Goal: ship the 6 deliverables in Chunk A3 + write the operator-steps
checklist into PLAN.md. Branch: feat/phase18-a3-sheet-decommission.

Hard invariants:
  - The export script defaults to DRY-RUN. --apply is the explicit
    opt-in.
  - Don't run the script live in this session — the user runs it on
    the mini after merge. Tests use mocked gspread + mocked DB only.
  - The Apps Script Sheet permission change is an OPERATOR step — do
    not attempt it programmatically.

When done:
  1. New tests + full suite green.
  2. PLAN.md Chunk A3 status → "DONE <date> @<sha>" AND add the
     "Phase 18 — DONE" summary block above all 5 chunks.
  3. Journal entry with the operator-steps checklist clearly flagged
     for the user.
  4. /ship with branch feat/phase18-a3-sheet-decommission.
  5. Print "A3 complete — Phase 18 ready for /compact + Opus review.
     Operator steps for Sheet archival are in the journal." Stop.

Status: DONE 2026-05-08 @a82db46 + Opus-review fixup @7b5ab77 (wrap Gemini call in apply_imports for network resilience)
```

---

### Phase 18 done criteria

- All 5 chunks merged to main with green CI.
- `grep -r "from jobs import huey" console/` returns 0 lines.
- `lsof -p $(pgrep -f streamlit | head -1) | grep jobs.db` empty after
  console restart.
- recipes.db is sole source-of-truth; Sheet archived read-only.
- Apps Script Sheet decommissioned per README.
- nas-intake `SUBPROCESS_TIMEOUT_S` is 90; one wedged-PDF replay
  confirms escalation arms.
- Test count grew (~15 from A1 + ~5 from A2 + ~5 from A3 + ~5 from
  B1 + ~6 from B2 = ~36 new tests; 587 → ~620+).

### Cross-cutting follow-ups (not blocking Phase 18)

- Memory entry to write after B2: `feedback_streamlit_in_process_huey`
  ("never `from jobs import huey` in a long-lived Streamlit process;
   route through the HTTP server instead").
- Health-dashboard has the same WAL+streamlit pattern (4 LaunchAgent
  collectors writing to `health-dashboard/data/health.db`). Same
  vulnerability class. File a separate phase after Phase 18 ships.
- Bug 1B (split queues — `SqliteHuey(name="home-tools-jobs-bg")`) is
  deferred indefinitely. If 1A holds in production for ≥1 week without
  a starvation incident, leave it deferred. See journal-136.md for
  full tradeoff.

---

## Phase 19 — Recipe instructions (DONE 2026-05-30 ✅)

Photo intake now captures preparation instructions; Recipes tab gained
a read-only View dialog. Five chunks merged to main:

- **Chunk 1** (d5b09d5): Claude-extracted golden instructions for the
  12-photo Phase 15 bake-off corpus. `.golden.json` files extended with
  `instructions` field (gitignored per privacy rule); README schema +
  `_PHASE19_NOTES.md` document transcription provenance.
- **Chunk 2** (ef6802d): vision prompt + validator extension.
  `recipe_extraction_prompt.txt` adds `instructions: string|null` to
  the schema spec; `validate_schema()` accepts optional instructions
  (str or None) with backward-compat for missing key. Bake-off quality
  gate passed: 11/12 non-empty, 10/12 step-correct vs goldens.
- **Chunk 3** (5a42c33): `jobs/kinds/meal_planner_ingest_photo.py`
  reads `result.parsed["instructions"]`, normalizes empty/whitespace
  to None, passes to `insert_recipe(..., instructions=...)`.
- **Chunk 4** (1f78bd4): Recipes tab View dialog. New `format_view_block`
  pure helper in `_recipe_form.py`; `@st.dialog`-decorated
  `_render_view_dialog` in `plan.py`; View button next to Edit gated on
  exactly-one-row-checked. `_No instructions saved._` placeholder when
  null.
- **Chunk 5**: deployed via `bootout`/`bootstrap jobs-consumer` +
  `kickstart -kp console`. Doctor smoke-test green. `/browse`
  verification on `homeserver:8503/?tab=recipes` confirmed: View button
  renders, dialog opens with title/meta/scaled ingredients/numbered
  instructions, Close button works, placeholder shows for recipes
  without instructions.

Test count: 720 passing locally (697 baseline + 23 new), 1 pre-existing
failed (test_bake_off_cli.py subprocess-import; not Phase 19), 5
skipped, 3 xfailed.

Known model limits surfaced by the bake-off (not Phase 19 regressions):
- IMG_9962 Chicken Juk: truncated to 19 chars (4-section recipe).
- IMG_9964 Cookies: parse_fail (malformed JSON, dense multi-section).
- Two photos returned prose-format instructions instead of "1.\n2.\n"
  numbered; content correct.

Next: Phase 20 (MCP + Claude Code planning sessions) per locked sequence.

---

## Phase 19 polish — \n-split inline-numbered instructions (DONE 2026-05-30 ✅)

The vision model sometimes returned `"1. step. 2. step. 3. step."` as a
single line instead of `\n`-separated, so the View dialog rendered step 1
as an ordered-list item with steps 2-3 as continuation prose.

Commit 82b2679: added `normalize_instructions(text)` in
`meal_planner/vision/_normalize.py` using regex
`(?<=\.)\s+(?=\d+\.\s+[A-Z])` (lookbehind on the period, lookahead on
the next-step marker so only inter-step whitespace is consumed). Wired
into `normalize_extraction()` so the consumer worker gets it
automatically. Idempotent on already-newline-separated input. No false
positive on fractional measurements (`1.5 cups`) or compound numerics
(`35-40 minutes at 425F`). +9 tests.

Verified end-to-end: reset recipe id=22 (Mom's Marinated Chicken
Drumsticks), moved photo back to intake, re-ingested. Stored
instructions now contain literal `\n` between steps; View dialog
renders as proper ordered list.

---

## Phase 19.5 — recipe_book column + bulk corpus import (DONE 2026-05-30 ✅)

User-requested extension: add a "recipe book / publisher" field
distinct from the existing `source` (intake mechanism). Auto-populated
from photo intake, editable in the form, filterable in its own pill
section. Plus: bulk-insert the 11 remaining Phase 15 corpus recipes
directly into the DB.

- **Chunk 1** (98ea2b0): schema + CRUD.
  - `recipes.recipe_book TEXT NULL` via `_add_column_if_missing`
  - `Recipe.recipe_book` dataclass field with defensive `_row_to_recipe`
    fallback for pre-migration row factories
  - `insert_recipe`, `create_recipe`, `update_recipe(_UNSET sentinel)`,
    new `list_all_recipe_books()`, `search_recipes(recipe_books=...)`
    with OR-within-selection, AND-with-tag-filter, case-insensitive
  - +11 query tests
- **Chunk 2** (no commit; one-shot script): bulk-insert 11 corpus
  recipes via `meal_planner.eval.recipe_photos_processed/*.golden.json`
  + `_insert_ingredients_batch`. Recipe-book attribution determined
  by Claude inspecting each photo: 6 Family, 1 Urvashi Pitre, 1 Mark
  Bittman, 1 NYT Cooking, 1 Serious Eats, 1 The Pioneer Woman.
  Retroactively backfilled id=22 (Mom's Marinated Chicken Drumsticks)
  with recipe_book="Family". Deleted 2 stub dupes (id=17, 18) per user
  confirmation; kept ids 19-21 (3 PDFs handled in follow-up).
- **Chunk 3** (4d00239): UI surface.
  - `format_view_block` renders "From: <recipe_book>" first in meta line
  - Edit form gains `Recipe book` text input with placeholder
  - "Recipe book" pill section below tag pills
  - +2 view-dialog tests
- **Chunk 4** (d79d9f3): vision pipeline auto-populates recipe_book.
  - Prompt schema gains `recipe_book: string|null` with rules paragraph
    instructing the model to identify bylines/headers/cookbook titles
    or default to "Family" for handwritten/personal
  - Validator + worker mirror the instructions-field plumbing from
    Phase 19 Chunk 2/3
  - +4 validator tests, +3 ingest persistence tests
- **Chunk 5** (deploy): mini `git pull` + `bootout`/`bootstrap`
  jobs-consumer + `kickstart -kp` console. `/browse` verification:
  6 Recipe-book pills render; filtering to "Urvashi Pitre" narrows the
  grid to the single matching recipe; View dialog meta line shows
  "From: Urvashi Pitre · Source: claude-corpus-import · …".

Total +29 tests in Phase 19.5; 750 passing locally after Chunk 4.

---

## Phase 19 followups (DONE 2026-05-30 ✅)

Polish surfaced during/after the Phase 19.5 verification:

- **3263ba1 — qty_raw fallback in View dialog.** Range qtys ("2-3",
  "8-10", "1/3-1/2") stored `qty_per_serving=NULL` + `qty_raw=<str>`
  but the View dialog only read `qty_per_serving` and silently dropped
  the qty entirely. Added `Ingredient.qty_raw: str | None = None`
  dataclass field, populated in `list_ingredients`, used as fallback
  in `format_view_block`. +3 tests.
- **No commit — bulk-replace 3 orphan PDFs via Claude.** Ids 19-21
  (Pot Pie, Orzo Risotto, Nanaimo Bars) had stub data from earlier
  photo-pipeline runs. scp'd the source PDFs from the mini's NAS,
  Claude read each PDF, deleted the stubs and re-inserted with full
  data (`source="claude-pdf-import"`, `recipe_book="Serious Eats"`,
  proper `cook_time_min`/`base_servings`, complete ingredients +
  numbered instructions). New ids: 34, 35, 36.
- **0947ee6 — fix test_bake_off_cli pre-existing failure.** Subprocess
  used `sys.executable` without setting PYTHONPATH, so when run by
  Python interpreters without the repo on sys.path (system Python
  3.14 on the laptop) the subprocess failed with
  `ModuleNotFoundError: No module named 'meal_planner'`. Fixed by
  prepending repo root to PYTHONPATH for the subprocess env. **Suite
  now fully green: 754 passed, 0 failed, 5 skipped, 3 xfailed** —
  first 0-failure run since the bake-off CLI tests landed.

---

## Phase 20 — Instacart deeplinks (ON HOLD — DO LATER)

**Status:** scoped 2026-05-30, held pending real-world user testing to
nail down the output format. The original "MCP + Claude Code planning
sessions" Phase 20 was put on hold indefinitely; this Instacart work
inherits the Phase 20 slot.

**Goal:** parallel "Send to Instacart" button alongside the existing
"Send to Todoist" on the Recipes tab. Generates per-ingredient
deeplinks of the form
`https://www.instacart.com/store/s?k=<urlencoded ingredient>` so the
user (on iPhone, with the Instacart app installed) can tap each link
to add items to their Instacart cart.

**Locked decisions (from 2026-05-30 scoping):**
- Single-item search deeplinks. NOT the Shoppable Recipe API
  (requires Instacart developer signup + likely business verification)
  and NOT headless-browser automation (ToS risk, 2FA fragility).
- Keep both Send-to-Todoist and Send-to-Instacart in parallel. Todoist
  is not deprecated.
- **Deeplinks must be permanently stored in the database and editable
  by the user.** Added 2026-05-30: a user-supplied override per
  ingredient (e.g. search for "Kikkoman low-sodium soy sauce" instead
  of just "soy sauce") means the deeplink isn't computed fresh every
  time from the ingredient name — it's a stored field.

**Why on hold:** the right output UX needs hands-on testing. Open
questions that can't be answered without real-world shopping trips:
- Per-ingredient column on `ingredients` (one override per recipe-
  ingredient junction) vs. a global `ingredient_deeplinks` table
  keyed on canonical ingredient name vs. both layers (per-recipe
  override falling back to global)?
- Output surface — modal dialog with tappable list? Todoist task
  with embedded URLs (since Anny already has Todoist on her phone)?
  A new bookmark-friendly Instacart-cart page at
  `homeserver:8503/instacart`?
- Mobile-first or desktop-with-mobile-followup? Deeplinks only work
  on phones where Instacart is installed.
- Dedup semantics when 3 recipes have "olive oil" — collapse to one
  link, or keep one per recipe (so per-recipe overrides survive)?

**Reactivate after** using the current Todoist-only flow for a few
real grocery trips to identify the friction Instacart needs to remove.

---

## Phase 21 — iPhone recipe intake via the console Capture tab

**Status:** DONE (code + tests) 2026-05-30; pending deploy + iPhone
dogfood on the mini. See `Mac-mini/shortcuts/iphone-capture-homescreen.md`
for the home-screen setup walkthrough.

**Final shape (v2 — supersedes the v1 Apple Shortcut path):**
- iPhone Safari → Add to Home Screen pointed at
  `http://homeserver:8503/?tab=capture`. Tap the icon → upload widget
  → pick intent (save / save_and_shop / shop_only) → wait ~10 s for
  the result. No Apple Shortcut to build per device.
- Streamlit (the existing console at `:8503`) calls
  `meal_planner.runner.process_iphone_intake_sync` **directly** — no
  huey enqueue, no polling. User sees a definitive result on the
  upload page.
- Gemini 2.5 Flash for extraction (free tier, env `GEMINI_API_KEY`
  in `meal_planner/.env`). HEIC photos go through verbatim.

**Why v2 replaced v1:** v1 used an Apple Shortcut posting multipart to
`POST /iphone-intake` on `jobs-http:8504`. The user prefers a
dashboard upload (more discoverable, same access pattern as the health
dashboard, no per-device Shortcut to maintain). v1 commits
(`c467e53 → 5b2d3bc`) remain on main; v2 strips the HTTP multipart
endpoint and Shortcut doc.

**Structural fix landed alongside v2:**
- The Streamlit tab cannot import `jobs.huey` (memory rule
  `feedback_streamlit_in_process_huey.md` — opening `jobs.db` holds
  an orphan WAL fd and silently drops enqueues from other processes).
- The intake + Todoist-send sync helpers moved out of `jobs/kinds/*.py`
  into `meal_planner/runner.py`. The `@huey.task()` decorators in
  `jobs/kinds/` stayed (so the kinds still register if anything
  enqueues via `/jobs`), but their bodies are now one-line calls into
  the runner.
- `jobs/adapters/todoist.py`'s body was duplicated to
  `meal_planner/todoist_client.py` so the runner could call it
  without triggering `jobs/__init__.py`. The original adapter is
  unchanged; tests retargeted.

**Deploy when ready:**
1. SSH to mini, `cd ~/Home-Tools && git pull`.
2. Restart both services (each `-k` is load-bearing —
   `feedback_launchctl_kickstart_k_flag.md`):
   ```
   launchctl kickstart -kp gui/$UID/com.home-tools.jobs-consumer
   launchctl kickstart -kp gui/$UID/com.home-tools.console
   ```
   Consumer restart picks up the new kind body
   (`feedback_huey_kind_module_reload.md`); console restart picks up
   the new Capture tab (Streamlit under launchd doesn't auto-reload).
3. From a laptop, browse `http://homeserver:8503/?tab=capture` and
   confirm the upload widget renders. Upload a test recipe with
   intent=save.
4. On each iPhone, follow `Mac-mini/shortcuts/iphone-capture-homescreen.md`
   to add the home-screen icon. Test all three intents end-to-end:
   - **save_and_shop** → recipe appears on the Recipes tab AND
     ingredients land in Todoist Grocery List.
   - **shop_only** → ingredients land in Todoist; the Recipes tab is
     unchanged (the temp recipe row was deleted after the Todoist
     push completed).
5. **Verify the huey wedge is gone:**
   ```
   ssh homeserver@homeserver \
     "lsof -p \$(pgrep -f 'streamlit run.*app.py') 2>/dev/null | grep -i jobs.db" \
     || echo "streamlit not holding jobs.db — wedge clear"
   ```

---

## Phase 22 — Two-lane huey (DONE 2026-05-30 ✅)

**Status:** shipped on one branch, six commits
(`feat/phase22-two-lane-huey`). Design source:
`~/.claude/plans/phase-22-huey-fast-lane.md`; auto-mode execution log:
`~/.claude/plans/we-re-goign-to-fully-distributed-kay.md`.

**Contention test (the proof point):** with `_debug_sleep(120)` holding
Worker-1 of the slow lane (`com.home-tools.jobs-consumer`), enqueued
`meal_planner_clear_todoist` on the fast lane via `POST /jobs`.
Result returned in **7.16 s** (vs. ~120 s if the lanes had been
contended) — fast-lane consumer was idle and picked it up immediately.
`_debug_sleep` was deleted from the branch (commit history retains
the trace but main has no probe file).

**What landed:**

- `jobs/__init__.py` — new `huey_fast = SqliteHuey(name="home-tools-jobs-fast", filename=~/Home-Tools/jobs/jobs-fast.db)`; `JOBS_FAST_DB_OVERRIDE` env var for test isolation.
- 4 user-initiated kinds swapped to `from jobs import huey_fast as huey`:
  `meal_planner_send_to_todoist`, `meal_planner_clear_todoist`,
  `event_aggregator_decide`, `meal_planner_iphone_intake`.
- `jobs/enqueue_http.py` — `/queue-size` returns both `size` + `size_fast`; `/kinds` adds `lane` field; `/jobs/<id>` tries both lanes.
- `jobs/run-consumer-fast.sh` + `jobs/config/com.home-tools.jobs-consumer-fast.plist` — second LaunchAgent.
- `jobs/install.sh` — chmod + plist install loop include the new agent.
- `service-monitor/services.py` + `flowchart.py` — surface the fast-lane consumer.
- 162 jobs tests + 430 jobs+meal_planner total pass.

**Problem:** Single huey worker (`-w 1 -k thread`) serves both
background batch work (nas_intake_scan, restic, periodic pollers) AND
user-initiated foreground work (Recipes tab Send/Clear, Decisions tab
approve/reject). One long-running periodic task starves every
interactive click. 2026-05-07 saw a 25-min wedge behind a healthcare-
PDF OCR. Phase 18B1's `SUBPROCESS_TIMEOUT_S=90` reduces the worst case
but doesn't fix the architecture.

**Approach:** Two huey instances on two consumers, no priority
sort, no preempt.

- New `huey_fast = SqliteHuey(name="home-tools-jobs-fast", filename=...)`
  in `jobs/__init__.py`, backed by `~/Home-Tools/jobs/jobs-fast.db`.
- 4 user-initiated kinds opt into `@huey_fast.task()`:
  `meal_planner_send_to_todoist`, `meal_planner_clear_todoist`,
  `event_aggregator_decide`, `meal_planner_iphone_intake` (wrapper).
  Everything else stays on default `huey`.
- New LaunchAgent `com.home-tools.jobs-consumer-fast` runs a second
  consumer process. Same env loading + keychain unlock as the
  existing wrapper.
- `enqueue_http.py` gains lane awareness on `/jobs/<id>` (tries both
  instances), `/queue-size` (returns both), `/kinds` (adds `lane`
  field). `_registered_kinds()` is already lane-agnostic — no change
  there.
- iPhone Capture is unchanged (already sync-in-process; bypasses
  huey).

**Rejected during scoping:**
- Three-tier priority sort via custom `dequeue` wrapper — huey 3.x
  has no native priority support, monkey-patching `SqliteStorage` is
  brittle, and the wedge frequency (~1/month) doesn't justify it.
- True preempt — would require cooperative-yield hooks in every
  long-running kind. Too much surface area.
- A third lane for restic/backups — fits on the existing slow lane.

**Out of scope:** multiple workers per lane (`@requires_model` lock
assumes `-w 1`); per-kind RAM caps / cgroups.

**Deploy:** restart all four services after merge (jobs-consumer,
new jobs-consumer-fast, jobs-http, console) per
`feedback_huey_kind_module_reload.md` +
`feedback_jobs_http_restart_on_module_change.md`. Then run the
contention test (long task running on slow lane while clicking Send
to Todoist).

---

## Phase 23+ — Future chunks (numbered as each chunk is claimed)

Each chunk gets the next sequential Phase number when claimed.
Numbers are not pre-allocated.

**Deferred polish ideas surfaced during Phase 19 work** (not committed
scope — pick up when ready):
- Render `0.25 tsp` as `1/4 tsp` (and similar fraction-friendly values)
  in the View dialog. Currently `_fmt_qty` uses Python `:g` which
  emits decimals. Common ratios (1/4, 1/3, 1/2, 2/3, 3/4) could round-
  trip back to fraction strings.
- Re-ingest the 3 Serious Eats PDFs via the live photo pipeline once a
  cleaner intake path exists (Phase 21) so they have `photo_path`
  populated (right now `source=claude-pdf-import` means they were
  inserted directly).

## Long-term future scope (re-evaluate later)

- **Re-enable recipe consolidation as an opt-in feature** — V0 sends raw
  scaled lines per recipe (Phase 14.10). Future phase: add a `Consolidate`
  checkbox on the Recipes tab; when set, call
  `meal_planner.consolidation.consolidate_for_grocery` with proper success
  indication, quota awareness, and partial-failure UX. Side-fix the
  `if resp` `Response.__bool__` bug in `consolidation.py:77-83` at the same
  time (prints `Gemini HTTP ?: <empty>` for 4xx/5xx because `if resp` returns
  False for non-2xx; should print `resp.status_code` and `resp.text[:200]`
  unconditionally).
- ~~**Job priority tiers (huey queue overhaul)**~~ — **PROMOTED to Phase 22**
  on 2026-05-30. Three-tier sort was rejected in favor of a simpler
  two-lane huey design. See "Phase 22" section above and full plan at
  `~/.claude/plans/phase-22-huey-fast-lane.md`.
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
