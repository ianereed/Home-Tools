# Handoff — 2026-04-14 (post-smarts session)

## What was done this session

Major "smarts" upgrade to the event extraction pipeline. All 11 files modified. Tests: 21/22 pass (1 pre-existing dedup failure unrelated to these changes).

### Changes

1. **Context enrichment** — Prompts now include sender/subject/channel/attendee metadata.
   - Gmail: From, Subject, To, CC in prompt
   - Slack: sender display name (via `users.info`), channel name
   - GCal: event title, start, location, attendee emails

2. **Source-aware prompting** — Different prompt templates per source:
   - `"email"` → formal email framing
   - `"calendar"` → structured calendar invite framing (still goes through Ollama)
   - `"chat"` → casual language + relative dates note
   - `"default"` → fallback

3. **User timezone** — `USER_TIMEZONE` config var (default `America/Los_Angeles`). Injected into all prompts so Ollama returns correct local times. GCal events now include `timeZone` in start/end objects.

4. **Banded confidence** — Replaces single threshold. Per-source config:
   - `confidence < medium` → skip entirely
   - `medium ≤ confidence < high` → create event with `[?]` prefix in GCal title
   - `confidence ≥ high` → create normally
   - See `config.CONFIDENCE_BANDS` for per-source values

5. **Extended LLM schema** — Ollama now returns:
   - `is_update`, `original_title_hint` — signals for update detection
   - `is_cancellation` — signals for deletion
   - `is_recurring` + `recurrence_hint` — flagged and skipped (no RRULE yet)
   - `attendees` → `[{name, email}]` — surfaced in Slack, not added to GCal
   - `category` → `work|personal|social|health|travel|other` — GCal color coded

6. **Event identity tracking** — `state.written_events` dict maps `gcal_id → {title, start, fingerprint, created_at}`. Used for update/cancel lookup.

7. **Update detection** — When Ollama signals `is_update=true` + `original_title_hint`, main.py fuzzy-searches `written_events` then `calendar_snapshot`. If found, calls `gcal_writer.update_event()` (patches GCal) instead of creating a duplicate.

8. **Cancellation detection** — `is_cancellation=true` → fuzzy lookup → `gcal_writer.delete_event()`.

9. **Cross-calendar dedup** — Before any new event is written, `calendar_snapshot` is checked for near-duplicate titles on the same date (ratio > 80). Catches events already on other calendars.

10. **Conflict detection** — Before writing, checks target calendar ±30 min window. Conflict titles are logged and posted to Slack (doesn't block write).

11. **GCal category colors** — `CATEGORY_COLORS` config maps category → GCal colorId. Applied on create and update.

12. **Slack channel threading** — ALL notifications now go to `ian-event-aggregator` channel (not DMs):
    - One thread per calendar day (opener created on first action)
    - Thread ts persisted in `state.day_thread_ts`
    - Each event action = one reply to the thread
    - Run summary posted at end (only if something happened)
    - Digests also route to channel thread

## Key files changed

`config.py`, `models.py`, `state.py`, `extractor.py`, `writers/google_calendar.py`, `main.py`, `logs/event_log.py`, `notifiers/slack_notifier.py`, `notifiers/digest.py`, `connectors/gmail.py`, `connectors/slack.py`, `connectors/google_calendar.py`

## .env additions needed

```
USER_TIMEZONE=America/Los_Angeles
SLACK_NOTIFY_CHANNEL=ian-event-aggregator
```

(`SLACK_DIGEST_USER_ID` is no longer used — can be removed from .env)

## How to run

```bash
# Safe test (no calendar writes, no Slack posts):
python3 main.py --dry-run

# Mock test (Ollama processes synthetic data, no real sources or calendar writes):
python3 main.py --mock --dry-run

# Full run:
python3 main.py
```

## Known deferred items

- Sending actual calendar invites to `suggested_attendees` (future phase when tool is reliable)
- Cross-calendar dedup from additional calendars not yet in snapshot (future upgrade — user will add other calendars)
- RRULE recurrence creation (recurring events are flagged and skipped, not created with recurrence rules)
