# health-dashboard

Personal health metrics dashboard for HRV, sleep, recovery, and training load. Streamlit UI + a 4-LaunchAgent collector pipeline that pulls from Apple Health (iPhone Auto Export), Strava, Intervals.icu, and Garmin into a single SQLite database.

## What it is

```
  iPhone Health Auto Export ──HTTP POST──▶ receiver:8095 ──▶ data/health.db
  Strava + Intervals + Garmin APIs ──poll──▶ collectors ──▶ data/health.db
                                                              │
                                                              ▼
                                                     Streamlit dashboard :8501
```

Receiver is on `:8095`. Streamlit UI is on `:8501`. Both reachable over Tailscale at `homeserver:8095` and `homeserver:8501`.

## Status

**LIVE** on the Mac mini since 2026-04-22. 4 LaunchAgents running:

- `com.health-dashboard.receiver` — iPhone POST endpoint, port 8095
- `com.health-dashboard.collect` — daily 7:00/7:20 collection from Strava/Intervals/Garmin
- `com.health-dashboard.intervals-poll` — 5-min Intervals.icu refresh
- `com.health-dashboard.staleness` — 7am/9pm staleness check + Pushover alert

Authoritative database is `data/health.db` (~91MB as of 2026-04-30). Laptop's copy is frozen at the cutover; do not write to it.

## Audience

Single-user (you). Hosted on the Mac mini and reachable over Tailscale. Not designed for multi-user, public deployment, or HIPAA workloads.

## Operational notes

- **Keychain shim**: `collectors/__init__.py` monkey-patches `keyring.get_password` to shell out to `security` with `KEYCHAIN_PATH`. This is the canonical pattern reused by other Mac-mini projects — see `~/.claude/projects/.../memory/project_mac_mini_keychain_shim.md`.
- **Apple Health automation**: see [`APPLE_HEALTH_AUTOMATION.md`](APPLE_HEALTH_AUTOMATION.md) in this directory.
- **Keychain entries** (7 secrets): `health-dashboard-strava`, `health-dashboard-intervals`, `health-dashboard-garmin` × `{client_id, client_secret, tokens, ...}`
- **Staleness**: receiver flagged stale if no POST received between 7am and 9pm.
- **Streamlit on `:8501`** runs as `com.health-dashboard.streamlit` — separate from the 4 collector agents.

## Future

- Weight + body composition
- Anomaly detection on HRV trends
- Better visualization of training-load deltas

Out of scope: medical-record integration (deliberate separation — `medical-records/` is its own project with stricter privacy posture).

## Reference

- Memory: `project_health_dashboard.md` for current operational state
- `Mac-mini/PLAN.md` Phase 5b — port-from-laptop history
