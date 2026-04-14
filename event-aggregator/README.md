# Event Aggregator

Monitors your messaging sources for mentions of upcoming events and writes them to Google Calendar.
Runs locally on your Mac every 15 minutes via launchd. All LLM extraction happens through a local
Ollama process — your message content never leaves your machine.

---

## Status: All phases complete

| Phase | What | Status |
|-------|------|--------|
| 1 | Scaffold + mock pipeline | ✅ Done |
| 2 | Gmail + Google Calendar writer | ✅ Done |
| 3 | iMessage + WhatsApp (local SQLite) | ✅ Done |
| 4 | Slack + Discord | ✅ Done |
| 5 | Messenger + Instagram (Notification Center) | ✅ Done |
| 6 | GCal reader + year-ahead analysis + digests | ✅ Done |
| 7 | launchd scheduler | ✅ Done |

---

## Quick start (Phase 1 — no real APIs needed)

```bash
cd event-aggregator
pip install -r requirements.txt
# Ollama must be running: ollama serve
python main.py --mock --dry-run
# Expected: candidate events logged by source/confidence, 0 calendar writes

# Pure unit tests (no Ollama, no APIs)
python -m pytest tests/test_dedup.py tests/test_models.py tests/test_calendar_analyzer.py -v
```

---

## Architecture

```
Sources            Connectors         Extractor       Dedup          Output
─────────────────────────────────────────────────────────────────────────────
Gmail              gmail.py    ─┐
GCal invites       gcal.py     ─┤
Slack              slack.py    ─┤──→ RawMessage ──→ Ollama LLM ──→ Fingerprint ──→ GCal write
iMessage           imessage.py ─┤    list            (local)        + fuzzy         + event log
WhatsApp           whatsapp.py ─┤                                   title match     + Slack DM
Discord            discord.py  ─┤
Messenger/IG       notifs.py   ─┘
```

**Privacy invariant**: `body_text` goes only to local Ollama. Never logged, never printed,
never shown to Claude. Use `--mock` for all demos/debugging.

---

## Calendar intelligence (Phase 6)

Beyond writing events, the pipeline also:
- **Scans the full year ahead** for scheduling conflicts and travel-time risks
- **Daily digest** (Slack DM, 7am): new/changed events in the next 14 days + conflict warnings
- **Weekly digest** (Slack DM, Monday): new/changed events 14 days → 1 year out
- **Per-event Slack DM**: sent whenever an event is created or updated — this is your iOS
  audit trail (searchable in Slack on mobile, with notifications)
- **Local log**: `event_log.jsonl` (gitignored) — every create/update as a JSONL record

---

## Setup checklist

Before each phase, add the corresponding `.env` variables from `.env.example`.

### macOS permissions required
- **Full Disk Access** → System Settings → Privacy & Security → Full Disk Access → add Terminal.app
  (needed for iMessage, WhatsApp, and Notification Center connectors)

### Credentials directory
`event-aggregator/credentials/` is gitignored. It will hold:
- `gmail_oauth.json` — OAuth2 client secrets from Google Cloud Console
- `gmail_token.json` — auto-generated after first OAuth flow
- `gcal_token.json` — auto-generated after first OAuth flow

### Install scheduler (Phase 7)
1. Run `bash install_scheduler.sh`
2. Logs: `/tmp/home-tools-event-aggregator.log` (stdout), `/tmp/home-tools-event-aggregator-error.log` (stderr)
3. To uninstall: `launchctl unload ~/Library/LaunchAgents/com.home-tools.event-aggregator.plist && rm ~/Library/LaunchAgents/com.home-tools.event-aggregator.plist`

---

## Key files

| File | Purpose |
|------|---------|
| `main.py` | CLI entry point — `--mock`, `--dry-run`, `--source` |
| `models.py` | `RawMessage`, `CandidateEvent`, `WrittenEvent` dataclasses |
| `extractor.py` | Ollama LLM extraction + output validation |
| `dedup.py` | Fingerprint + fuzzy dedup logic |
| `state.py` | JSON state (last_run, seen IDs, fingerprints) — auto-pruned |
| `config.py` | `.env` loading + per-source validation |
| `connectors/base.py` | `BaseConnector` abstract class |
| `analyzers/calendar_analyzer.py` | Year-ahead scan, conflict + location analysis |
| `notifiers/digest.py` | Daily/weekly digest builder |
| `logs/event_log.py` | JSONL log + Slack per-event notification |
| `tests/mock_data.py` | **Only** source of test data — all synthetic |
| `state.json` | Runtime state — gitignored, created on first run |
| `event_log.jsonl` | Audit log — gitignored, created on first run |

---

## Development rules

1. **Always use `--mock` when working with Claude** — real message content must never appear
   in conversation output or logs
2. Share only tracebacks and event counts, never message text
3. `body_text` is never printed — log only `source` and `id`
4. `state.json`, `event_log.jsonl`, OAuth tokens, and `credentials/` are all gitignored
