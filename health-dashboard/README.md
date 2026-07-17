# health-dashboard

Personal health metrics dashboard for HRV, sleep, heart rate, and training load.
A Streamlit UI + a collector pipeline that pulls from Apple Health (iPhone Auto
Export), Strava, and Garmin into a single SQLite database.

**Information-only, historical view.** Garmin owns day-to-day training guidance;
this dashboard is for the long view — holistic, historical tracking you check in
on periodically rather than every morning. The home screen ("Overview") answers
"what changed since I last looked?"; every data page favours long-range trends.
There are deliberately no "train hard / rest today" prescriptions.

## What it is

```
  iPhone Health Auto Export ──HTTP POST──▶ receiver:8095 ──▶ data/health.db
  Strava + Garmin APIs ──poll──▶ collectors ──▶ data/health.db
                                                              │
                                                              ▼
                                                     Streamlit dashboard :8501
```

Receiver is on `:8095`. Streamlit UI is on `:8501`. Both reachable over Tailscale at `homeserver:8095` and `homeserver:8501`.

## Status

**LIVE** on the Mac mini. Two always-on LaunchAgents plus jobs-driven collection:

- `com.health-dashboard.receiver` — iPhone POST endpoint, port 8095 (LaunchAgent)
- `com.health-dashboard.streamlit` — dashboard UI, port 8501 (LaunchAgent)
- Collection runs as **`jobs` huey periodic tasks** (not LaunchAgents): `health_collect`
  (daily 7:00/7:20 → `collectors.collect_all`) and `health_staleness` (7:00/21:00).
  They live in `../jobs/kinds/health_*.py` and subprocess into this project's `.venv`.
  After changing a kind: `launchctl kickstart -kp gui/$(id -u)/com.home-tools.jobs-consumer`.

**Data sources:** Strava (activities), Garmin (sleep, resting HR, wellness — HRV /
sleep score / steps), Apple Health (sleep, HRV, resting HR via the iPhone receiver).
Suunto/Intervals.icu was retired 2026-05-30 (device gone); Garmin now owns wellness.

## Dashboard pages

`dashboard/app.py` (UI) + `dashboard/lib.py` (one shared Plotly theme + the
"since you last looked" store). Six pages, all historical-first with a
30d/90d/6mo/1y/All range selector (default 90d):

- **Overview** — periodic-check home: "since you last looked" change strip, five
  headline trend tiles (HRV · resting HR · sleep · fitness · steps) with
  sparklines, a data-freshness panel, and 30-day highlights.
- **Sleep** — duration trend + 7-day average, monthly and weekday patterns,
  stages over time, Garmin sleep score, sleeping HR, single-night drill-down.
- **Heart & HRV** — HRV trend vs baseline, resting-HR trend, daily HR range, HR
  distribution by zone.
- **Fitness** — CTL/ATL/TSB "fitness & form" curve (informational only) + weekly
  training load. The recovery math lives in `recovery/engine.py`.
- **Activity** — activity log, type mix, per-activity HR stream.
- **Wellness** — daily steps, SpO₂ (weight/body-comp planned).

The "since you last looked" comparison is stored in `data/dashboard_state.json`
(your previous visit timestamp); first visit falls back to a 30-day comparison.

Authoritative database is `data/health.db`. Laptop's copy is frozen at the cutover; do not write to it.

## Garmin auth

Garmin enforces MFA + per-IP 429 rate-limits on fresh logins, so headless collection
**resumes from a saved OAuth token store** at `~/.garminconnect` (lasts ~1 year).
Seed it once, interactively, when it's missing/expired:

```
ssh -t homeserver@homeserver \
  'cd ~/Home-Tools/health-dashboard && \
   KEYCHAIN_PATH=/Users/homeserver/Library/Keychains/login.keychain-db \
   .venv/bin/python3 -m collectors.seed_garmin_token'
```

Enter the MFA code Garmin sends. (The `KEYCHAIN_PATH` env is required — the keychain
shim only activates when it's set.)

## DEXA workflow (quarterly)

DEXA scans aren't API-connected — after each quarterly scan:

1. Append a row to `cardiology/dexa_scans.csv` (gitignored; header + format in
   `CARDIO_PLAN.md` Appendix C — lb units, matching the US DEXA report). If the
   file doesn't exist yet, create it first: `python3 cardiology/import_dexa.py --init`.
2. `scp cardiology/dexa_scans.csv homeserver@homeserver:~/Home-Tools/health-dashboard/cardiology/`
3. On the mini: `ssh homeserver@homeserver 'cd ~/Home-Tools/health-dashboard && .venv/bin/python3 cardiology/import_dexa.py'`

The importer converts lb→kg and writes `body_composition` (full DEXA snapshot)
and `body_weight` (source `'dexa'`) so the scan lines up with Garmin/Apple weight
on the Cardiology page. Re-running the importer is safe (idempotent, keyed on
`UNIQUE(timestamp, source)`) — fix a typo'd row and re-run to converge.

## Audience

Single-user (you). Hosted on the Mac mini and reachable over Tailscale. Not designed for multi-user, public deployment, or HIPAA workloads.

## Operational notes

- **Keychain shim**: `collectors/__init__.py` monkey-patches `keyring.get_password` to shell out to `security` with `KEYCHAIN_PATH`. This is the canonical pattern reused by other Mac-mini projects — see `~/.claude/projects/.../memory/project_mac_mini_keychain_shim.md`.
- **Apple Health automation**: see [`APPLE_HEALTH_AUTOMATION.md`](APPLE_HEALTH_AUTOMATION.md) in this directory.
- **Keychain entries**: `health-dashboard-strava`, `health-dashboard-garmin` (`email`/`password`) × `{client_id, client_secret, tokens, ...}`
- **Staleness**: `health_staleness` flags sleep/HRV/resting-HR stale after 24h and sends an ntfy.sh push (topic `ian-health-dashboard`) with a diagnosis (receiver up? iPhone online on Tailscale? → app-side steps). Every run also appends to `logs/health-staleness.log` — that file's mtime is the kind's migration baseline, so it must always be written. Diagnosis subprocess calls (`tailscale`, `lsof`) degrade gracefully if the binary isn't on `PATH`.
- **Collection alerting**: `health_collect` persists each run to `logs/collect.log` and, on a non-zero exit, pushes an ntfy alert and raises (so huey records the failure). This is the primary "something broke" signal now the dashboard isn't watched daily. Collectors use a 30s socket timeout + one bounded retry each.
- **Streamlit on `:8501`** runs as `com.health-dashboard.streamlit`.

## Future

- Weight + body composition
- Anomaly detection on HRV trends
- Better visualization of training-load deltas

Out of scope: medical-record integration (deliberate separation — `medical-records/` is its own project with stricter privacy posture).

## Reference

- Memory: `project_health_dashboard.md` for current operational state
- `Mac-mini/PLAN.md` Phase 5b — port-from-laptop history
