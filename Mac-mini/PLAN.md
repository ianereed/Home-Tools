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

## Phase 7 — Backup (NEXT)

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

### Decision: target = NAS (decided 2026-04-30, journal-29)

Backup target is the iananny NAS (192.168.4.39) Share1 already mounted at
`~/Share1`. Not an external SSD. Not B2/Wasabi initially. Rationale:

- It already exists, already credentialed, already mounted.
- 3-2-1 isn't fully met with NAS-only (still on the same LAN as the mini),
  but it's a strong first leg — protects against mini SSD failure, OS
  reinstall, accidental `rm -rf`. Off-site (B2/restic) can be added later
  as a second leg without redoing the first.
- Phase 5d already proved NAS reachability + TCC + autofs-style remount
  patterns work; Phase 7 doesn't have to re-solve that.

### Scope

1. **Time Machine to NAS (SMB target)** — system-native, encrypted, hourly.
2. **`restic` to NAS** for the priority-list files at higher cadence — hourly
   for `health.db`, daily for the rest. Repo password in keychain
   (`restic-backup`/`password`).
3. **Test a restore.** Pick one file, restore it to a scratch dir, diff.
   Untested backups aren't backups.
4. **Exclude:** `.venv/` directories, `__pycache__/`, `.git/`,
   `~/.ollama/models/**` (re-pullable).
5. **Off-site (Phase 7.5, deferred):** add B2/Wasabi as second leg if/when
   desired.

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

## Phases 10–11 — Deferred

- **BlueBubbles iMessage bridge** — requires iCloud sign-in on the mini.
  Defer until we actually want iMessage-based control.
- **Hermes Agent / OpenClaw evaluation** — couldn't verify OpenClaw in 2026
  web searches; treat both as needing real-world provenance audit before
  installing. Finance/dispatcher work fine without an agent framework.

The CEO-approved Tier-2 LLM orchestrator (P0+P1, strangler-fig) is the
medium-term direction past Phase 7 and the Mini Jobs queue + console (Pick 1,
agreed-next per `reference_macmini_brainstorm.md`). Full design at
`future-architecture-upgrade.md`.

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
