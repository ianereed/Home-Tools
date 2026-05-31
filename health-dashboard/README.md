# health-dashboard

Personal health metrics dashboard for HRV, sleep, recovery, and training load. Streamlit UI + a collector pipeline that pulls from Apple Health (iPhone Auto Export), Strava, and Garmin into a single SQLite database.

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

## Audience

Single-user (you). Hosted on the Mac mini and reachable over Tailscale. Not designed for multi-user, public deployment, or HIPAA workloads.

## Operational notes

- **Keychain shim**: `collectors/__init__.py` monkey-patches `keyring.get_password` to shell out to `security` with `KEYCHAIN_PATH`. This is the canonical pattern reused by other Mac-mini projects — see `~/.claude/projects/.../memory/project_mac_mini_keychain_shim.md`.
- **Apple Health automation**: see [`APPLE_HEALTH_AUTOMATION.md`](APPLE_HEALTH_AUTOMATION.md) in this directory.
- **Keychain entries**: `health-dashboard-strava`, `health-dashboard-garmin` (`email`/`password`) × `{client_id, client_secret, tokens, ...}`
- **Staleness**: `health_staleness` flags sleep/HRV/resting-HR stale after 24h and sends an ntfy.sh push with a diagnosis (receiver up? iPhone online on Tailscale? → app-side steps).
- **Streamlit on `:8501`** runs as `com.health-dashboard.streamlit`.

## Future

- Weight + body composition
- Anomaly detection on HRV trends
- Better visualization of training-load deltas

Out of scope: medical-record integration (deliberate separation — `medical-records/` is its own project with stricter privacy posture).

## Reference

- Memory: `project_health_dashboard.md` for current operational state
- `Mac-mini/PLAN.md` Phase 5b — port-from-laptop history
