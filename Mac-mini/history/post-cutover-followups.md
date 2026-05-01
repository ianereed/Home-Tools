# Post-cutover follow-ups (laptop → mini, Apr 22 → Apr 29)

Scheduled for 2026-04-29 (7-day rollback window closes). If no issues
surfaced on the mini by then, delete the following laptop-side rollback
artifacts:

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
