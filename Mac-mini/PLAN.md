# Mac mini Home Server — Working Plan

Living plan for the ongoing build. Update as phases advance. Pair this with
`Mac-mini/README.md` (the state-of-the-project page) and the
`.claude/projects/.../memory/` entries (the accumulated lessons).

---

## Quick status (as of 2026-04-24)

Phases 0–5 complete. Event-aggregator + health-dashboard both running on the
mini under launchd at `~/Home-Tools/<project>` with `.venv`-based LaunchAgents.
iPhone Health Auto Export now posts to `http://homeserver:8095/` over
Tailscale. Event-aggregator's staging dir is at
`~/Home-Tools/event-aggregator/staging/` (moved out of TCC-protected
`~/Documents/` 2026-04-22). Laptop's duplicate event-aggregator LaunchAgent
is unloaded and renamed `.disabled`; mini is now the sole writer to
Google Calendar. Medical-records and meal-planner stay on the laptop (user
decision — medical-records Slack migration deferred, meal-planner is Apps
Script).

Phase 8 (finance-monitor) Phases 1 + 2 are LIVE on the mini (2026-04-24):
Slack bot DMs locked to `ALLOWED_SLACK_USER_IDS` with 60s per-user rate limit;
read-only YNAB API sync runs every 5 min from the watcher LaunchAgent
(transactions + monthly category snapshots in new `budget_months` table); CSV
imports retired from 2026-04-24 forward. Phases 6, 7, and 8 Phase 3+ remain.

See `README.md` for the full status table and running services.

---

## Resume from here

**Next single action**: Phase 6 — minimal failure monitoring. Pushover (or
ntfy) + a shared `notify.sh` that each LaunchAgent calls on non-zero exit.
Detailed steps in Phase 6 below.

Before touching anything, run these to confirm the server is still healthy:

```bash
ssh homeserver@homeserver '
  tailscale status | head -3
  launchctl list | grep -E "ollama|event-aggregator|health-dashboard"
  sudo lsof -iTCP:11434 -sTCP:LISTEN -n -P
  sudo lsof -iTCP:8095 -sTCP:LISTEN -n -P
'
```

Expected: tailscale connected, Ollama + event-aggregator + 4 health-dashboard
LaunchAgents registered with clean exit status, Ollama on `127.0.0.1:11434`,
receiver on `*:8095`.

---

## Phase 5b — Port health-dashboard (DONE 2026-04-22)

Health-dashboard is live on the mini. Receiver on port 8095, collect at
7:00/7:20, intervals-poll every 5 min, staleness at 7am/9pm. iPhone posts
to `http://homeserver:8095/` over Tailscale. Laptop plists renamed to
`*.plist.disabled` so they don't auto-load. Records kept below for future
reference / if we ever port a similar project.

### Gotchas encountered during the port

- **Login keychain not reachable from LaunchAgents.** `homeserver`'s aqua
  session on this headless mini never got the interactive login that
  auto-unlocks the default keychain. Symptoms: `keyring.get_password`
  returns `errSecAuthFailed` (security CLI exit 152) from within a
  LaunchAgent, even though it works from an SSH shell and even though the
  keychain *is* in the search list. Fix: recreate `login.keychain-db` with
  empty password (`security create-keychain -p ""`), set no-auto-lock, and
  have the shim in `collectors/__init__.py` explicitly unlock it on import.
- **keyring>=25 ignores `Keyring.keychain`.** Even after fixing unlock,
  `keyring` can't be pointed at a specific keychain any more (upstream
  issue #623). The shim works around this by monkey-patching
  `keyring.get_password` to shell out to `security` with `KEYCHAIN_PATH`.
- **Keychain migration needed explicit target.** `security add-generic-password`
  from SSH writes to `System.keychain` (root-only → "Write permissions
  error") unless you pass the target keychain as the final positional arg.
  The same is true on the mini; the default-keychain `-d user` setting
  exists in the user preference domain but doesn't propagate to the
  Security framework calls from ssh.

### Why it wasn't trivial

- Health-dashboard ships **4 plists** (collect, intervals-poll, receiver,
  staleness), not 1. All must install cleanly.
- May have its own `requirements.txt` + credential files; treat it like a
  fresh project, not a quick re-run of event-aggregator.

### Steps (execute on the mini via SSH)

1. **Sanity-read the existing memory and code**:
   ```bash
   ssh homeserver@homeserver '
     ls ~/Home-Tools/health-dashboard/
     cat ~/Home-Tools/health-dashboard/README.md 2>/dev/null || true
     ls ~/Home-Tools/health-dashboard/config/
   '
   ```
   Read `project_health_dashboard.md` memory before proceeding.

2. **Path-cleanup check** (plists were part of the earlier sed sweep, but
   verify nothing in health-dashboard still references the wrong paths):
   ```bash
   grep -r '/Users/homeserver/Documents/GitHub' ~/Home-Tools/health-dashboard 2>/dev/null
   grep -r '/Users/ianreed' ~/Home-Tools/health-dashboard 2>/dev/null
   # Both should return nothing.
   ```

3. **Clear any stale bytecode** (the earlier sed corrupted `.pyc` files —
   this will have done the same to health-dashboard):
   ```bash
   find ~/Home-Tools/health-dashboard -type d -name __pycache__ -exec rm -rf {} +
   ```

4. **Build the venv**:
   ```bash
   cd ~/Home-Tools/health-dashboard
   uv venv --python 3.12
   source .venv/bin/activate
   uv pip install -r requirements.txt
   ```

5. **Migrate credentials / .env from laptop** (same scp pattern as
   event-aggregator, only what's needed):
   ```bash
   # FROM laptop:
   cd ~/Documents/GitHub/Home-Tools/health-dashboard
   ls .env credentials/ 2>/dev/null   # see what exists
   scp .env homeserver@homeserver:~/Home-Tools/health-dashboard/
   scp -r credentials homeserver@homeserver:~/Home-Tools/health-dashboard/ 2>/dev/null || true
   # Back on mini:
   ssh homeserver@homeserver 'cd ~/Home-Tools/health-dashboard && chmod 600 .env credentials/*.json 2>/dev/null'
   ```

6. **Smoke-test on the mini via SSH shell** (not launchd):
   ```bash
   cd ~/Home-Tools/health-dashboard
   source .venv/bin/activate
   python -c "import main" || python -m py_compile *.py  # adapt to actual entrypoint
   # Run whatever equivalent of --mock/--dry-run exists (may differ from event-aggregator).
   ```
   If there's no mock mode, skip the smoke test and trust the LaunchAgent
   install step.

7. **Apply the outstanding fixes** noted in `project_health_dashboard.md`
   memory (3 remaining steps). Resolve those before loading any plist —
   they're the reason this project hasn't been running already.

8. **Install the LaunchAgents** (4 of them). Health-dashboard may or may not
   ship an `install_scheduler.sh`. If it does, activate the venv first and
   run it. If not, copy the plist files to `~/Library/LaunchAgents/` and
   `launchctl load` each one, rewriting the Python path to
   `<project>/.venv/bin/python3`.

9. **Verify**:
   ```bash
   launchctl list | grep health-dashboard
   ls -la /tmp/home-tools-health-dashboard*.log
   ```
   PID `-` + exit status `0` + nonzero log sizes after first fire = success.

### Known gotchas to watch for

- **Empty log + Python `S` state for minutes** → TCC hang. Move whatever
  path is blocked out of `~/Documents`, `~/Downloads`, `~/Desktop`, etc.
  (Shouldn't happen since we're already at `~/Home-Tools`, but stay alert
  for any code that writes to `~/Documents/whatever`.)
- **`bad marshal data` on import** → stale `.pyc` from the earlier sed pass.
  `find ... -name __pycache__ -exec rm -rf {} +`.
- **`launchctl list` shows non-zero exit status** → always read the error
  log first. It may be Python logging at INFO (stderr by default) — cosmetic
  — or actual traceback.

### Skip meal-planner

Meal-planner is Google Apps Script + Gemini cloud. Nothing to run on the
mini. Drop from Phase 5 scope; the laptop will continue to deploy Apps
Script updates.

---

## Phase 6 — Minimal monitoring

Goal: get a phone ping when any LaunchAgent fails, without building
dashboards. With 6 agents now running (ollama, event-aggregator, and the
5 health-dashboard agents), silent failures have a higher cost.

### Scope

1. **Pick a push channel.** Pushover ($5 one-time per device, single API
   POST with curl) or self-hosted ntfy (free, needs a public endpoint —
   can ride on Tailscale HTTPS funnel for free). Pushover is faster to set
   up for a single user; ntfy is the path if we ever add multi-user or
   want Slack-like channels. **Recommend: Pushover, defer ntfy.**

2. **Store the Pushover creds in the login keychain** using the same
   pattern we set up for health-dashboard:
   - Services: `pushover-mac-mini` / accounts: `app_token`, `user_key`
   - Write via `security add-generic-password -U ... ~/Library/Keychains/login.keychain-db`
     (see `reference_mac_mini_porting_checklist.md`)

3. **Write `~/Home-Tools/bin/notify.sh`** — one shared script, reads creds
   via `security find-generic-password -w`, POSTs to
   `https://api.pushover.net/1/messages.json`. Takes args: title, message,
   priority (default 0, 1=high for failures).

4. **Wrap each LaunchAgent** with a `trap`-ing shell script so non-zero
   Python exits trigger `notify.sh`. Rather than edit each plist's
   `ProgramArguments`, introduce a single wrapper: `~/Home-Tools/bin/run-agent.sh`
   that runs `"$@"` and on failure sends the tail of the log via notify.
   Update the 6 plists to invoke the wrapper.

5. **Heartbeat / liveness check** — a new LaunchAgent that runs every
   30 min and checks:
   - `launchctl list | grep health-dashboard` — all 5 present
   - `curl -sf http://127.0.0.1:8095/` — receiver responding
   - `curl -sf http://127.0.0.1:8501/` — streamlit responding
   - `curl -sf http://127.0.0.1:11434/api/tags` — ollama responding
   - health.db `mtime` is <25h old (detects stuck receiver even when the
     process is "running")
   Any failure → one Pushover alert. Suppress repeats with a lockfile so
   a stuck receiver doesn't page every 30 min.

6. **Weekly SSH-failure digest** as a LaunchAgent, once per week:
   `log show --predicate 'process == "sshd"' --last 7d | grep -i "failed\|invalid"`
   → pipe to notify.sh (low priority).

7. **Port-audit reminder** — not automated; calendar reminder to run
   `sudo lsof -iTCP -sTCP:LISTEN -n -P` and diff against the expected
   baseline (sshd, screensharing, ollama, 8095, 8501, utun*). Anything
   unexpected → investigate.

### What to skip unless actually needed

- iStatistica / Stats menu bar apps (can't see them — headless)
- Uptime Kuma / Netdata dashboards (the streamlit page already gives us
  eyes-on-glass when we want it)
- Structured log shipping (Elastic/Loki etc. — overkill for 6 agents)

---

## Phase 7 — Backup

Goal: 3-2-1 backup for the mini so we can recover from disk failure or
ransomware. Now that `health.db` is the authoritative copy (laptop's DB is
frozen at the 2026-04-22 cutover), losing it = re-scraping from Intervals +
Strava APIs, which only cover recent data. Protect it.

### What actually matters to protect

- `~/Home-Tools/health-dashboard/data/health.db` (91MB, active)
- `~/Home-Tools/event-aggregator/*.db` or similar state
- `~/Library/Keychains/login.keychain-db` (7 health-dashboard secrets;
  reproducible but a pain to re-migrate)
- `~/Library/LaunchAgents/com.*.plist` (6 files — reproducible from repo)
- Future `~/Home-Tools/finance-monitor/**` data once Phase 8 lands

### Scope

1. **Local: Time Machine** to an external SSD or SMB share on a NAS:
   - System Settings → General → Time Machine → Add Backup Disk
   - Check "Encrypt backups" (critical)
   - Leave on automatic hourly schedule
2. **Off-site: restic** to B2 / Wasabi / S3. Restic is free, well-audited,
   has good macOS support. Run as a LaunchAgent at 03:00 daily.
   - Repository password goes in the login keychain (new entry:
     `restic-<project-backup>/password`)
   - Initial backup may take hours; let it run overnight
   - Daily incremental after that
3. **Test a restore.** Pick one file, restore it to a scratch dir, diff.
   Untested backups aren't backups.
4. **Exclude** from both: `.venv/` directories, `__pycache__/`, `.git/`
   (optional, git lives on GitHub anyway), large model weights under
   `~/.ollama/models/**` (redownloadable via `ollama pull`).

### Open question

- Do we have a NAS available for SMB-target Time Machine? If not, buy a
  cheap external SSD (~$50 for 1TB) and skip SMB. Or skip Time Machine
  entirely and rely on restic to B2 for everything. **Revisit when we
  start Phase 7** — don't buy hardware speculatively.

---

## Phase 8 — Finance automation (Phases 1 + 2 LIVE 2026-04-24)

Work at `~/Home-Tools/finance-monitor/`. Two LaunchAgents running on the mini:
KeepAlive Slack bot + 5-min interval watcher (which now also runs YNAB API
sync at the top of each cycle).

### Phase 1 — Local Q&A (DONE 2026-04-23, hardened 2026-04-24)

- YNAB CSV export ingestion (`ingest/ynab_csv.py`) → SQLite — kept for
  historical CSVs; retired as a live source 2026-04-24
- PDF ingestion via pdfplumber (`ingest/pdf_importer.py`) for advisor plan docs
- Image OCR ingestion via qwen2.5vl:7b (`ingest/image_importer.py`)
- Plain-English Q&A engine (`query_engine.py`) — routes to transaction or
  document mode, calls qwen3:14b locally
- Slack DM bot (`slack_bot.py`) — Socket Mode, DMs only, dedicated Finance Bot
  Slack app. **Locked down 2026-04-24:** `ALLOWED_SLACK_USER_IDS` allowlist
  in `.env` rejects unauthorized senders; 60s per-user rate limit; sender ID
  in audit logs. Startup warns if allowlist is empty.

### Phase 2 — Read-only YNAB API sync (DONE 2026-04-24)

- `ingest/ynab_api.py` — `YnabClient` exposes ONLY `.get()`. Read-only is a
  hard requirement; never add write methods. Sync handles delta via YNAB's
  `last_knowledge_of_server` cursor, monthly category snapshots into a new
  `budget_months` table, deleted-transaction propagation, and auto-discovery
  of the single budget ID.
- New SQLite tables: `budget_months` (per-month per-category budgeted /
  activity / balance) and `sync_state` (cursor + flags).
- New env vars in `.env`: `YNAB_API_TOKEN` (PAT from
  https://app.ynab.com/settings/developer), `YNAB_BUDGET_ID` (optional,
  auto-discovered), `YNAB_API_CUTOFF=2026-04-24` (the date API takes over from
  CSV imports). One-time CSV cleanup deletes `ynab_csv` rows dated ≥ cutoff.
- Wired into the existing 5-min watcher LaunchAgent. Sync `never raises`,
  so YNAB outages don't kill file intake. Manual run: `python main.py sync`.
- One-off backfill on 2026-04-24 pulled the single 2026-04-23 transaction
  that was made after the final CSV export.

**To use:**
- DM the Finance Bot in Slack with any question: "How much did I spend on
  restaurants last month?"
- To analyze the advisor plan: "Analyze the portfolio allocation in the
  financial plan"
- CLI test: `python main.py ask "What were my top 5 categories this month?"`
- Stats: `python main.py stats` (now includes by-source counts + month count)

### Phase 3+ — Future

- Amazon order reconciliation via Gmail API
- Daily/weekly spending digests via Pushover
- Anomaly detection

### Security controls (current state)

- YNAB PAT in `.env` (PATs have full read+write at the YNAB level; read-only
  is enforced **client-side** by `YnabClient.get()` being the only HTTP method)
- Slack tokens in `.env` (consistent with rest of project)
- Slack DM allowlist via `ALLOWED_SLACK_USER_IDS`; rate-limited; audit-logged
- No LangChain (active critical CVEs: CVE-2025-68664 CVSS 9.3, CVE-2024-36480 CVSS 9.0)
- All data local (SQLite on mini, Ollama on `127.0.0.1:11434`)
- Slack bot DM-only — never posts to channels

---

## Phase 9 — Slack UX split (dispatcher bot)

**Status:** Code complete 2026-04-24 (`Home-Tools/dispatcher/`). Pending user
actions before it goes live on the mini.

**What shipped** (see plan at
`~/.claude/plans/we-are-going-to-ancient-platypus.md`):

- New project `dispatcher/` — long-running Socket Mode bot that listens in
  `#ian-event-aggregator` (interactive commands) and `#ian-image-intake` (file
  uploads). Routes images locally via qwen2.5vl:7b, drops financial docs into
  `finance-monitor/intake/`, and invokes `event-aggregator main.py
  ingest-image` for event-type files.
- New CLI subcommands on event-aggregator: `classify`, `ingest-image`,
  `approve`, `reject`, `add-event`, `status`, `query`.
- Cloud fallback (Gemini) **removed** from event-aggregator. All intake is
  local-only now. PDF rasterization via `pypdfium2` added so PDFs still work
  without the cloud path.
- finance-monitor watcher extended to OCR images via qwen2.5vl (`ingest/image_importer.py`).
- Old Slack file scanner retired (`connectors/slack.py:fetch_files` deleted).

**User actions required (mini):**

1. Create `#ian-image-intake` in Slack.
2. Create a new Slack app "Home Router Bot" — Socket Mode on, App-Level Token
   with `connections:write`, Bot Token with scopes: `channels:history,
   channels:read, groups:history, groups:read, chat:write, files:read,
   reactions:write`. Install and invite to both channels.
3. Add tokens to the mini's login keychain:
   ```bash
   security add-generic-password -U -s dispatcher-slack -a app_token \
     -w "xapp-..." ~/Library/Keychains/login.keychain-db
   security add-generic-password -U -s dispatcher-slack -a bot_token \
     -w "xoxb-..." ~/Library/Keychains/login.keychain-db
   ```
4. `git pull` on the mini. `cd ~/Home-Tools/dispatcher && bash install.sh`.
5. Set `ALLOWED_SLACK_USER_IDS` in `dispatcher/.env` to your Slack user ID.
6. Pull the same ID into `event-aggregator/.env` too — `pypdfium2` must be
   installed in the event-aggregator venv: `source .venv/bin/activate && pip
   install -r requirements.txt`.
7. `launchctl list | grep dispatcher` → PID + exit 0, then `tail -f
   /tmp/home-tools-dispatcher.log` to confirm the bot is connected.

**Verification (no real data — per privacy rule):**

- Post `help` in `#ian-event-aggregator` → bot replies with command list
- Post `status` → JSON summary appears within a few seconds
- Drop a harmless test image in `#ian-image-intake` → bot posts "Received…
  classifying" ack, then a routing decision
- Confirm no outbound Google traffic: `ssh homeserver 'sudo lsof -iTCP
  -sTCP:ESTABLISHED | grep -i google'` while an upload processes

---

## Phase 10–11 — Deferred

- **BlueBubbles iMessage bridge** — requires signing into iCloud on the
  mini. Defer until we actually want iMessage-based control of the finance
  monitor.
- **Hermes Agent / OpenClaw evaluation** — original research treated these
  as existing; I was unable to verify OpenClaw at all in 2026 web searches
  (likely prior-context hallucination). Before installing either, do a
  real-world verification pass. Finance automation works fine without an
  agent framework; this is optional polish.

---

## Reference

- `Mac-mini/README.md` — current state, running services, key decisions
- `Mac-mini/original-context.rtf` — original planning conversation (Apr 19–21)
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

## Post-cutover follow-ups

**Scheduled for 2026-04-29** (7-day rollback window closes). If no issues
have surfaced on the mini by then, delete the following laptop-side
rollback artifacts:

- `~/Library/LaunchAgents/com.health-dashboard.receiver.plist.disabled`
- `~/Library/LaunchAgents/com.health-dashboard.collect.plist.disabled`
- `~/Library/LaunchAgents/com.health-dashboard.intervals-poll.plist.disabled`
- `~/Library/LaunchAgents/com.health-dashboard.staleness.plist.disabled`
- `~/Library/LaunchAgents/com.home-tools.event-aggregator.plist.disabled` (added 2026-04-22 when the laptop instance was shut down to stop the split-brain with the mini)
- `~/Documents/GitHub/Home-Tools/health-dashboard/data/health.db` (91MB frozen snapshot)
- 7 laptop Keychain entries under services `health-dashboard-strava`,
  `health-dashboard-intervals`, `health-dashboard-garmin` (mini has its own
  copies)

Cleanup one-liner to run 2026-04-29 or later:

```bash
rm /Users/ianreed/Library/LaunchAgents/com.health-dashboard.*.plist.disabled
rm /Users/ianreed/Library/LaunchAgents/com.home-tools.event-aggregator.plist.disabled
rm /Users/ianreed/Documents/GitHub/Home-Tools/health-dashboard/data/health.db
for s in health-dashboard-strava health-dashboard-intervals health-dashboard-garmin; do
  for a in client_id client_secret tokens api_key athlete_id email password; do
    security delete-generic-password -s "$s" -a "$a" 2>/dev/null
  done
done
```

---

## How to pick up next session

Paste into the opening prompt something like:

> Read `Mac-mini/PLAN.md` and `Mac-mini/README.md` in this repo, then let's
> continue the Mac mini build from where we left off. Next up is Phase 6
> (Pushover failure monitoring).

That's enough context — the plan points at the memory files and the README,
so Claude will pick up from there.
