# Health Dashboard

A personal health data dashboard that collects sleep, heart rate, activity, and wellness data from all your devices (Apple Watch, Suunto, Garmin) and displays it with recovery recommendations.

Built April 2026 with Claude Code.

---

## Quick Start

### View the dashboard
```
cd ~/Documents/Claude/Health\ Dashboard && ./run.sh
```
Opens at http://localhost:8501 on your Mac, or http://YOUR.TAILSCALE.IP:8501 from your phone via Tailscale.

### Collect fresh data
```
cd ~/Documents/Claude/Health\ Dashboard
source venv/bin/activate
python -m collectors.collect_all --days 30
```

---

## How It All Works

### Data Flow
```
Apple Watch ──→ Apple Health ──→ Health Auto Export app ──→ HTTP POST ──→ Mac receiver ──→ SQLite
Suunto Watch ──→ Suunto App ──→ Intervals.icu ──→ REST API ──→ Mac Python ──→ SQLite
Garmin Watch ──→ Strava ──→ Strava API ──→ Mac Python ──→ SQLite
                                                                              │
                                                                    Streamlit Dashboard
                                                                              │
                                                                    Tailscale VPN ──→ iPhone
```

### Data Sources

| Source | What it provides | How it's collected | Automation |
|--------|-----------------|-------------------|------------|
| **Apple Health** | Sleep (with stages), HR, HRV | Health Auto Export iOS app sends JSON to a server running on your Mac (port 8095) | Automatic — iOS Shortcut triggers export every time you plug in your phone |
| **Suunto** | Sleep, resting HR, HRV, sleep score, SpO2, steps | Intervals.icu free account syncs from Suunto, our Python script pulls from Intervals.icu API | Runs via `collect_all.py` |
| **Strava** | Activities + detailed HR streams | Strava OAuth API | Runs via `collect_all.py` |
| **Garmin** | HR data (gap-filling only) | `garminconnect` Python library | Currently blocked by rate limit — low priority since Garmin activities flow through Strava |

### What's Running in the Background

1. **Apple Health Receiver** — a small HTTP server on port 8095 that receives data from the Health Auto Export iPhone app
   - Runs as a macOS launchd service (`com.health-dashboard.receiver`)
   - Starts automatically on login, restarts if it crashes
   - Check if running: `curl http://localhost:8095/`
   - View logs: `tail -f ~/Documents/Claude/Health\ Dashboard/data/receiver.log`

2. **Daily Data Collection** — a launchd job that runs `collect_all.py` at 8 AM
   - Plist exists at `config/com.health-dashboard.collect.plist` but may not be installed yet
   - To install: copy plist to `~/Library/LaunchAgents/` and `launchctl load` it
   - Or just run manually: `python -m collectors.collect_all --days 7`

3. **Tailscale VPN** — mesh VPN connecting your Mac and iPhone so you can view the dashboard from your phone over any network
   - Mac Tailscale IP: check Tailscale app on Mac for your IP
   - Dashboard URL from phone: `http://YOUR.TAILSCALE.IP:8501`

---

## iPhone Setup (already done, but in case you need to redo it)

### Health Auto Export App ($5)
1. Install "Health Auto Export" by K-Duo from App Store
2. Create automation → REST API → URL: `http://YOUR-MAC-NAME.local:8095/`
3. Format: JSON. Metrics: Heart Rate, Resting Heart Rate, Sleep Analysis, HRV
4. Sync cadence: every 6 hours

### Shortcuts Automation (most reliable trigger)
1. Shortcuts app → Automation → + → "When Charger Is Connected"
2. Add action: Health Auto Export → Export
3. Run Immediately: ON, Notify When Run: OFF

This silently exports health data every time you plug in your phone.

### Tailscale
1. Install Tailscale on Mac and iPhone
2. Sign in with the same account on both
3. Access dashboard from phone at `http://YOUR.TAILSCALE.IP:8501`
4. Add to Home Screen from Safari for an app-like icon

### Intervals.icu (for Suunto data)
1. Free account at intervals.icu
2. Connected to Suunto under Settings → Connections
3. API key stored in macOS Keychain
4. Athlete ID: (find yours at intervals.icu → Settings → API)

---

## Dashboard Pages

### Today (landing page)
- **Training recommendation** — colored banner: Rest Day (red), Easy Only (orange), Moderate (blue), Train Hard (green)
- Based on: TSB (fitness-fatigue balance) + HRV z-score + resting HR elevation + sleep hours + sleep debt
- **Last night's sleep** — total hours, sleep stage breakdown bar, comparison to 7-day average
- **Tonight's recommendation** — sleep debt calculation with actionable advice
- **Recovery snapshot** — training load, form status, HRV and resting HR vs baseline

### Recovery
- Three recovery models displayed independently:
  1. **TRIMP** (Training Impulse) — training load per activity with decay over time
  2. **Fitness-Fatigue** (CTL/ATL/TSB) — long-term fitness vs short-term fatigue balance
  3. **Physiological** — HRV, resting HR, sleep quality vs personal baselines
- Expandable "What do these numbers mean?" section

### Sleep
- Duration trend (Apple + Suunto), stacked sleep stage area chart
- Selectable date for stage pie chart breakdown
- Sleep score trend (from Suunto), sleeping heart rate trend

### Heart Rate
- Daily min/avg/max range chart, smoothed resting HR trend
- HR distribution histogram with zone overlays

### Activities
- 88+ activities from Strava (1 year of history)
- Per-activity HR chart from detailed HR streams
- Weekly volume (hours + distance), stats by activity type

### Wellness
- Daily steps, HRV trend, SpO2, sleep score — all from Suunto via Intervals.icu

---

## Project Structure

```
~/Documents/Claude/Health Dashboard/
├── dashboard/
│   └── app.py              # Main Streamlit dashboard (all 6 pages)
├── recovery/
│   ├── engine.py            # TRIMP, CTL/ATL/TSB, physiological signals
│   └── advisor.py           # Daily training + sleep recommendations
├── collectors/
│   ├── db.py                # SQLite schema + connection helpers
│   ├── collect_all.py       # Runs all collectors
│   ├── strava_collector.py  # Strava activities + HR streams
│   ├── intervals_collector.py # Suunto data via Intervals.icu
│   ├── apple_health_server.py # HTTP receiver for Health Auto Export
│   ├── apple_health_xml.py  # One-time Apple Health XML import
│   ├── apple_health.py      # Legacy JSON parser (iOS Shortcut approach)
│   ├── garmin_collector.py  # Garmin Connect (currently blocked)
│   └── strava_setup.py      # One-time Strava OAuth setup
├── config/
│   ├── com.health-dashboard.receiver.plist  # launchd: Apple Health receiver
│   └── com.health-dashboard.collect.plist   # launchd: daily collection
├── data/
│   ├── health.db            # SQLite database (all health data)
│   └── receiver.log         # Apple Health receiver logs
├── screenshots/             # Screenshots for reference
├── venv/                    # Python virtual environment
├── run.sh                   # Start the dashboard
├── setup.sh                 # First-time setup (credentials, deps)
├── requirements.txt         # Python dependencies
├── APPLE_HEALTH_AUTOMATION.md  # Apple Health Export setup guide
└── SHORTCUT_INSTRUCTIONS.md    # iOS Shortcut setup (legacy approach)
```

---

## Credentials

All stored in macOS Keychain (not in any files). Services:
- `health-dashboard-strava` — Strava OAuth tokens, client ID, client secret
- `health-dashboard-intervals` — Intervals.icu API key + athlete ID
- `health-dashboard-garmin` — Garmin email + password

To view: Keychain Access app → search "health-dashboard"

---

## Database

SQLite at `data/health.db`. Tables:
- `sleep` — nightly sleep records (date, total/deep/rem/light/awake minutes, source)
- `heart_rate` — HR samples (timestamp, bpm, context, source)
- `activities` — activity summaries (date, type, duration, distance, HR, calories, source)
- `wellness` — daily wellness metrics (HRV, sleep score, SpO2, steps, sleeping HR)
- `activity_streams` — per-second HR data during activities (for TRIMP calculation)

---

## Troubleshooting

### Dashboard won't start
```
cd ~/Documents/Claude/Health\ Dashboard
source venv/bin/activate
streamlit run dashboard/app.py
```
If venv is broken: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`

### Apple Health data not flowing
1. Check receiver: `curl http://localhost:8095/` — should say "running"
2. Check logs: `tail ~/Documents/Claude/Health\ Dashboard/data/receiver.log`
3. If receiver is down: `launchctl load ~/Library/LaunchAgents/com.health-dashboard.receiver.plist`
4. Make sure Health Auto Export app URL is `http://YOUR-MAC-NAME.local:8095/`
5. Plug in your phone to trigger the Shortcut automation

### Can't access from phone
1. Make sure Tailscale is connected on both Mac and iPhone (check Tailscale app)
2. Make sure Streamlit is running on Mac (`./run.sh`)
3. URL: `http://YOUR.TAILSCALE.IP:8501`
4. If Tailscale IP changed: check Tailscale app on Mac for current IP

### Strava token expired
```
cd ~/Documents/Claude/Health\ Dashboard
source venv/bin/activate
python collectors/strava_setup.py
```

### Garmin rate limited
Don't retry more than once. Wait several days between attempts. Each failed attempt extends the block.

---

## Future Plans

1. **Cloud deployment** — move to Streamlit Cloud + Supabase (free PostgreSQL) so the dashboard runs 24/7 without the Mac
2. **Native iOS app** — wrap the cloud dashboard with Capacitor for App Store
3. **Polish remaining tabs** — Sleep, Heart Rate, Activities, Wellness could use the same mobile optimization as Today + Recovery
4. **Install daily collection cron** — the plist exists but isn't installed yet
5. **Garmin** — try connecting again after rate limit clears (low priority)

---

## Recovery Models Explained

### TRIMP (Training Impulse)
Measures how hard each workout was using HR data. Higher TRIMP = more stress on the body. Decays exponentially over 1-3 days depending on intensity.

### CTL / ATL / TSB (Fitness-Fatigue Model)
- **CTL** (Chronic Training Load) = your fitness level (42-day rolling average of daily TRIMP)
- **ATL** (Acute Training Load) = recent fatigue (7-day rolling average)
- **TSB** (Training Stress Balance) = CTL minus ATL = your "form"
  - Positive TSB → you're fresh, ready to perform
  - Negative TSB → you're carrying fatigue, building fitness
  - Very negative (< -30) → overreaching, need rest

### Daily Training Recommendation
Combines TSB + HRV (heart rate variability) + resting HR + sleep into a single recommendation:
- **Train Hard** (green): TSB positive, HRV normal, good sleep
- **Moderate** (blue): generally OK to train
- **Easy Only** (orange): HRV depressed, RHR elevated, or poor sleep
- **Rest Day** (red): multiple warning signals, body needs recovery
