# Mac mini Home Server

Setup and operations log for the headless Mac mini M4 home server that hosts
Ollama and the rest of the Home-Tools Python stack.

## Purpose

Private AI appliance living next to the router. Runs local LLM inference on
sensitive personal data (communications, finance, medical) without anything
leaving the house. Hosts scheduled Python jobs for the existing
`event-aggregator`, `meal-planner`, `health-dashboard`, and future
`finance-monitor` projects. Remote access from iPhone and laptop via
Tailscale.

## Hardware

- Apple Mac mini M4 base
- 24GB unified memory
- ~256GB SSD (base)
- Wired Ethernet to home router
- No attached display or keyboard after initial setup

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Physical setup (Ethernet, first power-on) | ✅ 2026-04-22 |
| 1 | First boot (account, hostname, pmset, sharing, firewall, auto-update) | ✅ 2026-04-22 |
| 2 | Remote access (Homebrew, Tailscale, Tailscale SSH from laptop) | ✅ 2026-04-22 |
| 3 | Core tools (`git`, `python@3.12`, `uv`, `gh`, `ollama`) | ✅ 2026-04-22 |
| 4 | Ollama configuration + model pulls | ✅ 2026-04-22 |
| 5 | Port `Home-Tools` repo to server | ✅ 2026-04-22 — event-aggregator + health-dashboard fully migrated (event-aggregator staging moved to `~/Home-Tools/event-aggregator/staging/` out of TCC-protected path; laptop instance disabled, mini is sole writer). Medical-records + meal-planner intentionally stay on laptop. |
| 5c | Service monitor dashboard | ✅ 2026-04-27 — Streamlit at port 8502, shows every loaded LaunchAgent + queues + DBs + Ollama + log tails. `http://homeserver:8502/` |
| 5d | NAS mount + TCC privacy fix | ✅ 2026-04-29 — Share1 mounted at `~/Share1` via `mount_smbfs //iananny@192.168.4.39/Share1`. Two TCC grants required: `tailscaled` in Local Network + `python3.12` in Full Disk Access. See `feedback_macos_lan_wedge_recovery.md`. |
| 5e | nas-intake v1 (NAS drop-folder watcher) | ✅ 2026-04-29 — `~/Home-Tools/nas-intake/` LaunchAgent watches `~/Share1/**/[Ii]ntake/`, OCR + classifies via event-aggregator subprocess (NAS_WRITE_DISABLED=1), files under parent (`<parent>/<year>/<doc-type>/<date>_<slug>/`), appends per-parent JOURNAL.md + journal.jsonl, archives source to `intake/_processed/<YYYY-MM>/`. Calendar events come for free via the subprocess (proposed via Slack dashboard). Auto-remounts via `mount-nas.sh` if NAS unavailable. v1 verified end-to-end with a real medical PDF. |
| 6 | Minimal monitoring (launchd logs + Pushover) | ⏳ Pending |
| 7 | Backup (Time Machine + off-site) | ⏳ Pending |
| 8 | Finance automation scripts (YNAB, Amazon reconciliation) | 🟢 Phase 1 + 2 LIVE 2026-04-24 — Slack bot + read-only YNAB API sync running on the mini; Amazon reconciliation deferred |
| 9 | Slack UX split — dispatcher bot + `#ian-image-intake` (local-only classify/OCR) | 🟡 Code complete 2026-04-24; user must create Slack app + channel before loading LaunchAgent |
| 10 | (Deferred) BlueBubbles iMessage bridge | ⏳ Deferred |
| 11 | (Deferred) Hermes Agent / OpenClaw eval | ⏳ Deferred |

## What's running right now

- **Hostname**: `homeserver.local` (Bonjour) / Tailscale `100.66.241.126`
- **Account**: `homeserver` (not `ianreed` — see path cleanup below)
- **Auto-login**: on
- **FileVault**: off (tradeoff: auto-recovery after reboots > at-rest
  encryption for this threat model)
- **Sleep**: disabled (`sleep 0`, `disksleep 0`, `displaysleep 0`); auto-restart
  after power failure enabled
- **Application Firewall**: on + Stealth Mode
- **Automatic OS security updates**: on
- **SSH**: Remote Login enabled; access via Tailscale SSH (identity-based,
  auth managed by Tailscale, not password)
- **Tailscale**: standalone Homebrew install running as a system daemon;
  `tailscale up --ssh` brings it online on boot
- **Ollama**: `brew services`, LaunchAgent at
  `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist`, bound to
  `127.0.0.1:11434` only. Env vars baked into plist: `OLLAMA_FLASH_ATTENTION=1`,
  `OLLAMA_KV_CACHE_TYPE=q8_0`. Other env vars controlled per-request from
  Python (`keep_alive`, `options.num_ctx`).
- **Claude Code state** on the mini: full `~/.claude/` copied from laptop via
  rsync over Tailscale SSH (plans, memory, settings, slash commands). All 7
  memory project directories renamed from `-Users-ianreed-...` to
  `-Users-homeserver-...` so auto-memory resolves on the server.
- **Python environment pattern** (proven with event-aggregator + health-dashboard):
  - `uv venv --python 3.12` in each project directory
  - `source .venv/bin/activate && uv pip install -r requirements.txt`
  - Run `install_scheduler.sh` from inside the activated venv — it auto-detects
    the venv's python3 via `which python3` and bakes that into the LaunchAgent
    plist
- **Health-dashboard** at `~/Home-Tools/health-dashboard` with 5 LaunchAgents:
  `receiver` (port 8095, KeepAlive), `collect` (7:00 + 7:20am), `intervals-poll`
  (every 5 min), `staleness` (7am + 9pm), `streamlit` (port 8501, KeepAlive,
  bound `0.0.0.0`). iPhone Health Auto Export posts to
  `http://homeserver:8095/` over Tailscale; dashboard UI at
  `http://homeserver:8501/`. Laptop copies of the 4 data-plane plists at
  `~/Library/LaunchAgents/com.health-dashboard.*.plist.disabled` (renamed so
  they don't auto-load on login).
  - LaunchAgent logs: `/tmp/home-tools-<project>.log` (+ `-error.log`)
- **Models pulled**:
  - `qwen3:14b` (Q4_K_M, ~9GB) — event-aggregator text extraction
  - `qwen2.5vl:7b` (~6GB) — event-aggregator vision/image pipeline
- **event-aggregator schedule**: every 10 min (upgraded from 15 min 2026-04-22); heavy
  phases (Ollama extraction + vision) run 24/7 — no time-window gate on the mini
- **service-monitor** at `~/Home-Tools/service-monitor` — Streamlit dashboard at
  `http://homeserver:8502/`. 1 LaunchAgent: `com.home-tools.service-monitor`
  (KeepAlive, port 8502). Shows every loaded mini LaunchAgent with 🟢/🟡/🔴 status,
  HTML swim-lane data-flow visual, queue depths (event-aggregator state.json),
  DB sizes (health.db, finance.db), Ollama model list, and log tails.
- **finance-monitor** at `~/Home-Tools/finance-monitor` with 2 LaunchAgents:
  `com.home-tools.finance-monitor` (Slack bot, KeepAlive — Socket Mode,
  DM-only, locked to `ALLOWED_SLACK_USER_IDS`, 60s/user rate limit) and
  `com.home-tools.finance-monitor-watcher` (every 5 min — runs read-only
  YNAB API sync first, then scans `intake/` for CSVs/PDFs/images). YNAB API
  client is GET-only by design (hard requirement); cutoff `2026-04-24`. Live
  data: `~/Home-Tools/finance-monitor/data/finance.db`

## Key decisions (2026-04-22)

- **Server account name**: `homeserver` (not `ianreed`). Trades off ~20
  hardcoded path references across 6 files in the repo for cleaner mental
  separation and nicer SSH syntax. See
  `.claude/projects/.../project_mac_mini_path_cleanup.md` for the one-shot
  `sed` fix command to run after cloning to the server.
- **FileVault off** over auto-recovery: threat model is home-network only,
  behind a locked door, with no physical targeting expected. Auto-login
  lets launchd services come back up unattended after reboots/power events.
- **Minimal monitoring**: launchd log files + Pushover/ntfy for failure
  pings. No dashboards, no Uptime Kuma, no iStatistica. Add later if
  actually needed.
- **Accept Homebrew's opinionated Ollama plist**: Only 2 of 7 env vars
  survive brew's plist regeneration. Acceptable because the two that stick
  (FLASH_ATTENTION, KV_CACHE_TYPE) are the memory-critical ones. The rest
  (context length, keep-alive) are better controlled per-request from
  Python anyway.
- **No Apple ID / iCloud** on the server: not needed for any current workload.
  Will revisit only if the BlueBubbles iMessage bridge is actually deployed.
- **No Apple Intelligence**: wrong tool for a headless server, competes with
  Ollama for unified memory, requires Apple ID we aren't signing into.
- **Code lives at `~/Home-Tools`, NOT `~/Documents/GitHub/Home-Tools`** (found
  2026-04-22). macOS TCC protects `~/Documents`, `~/Downloads`, `~/Desktop`,
  `~/Pictures`, `~/Music`, `~/Movies`. launchd agents don't have Full Disk
  Access by default, so Python invoked by a LaunchAgent hangs indefinitely on
  `getpath_readlines` → `__open_nocancel` when the venv lives under
  `~/Documents`. Running the same script from an SSH shell works (different
  TCC context), which makes the bug deceptively sneaky. Rule: **on this
  server, all project code lives at `~/<project-name>/` or `~/src/`, never
  under the protected user folders.**
- **Empty-password login keychain for `homeserver`** (2026-04-22). LaunchAgents
  on this server run in an aqua audit session whose keychain search list
  includes `~/Library/Keychains/login.keychain-db` but cannot auto-unlock a
  password-protected one (no GUI login). A non-empty password means every
  `keyring.get_password` from a LaunchAgent returns `errSecAuthFailed` (security
  CLI exit 152). Empty password + `security set-keychain-settings` (no
  auto-lock) makes the keychain always readable by the homeserver user. Net
  security loss is negligible given FileVault is off, SSH is Tailscale-gated,
  and anyone with `homeserver` shell can read `~/Home-Tools/**` anyway. If
  FileVault is ever turned on, pair it with a real keychain password.
- **`keyring` library on the mini uses a shim** (2026-04-22). `keyring>=25`
  ignores `Keyring.keychain` (upstream issue #623). `collectors/__init__.py`
  in health-dashboard monkey-patches `keyring.{get,set,delete}_password` to
  shell out to `security` with the keychain path from env var `KEYCHAIN_PATH`
  (and unlocks the keychain with empty password at module import). The shim is
  a no-op on the laptop, where `KEYCHAIN_PATH` is unset.
- **event-aggregator upgraded to qwen3:14b + 24/7 scheduling (2026-04-22).**
  Text extraction model upgraded from `qwen2.5:7b` to `qwen3:14b` (Qwen3
  family; better instruction-following and JSON compliance at same ~9 GB
  footprint). Scheduler interval tightened from 15 → 10 min. The midnight–6am
  heavy-phase gate is disabled (`OLLAMA_ACTIVE_HOUR_END=24`) because the mini
  has no interactive user session to protect. Rollback: flip `OLLAMA_MODEL` in
  `.env` back to `qwen2.5:7b`; no code change needed. Chinese open-weight
  models (Qwen3, DeepSeek) are safe for this use case because Ollama is
  127.0.0.1-bound, GGUF weights are not executable, and every event write is
  gated behind Slack approval (human-in-the-loop before any GCal write).
- **Application Firewall + Stealth Mode silently drops unapproved app inbound
  traffic** (2026-04-22). Symptom: TCP handshake succeeds from clients but
  subsequent data is dropped and the request times out with 0 bytes received.
  Loopback connections work normally, which makes this look like an app bug.
  Fix: for any project that binds a non-loopback port, add BOTH the Homebrew
  Python bin shim (`/opt/homebrew/Cellar/python@3.12/X.Y.Z/.../bin/python3.12`)
  AND the re-exec'd app-bundle binary
  (`.../Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python`)
  to the allowlist via `sudo socketfilterfw --add` + `--unblockapp`, then
  kickstart the LaunchAgent so it re-links. Ollama is unaffected because it
  binds only to `127.0.0.1`.

## Critical file paths

| Path | Purpose |
|---|---|
| `~/Home-Tools` | The main repo on the mini (outside TCC-protected folders) |
| `~/Home-Tools/event-aggregator/.venv` | Per-project Python venv |
| `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist` | Ollama service definition |
| `~/Library/LaunchAgents/com.home-tools.*.plist` | Home-tools LaunchAgents |
| `/opt/homebrew/var/log/ollama.log` | Ollama stdout+stderr |
| `/tmp/home-tools-<project>.log` | Per-project LaunchAgent stdout+stderr |

## Verification commands

Run these at any point to confirm the server is healthy:

```bash
# Event-aggregator last run completed cleanly (PID `-`, exit status `0`)
launchctl list | grep event-aggregator

# Ollama is up, bound to loopback only
sudo lsof -iTCP:11434 -sTCP:LISTEN -n -P

# Hot-path inference works
time ollama run qwen3:14b "say only: ready"

# All models are present
ollama list

# Power policy is correct
sudo pmset -g | grep -E '^ (sleep|disksleep|displaysleep|womp|autorestart)'

# Tailscale is connected
tailscale status

# No unexpected listeners
sudo lsof -iTCP -sTCP:LISTEN -n -P

# Automatic updates on
softwareupdate --schedule
```

## Related documents

- **`Mac-mini/PLAN.md`** — living working plan. Read this first when
  resuming a session. Has the next concrete action + per-phase detailed
  steps + known gotchas.
- `Mac-mini/original-context.rtf` — the full planning conversation that led
  to the purchase decision (Apr 19–21)
- `~/.claude/plans/i-want-you-to-tranquil-pearl.md` — frozen initial setup
  plan (phases 0–7 as originally scoped). Preserved for history; superseded
  by `PLAN.md`.

## Update discipline

Keep this README current as phases complete. Entry points for updates:

- Add a row to the status table (or flip an emoji) when a phase finishes
- Add to the "What's running" section when a new long-running service comes
  up (Pushover, BlueBubbles, a new LaunchAgent)
- Record any new decision + its reasoning in the "Key decisions" section
- Don't put command output here — put it in the plan file or a separate log
