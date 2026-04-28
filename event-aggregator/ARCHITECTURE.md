# Event Aggregator — Architecture Reference

A precise, file-cited description of how the tool currently behaves.
Use this as the entry point when picking up the project after a long
gap or onboarding a new collaborator.

Last verified against code: 2026-04-28 (Tier 2 — Intake Audit + Status enum).

---

## 1. Sources monitored

Eight connectors registered in `main.py:_CONNECTOR_REGISTRY`:

| Source | Connection | Config | Scope/Filter |
|---|---|---|---|
| **Gmail** | OAuth2 `users.messages.list` | `GMAIL_CREDENTIALS_JSON`, `GMAIL_TOKEN_JSON` | `after:{since}` query; max 100/fetch; marketing labels dropped unless user replied with confirmation language; plain-text MIME parts only |
| **GCal invites** | OAuth2 `events.list` | `GCAL_TOKEN_JSON`, `GCAL_PRIMARY_CALENDAR_ID` | `responseStatus == "needsAction"` only; `timeMin = today UTC`; `updatedMin = max(since, now-30d)` so we re-pull only invites whose state changed (Tier 1.2); recurring series collapsed to next instance |
| **Slack** | Bolt WebClient | `SLACK_BOT_TOKEN`, `SLACK_MONITOR_CHANNELS` | configured channels only; `oldest = since`; bot/empty/subtype-tagged messages skipped |
| **iMessage** | SQLite read | `IMESSAGE_DB_PATH` | requires Full Disk Access; `WHERE date > ? AND text != '' LIMIT 500` |
| **WhatsApp** | SQLite read | `WHATSAPP_DB_PATH` | schema sniff before query; `LIMIT 500` |
| **Discord** | REST API | `DISCORD_BOT_TOKEN`, `DISCORD_MONITOR_CHANNELS` | snowflake `since` filter; deferred (no token live) |
| **Messenger / Instagram** | macOS NotificationCenter SQLite | hard-coded bundle filter | notifications truncate to ~80 chars |

Image intake is event-driven: dispatcher routes `#ian-image-intake` uploads
to `cli enqueue-image --file <path>` (or directly to `state.ocr_queue`).

## 2. Information monitored for

Two output shapes, both extracted by one LLM call per message
(`extractor._SCHEMA`):

- **Events**: `title`, `start` (ISO+tz), `end`, `location`, `confidence`,
  `is_update`, `original_title_hint`, `is_cancellation`, `is_recurring`,
  `recurrence_hint`, `attendees`, `category`, `date_certainty`,
  `event_description`.
- **Todos**: `title`, `context`, `due_date`, `priority`, `confidence`.

`date_certainty` (Tier 2.3) routes uncertain dates to a fuzzy_event
proposal that asks the user for a date instead of guessing.

Calendar context (next `CALENDAR_CONTEXT_WEEKS=4`) pre-fetched from
**both** primary and weekend calendars (Tier 2.2) and injected into the
extraction prompt for update/dup detection.

## 3. Information intentionally ignored

- Slack channels not in `SLACK_MONITOR_CHANNELS`; Discord channels not
  in `DISCORD_MONITOR_CHANNELS`; NotificationCenter bundles outside the
  Messenger/Instagram allowlist.
- Slack messages with any `subtype`; Gmail messages with
  `CATEGORY_PROMOTIONS`/`CATEGORY_UPDATES` unless replied to.
- All sources: empty/null body text.
- GCal invites with no attendees or non-pending status.
- Messages older than `state.last_run[source]`.
- Events outside `[now, now + 2 years]`.
- All-day events stripped from the extraction prompt context using the
  authoritative GCal `start.date` field (Tier 1.2 — no longer a
  midnight-UTC heuristic).
- Events below `CONFIDENCE_BANDS[source].medium`; todos below
  `TODOIST_TODO_MIN_CONFIDENCE`.
- Already-seen message IDs (`state.seen_message_ids`, 30-day window).
- Already-written fingerprints (Tier 1.5: hour-bucketed
  `sha256(title + YYYY-MM-DDTHH)`).
- Rejected fingerprints (Tier 1.6: 90-day window — explicit "skip"
  remembers itself across runs and across sources).
- Recurring events: dropped from the calendar write but a
  `state.recurring_notices` entry is added (24h) so the dashboard
  surfaces "Possibly recurring — handle manually" (Tier 2.1).
- LLM verdict "no" from the pre-classifier (Tier 2.5).

## 3.5 Connector contract (Tier 2 — Intake Audit, 2026-04-28)

Every connector implements `BaseConnector` (`connectors/base.py`):
- `source_name: str` — class constant matching `RawMessage.source`
- `fetch(since: datetime, mock: bool = False) -> (list[RawMessage], ConnectorStatus)`

`fetch()` MUST NEVER raise. Every exception path maps to a
`ConnectorStatus`:

| Code | Meaning | Watermark advances? | User action |
|---|---|---|---|
| `ok` | fetched (0+ messages) | yes | none |
| `no_credentials` | token/key not configured | yes (deferred-by-design) | configure or accept deferred |
| `auth_error` | 401/403/refresh failed | no (catch up after re-auth) | re-auth |
| `permission_denied` | macOS FDA missing, file unreadable | no (catch up after grant) | grant FDA to launchd |
| `unsupported_os` | platform incompatible (e.g. NC on Sequoia) | yes (terminal — no catch-up possible) | none — feature unavailable |
| `network_error` | transient | no (retry next cycle) | none unless persistent |
| `schema_error` | upstream API/DB shape changed | no (catch up after deploy) | code update |
| `unknown_error` | catchall | no | investigate logs |

`fetch_only()` (`main.py:fetch_only`) records the outcome via
`state.record_connector_status(source, code, message, ts)` and applies
the watermark policy above. A 14-day floor on `since` keeps recovery
queries bounded after a long failure window.

The Slack dashboard surfaces any source in a non-`ok` terminal state, plus
any source with ≥6 consecutive transient errors (~1h at 10-min cadence).
service-monitor (`http://homeserver:8502/`) renders red / yellow badges
per source.

**Privacy invariant:** `ConnectorStatus.message` MUST NOT contain message
bodies, contact info, or location strings. Use error class names, HTTP
codes, missing-config keys, count summaries.

## 4. What's pulled regularly to establish "current truth"

- **Per fetch-only run**: each connector's new messages → enqueued into
  `state.text_queue`; `last_run[source]` advanced.
- **Per worker job**: upcoming-4-weeks fetch from primary + weekend
  (`calendar_analyzer.fetch_upcoming`) for prompt context; cross-
  calendar dedup pulls from `state.calendar_snapshot`.
- **At digest send**: year-ahead fetch from both calendars
  (`calendar_analyzer.fetch_year_ahead`) → `state.calendar_snapshot`
  for next-run diff.
- **Cached**: `last_run[source]`, `calendar_snapshot`,
  `processed_slack_files` (90 d), `todoist_project_id`.

## 5. What triggers the LLM

Two queues consumed by a long-running worker (Tier 2.4):

- **Producer**: `main.py fetch-only` (cron every 10 min) calls each
  connector and `state.enqueue_text_job(...)`. Cheap, no LLM calls.
- **Producer (events)**: dispatcher writes file paths into
  `state.ocr_queue` when an image lands in `#ian-image-intake`.
- **Consumer**: `main.py worker` (long-running, KeepAlive plist).
  Loop: pop a job, run it, persist. Idle-sleeps 30 s when both queues
  are empty.

Per-text-job pipeline:
1. **Pre-classifier** (Tier 2.5): cheap qwen3 call at 2 k ctx —
   "yes/no/maybe contains an event/todo?". "no" → mark seen, skip.
2. **Calendar context refresh** (per job — handles drift between fetch
   and processing).
3. **Full extraction** at 16 k ctx (`extractor.extract`).
4. **Routing**:
   - Event candidates → `_propose_events()` (propose mode) or
     `_auto_create_events()` (auto mode).
   - Todo candidates → kind:"todo" proposal item (propose mode) or
     auto-create (auto mode).

Per-OCR-job pipeline: see `worker._run_ocr_job` → `cli._cmd_ingest_image`.
Pages rasterized via `pypdfium2`; each page → vision call; results
merged → calendar-detect text call; CandidateEvents flow through the
same proposal path as text-extracted events.

## 6. AI models

| Task | Model | Config | Context | keep_alive |
|---|---|---|---|---|
| Pre-classifier | qwen3:14b | `OLLAMA_MODEL` | `PRE_CLASSIFIER_NUM_CTX=2048` | `OLLAMA_KEEP_ALIVE_TEXT=-1` |
| Text extraction (events + todos) | qwen3:14b | `OLLAMA_MODEL` | `OLLAMA_NUM_CTX_TEXT=16384` | `OLLAMA_KEEP_ALIVE_TEXT=-1` |
| Vision + per-page OCR | qwen2.5vl:7b | `LOCAL_VISION_MODEL` | `OLLAMA_NUM_CTX_VISION=16384` | `OLLAMA_KEEP_ALIVE_VISION=30s` |
| Calendar detection on doc text | qwen3:14b | reuses `OLLAMA_MODEL` | `OLLAMA_NUM_CTX_TEXT` | reuses `OLLAMA_KEEP_ALIVE_TEXT` |
| Calendar conflict / cluster analysis | none — pure Python | — | — | — |

No cloud fallback (deliberately removed 2026-04-24). All Ollama calls
use `format:"json"`, `think:False`. Vision pins `temperature:0.1`.

**Both models cannot be hot simultaneously** (24 GB mini RAM budget):
qwen3 hot ≈ 14 GB, vision hot ≈ 9 GB — together = 23 GB before OS +
dispatcher + finance-monitor. Worker enforces serial loading via
`worker._ollama_unload` / `_ollama_warmup` with explicit `keep_alive=0`
to free memory immediately during a swap (Tier 2.4).

## 7. Result parsing

Transport: `POST /api/generate` → `json.loads(resp.response)`.

Retries:
- Text extraction: 3 attempts, 1 s/2 s exponential backoff. Final
  failure → returns `[], []` silently AND
  `state.mark_ollama_down(skipped_count)` so the dashboard surfaces it.
- Vision: matched to text path, 3 attempts (Tier 1.3). Bounded.
- Calendar-detect: 3 attempts.
- Pre-classifier: fail-open — any error returns `("maybe", reason)` so
  we never silently drop a real event due to a transient glitch.

Validation: `_validate_event` / `_validate_todo` — strict types, length
caps, regex checks (email, due date), enum check (category, priority).
`_merge_page_results` defends against `null` fields with `or <default>`
on every getter.

Confidence banding: per-source thresholds (`config.CONFIDENCE_BANDS`).
Below `medium` → drop. `medium ≤ x < high` → `[?]` prefix on title.
`≥ high` → normal. Update/cancel signals require `≥ 0.75`.

`date_certainty == "unknown"` skips date validation entirely; routes to
fuzzy_event proposal with the model-provided `event_description`.

## 8. Slack outputs

Every Slack post lands in `SLACK_NOTIFY_CHANNEL` (default
`ian-event-aggregator`).

Propose mode (default): a single dashboard message per day, edited
in-place via `chat_update` until buried beyond
`DASHBOARD_REPOST_AFTER_N=20` messages — at which point a fresh post
replaces it (Tier 3.2). Dashboard sections (top to bottom):

1. **Header** (date)
2. **Swap decisions** (Tier 3.4) — pending OCR-vs-text-queue prompts
   with [Wait] / [Interrupt] buttons
3. **Decisions** (pending proposals) — events, merges, fuzzy events,
   todos. Capped at 25 visible items.
4. **Notices** — recurring-event hints (Tier 2.1)
5. **Today's actions** — full list if ≤ 3, else collapsed summary
   (`:white_check_mark: 3 added · :x: 1 skipped`)
6. **Errors** — Ollama-down banner when applicable
7. **Footer** — `N pending · last run HH:MM · queue: X text · Y ocr`

Auto mode (legacy): events post into a Slack day thread protected by
`fcntl.flock`. Run summaries still post a thread reply at end of run.
Propose mode no longer posts a run summary — the dashboard footer is
the summary.

File-intake results (`post_file_result`) are still in place for the
dispatcher's reply path.

## 9. Google Calendar writes (two-calendar model — Tier 2.2)

- **Primary calendar** (`GCAL_PRIMARY_CALENDAR_ID`, default `primary`):
  read for context + dedup. The tool **never writes spontaneously**;
  every primary patch goes through the proposal flow as a `kind:"merge"`
  item.
- **Weekend calendar** (`GCAL_WEEKEND_CALENDAR_ID`): the write target.
  New events insert here; additive merges patch silently with a
  dashboard notice.

`writers/google_calendar.write_event` returns one of:

- `Inserted` — new event on weekend
- `Merged` — silent additive patch on weekend (caller emits notice)
- `MergeRequired` — primary match; caller emits a kind:"merge" proposal
- `Skipped` — duplicate or error

Body builder maps:
- `title` (with `[?]` if medium-band) → `summary`
- `start_dt` / `end_dt` (or `+1h`) with `USER_TIMEZONE`
- `location` (optional)
- `description = "[<action> via event-aggregator | source: <src>{ | <url>}]"`
- `colorId` from `CATEGORY_COLORS`

`update_event(target_calendar_id, gcal_event_id, candidate)` and
`delete_event(target_calendar_id, gcal_event_id)` both require an
explicit calendar ID — the writer never assumes which calendar to
target.

`_resolve_gcal_id` (in `main.py`) returns `(gcal_event_id, calendar_id)`
so update/cancel know where to land.

## 10. Decision tracking (`state.json`)

Atomic writes via tempfile + `os.replace`.

| Key | Purpose | Pruning |
|---|---|---|
| `text_queue[]`, `ocr_queue[]` | worker queues | drained by worker |
| `worker_status{}` | dashboard footer | overwritten each loop |
| `swap_decisions{}` | `[Wait]/[Interrupt]` lifecycle | auto-resolve to "wait" after 5 min |
| `pending_proposals[]` | dashboard items (event/merge/fuzzy_event/todo) | actioned + 72 h |
| `proposal_counter` / `_date` | daily-resetting numeric ID | midnight UTC |
| `proposal_dashboard{date: ts}` | Slack message ts per day | 7 d |
| `dashboard_buried{date: count}` | repost-when-buried counter | bumped by dispatcher; reset on repost |
| `written_events{gcal_id: {..., calendar_id}}` | audit + update lookup | 200 newest |
| `written_fingerprints[]` | event dedup | last 5000 |
| `rejected_fingerprints{fp: {...}}` | rejection memory (Tier 1.6) | 90 d |
| `written_todo_fingerprints[]` | todo dedup | last 5000 |
| `seen_message_ids{source: [{id, ts}]}` | per-source extraction dedup | 30 d / 1000 per source |
| `last_run{source}` | watermark | refreshed by fetch-only |
| `calendar_snapshot{gcal_id: {...}}` | year-ahead snapshot for digests | refreshed at digest send |
| `recurring_notices[]` | "saw something recurring" surface | 24 h |
| `ollama_health{down_since, skipped_count}` | dashboard error block | cleared when Ollama recovers |
| `last_digest_daily/weekly`, `warned_conflict_ids`, `day_thread_ts/_date`, `processed_slack_files`, `todoist_project_id` | various | various |

Approval entry points:
- Dispatcher: `@app.action("ea_approve" | "ea_reject" | "ea_swap_wait" | "ea_swap_interrupt")` → CLI subcommand
- CLI: `cli approve <num>`, `cli reject <num>`, `cli forget [--fp X]`,
  `cli swap --decision-id X --decision wait|interrupt`,
  `cli bump-dashboard`

Audit log: `event_log.jsonl` append-only.

## 11. Dedup layers

1. **Message-ID** (fetch time): `state.is_seen(source, id)`. 30 d / 1000 per source.
2. **Fingerprint** (Tier 1.5): `sha256(title.lower().strip() + YYYY-MM-DDTHH)`. Hour-bucketed so a 2 pm → 4 pm reschedule produces a distinct fingerprint. Checked against both `written_fingerprints` and `rejected_fingerprints`.
3. **In-batch + cross-run fuzzy + window** (`dedup.is_duplicate` + `dedup.persisted_events`): fingerprint match OR `fuzz.ratio > 85` AND `|Δt| ≤ 60 min`. Now consults `state.written_events` (last 30 days) and pending proposals — Layer 3 catches reschedules across runs that the per-hour fingerprint misses.
4. **Target calendar ±1 day live scan**: `fuzz.ratio > 85` AND `|Δt| ≤ 30 min` → skip.
5. **Cross-calendar snapshot**: same date + `fuzz.ratio > 80`. Branches:
   - match on **primary** + new fields → `MergeRequired` proposal
   - match on **primary** + nothing new → silent skip
   - match on **weekend** + new fields → silent patch + notice
   - match on **weekend** + nothing new → silent skip

Recurring events: dropped from writes; surfaced as a 24 h notice.

Update vs duplicate: `_resolve_gcal_id` fuzzy matches against
`written_events ∪ calendar_snapshot` (`fuzz.ratio > 75`); match → patch
path with the resolved `(gcal_event_id, calendar_id)`.

Todo dedup: `sha256(title + source + source_id)` — narrower than
events (per-message rather than date-based).

Conflict-warning dedup: `state.warned_conflict_ids[fp] = date`, 30 d window.

## 12. Run cadence

Production runtime is **fetch-only timer + worker daemon** (Tier 2.4):

- `com.home-tools.event-aggregator.fetch.plist` — `StartInterval=600`,
  `RunAtLoad=false`. Runs `main.py fetch-only` every 10 min.
- `com.home-tools.event-aggregator.worker.plist` — `KeepAlive=true`,
  `RunAtLoad=true`, `ThrottleInterval=10`. Runs `main.py worker`.

Log files land in `~/Library/Logs/home-tools/` (persistent across reboots):
- `event-aggregator-worker.log` — worker stdout + stderr (combined)
- `event-aggregator-fetch.log` — fetch stdout + stderr (combined)

To tail live: `tail -f ~/Library/Logs/home-tools/event-aggregator-worker.log`

Legacy single-plist mode (`main.py` no-args, full inline run) still
works for backward compat and is what the original
`com.home-tools.event-aggregator.plist` invokes.

---

## File index

| Concern | Files |
|---|---|
| Run-loop legacy | `main.py:main()` |
| Fetch loop | `main.py:fetch_only()` |
| Worker | `worker.py` |
| Connectors | `connectors/{gmail,google_calendar,slack,imessage,whatsapp,discord_conn,notifications}.py` |
| LLM extraction | `extractor.py`, `models.py` |
| Vision pipeline | `analyzers/image_analyzer.py`, `image_pipeline.py` |
| Calendar context | `analyzers/calendar_analyzer.py` |
| Dedup | `dedup.py` |
| State | `state.py`, `state.json` |
| Slack output | `notifiers/slack_notifier.py`, dispatcher `slack_bot.py` |
| GCal writes | `writers/google_calendar.py` |
| Todoist | `writers/todoist_writer.py` |
| CLI subcommands | `cli.py` |
| Audit log | `logs/event_log.py`, `event_log.jsonl` |
| Config | `config.py`, `.env` |
| Tests | `tests/` (120 pass, 4 skip Ollama-live) |

---

## Tunables (config.py / .env)

- `OLLAMA_NUM_CTX_TEXT` / `OLLAMA_NUM_CTX_VISION` — context ceilings
- `OLLAMA_KEEP_ALIVE_TEXT` / `OLLAMA_KEEP_ALIVE_VISION` — model lifetime
- `PRE_CLASSIFIER_ENABLED` / `PRE_CLASSIFIER_NUM_CTX`
- `GCAL_PRIMARY_CALENDAR_ID` / `GCAL_WEEKEND_CALENDAR_ID`
- `CALENDAR_CONTEXT_WEEKS`
- `CONFIDENCE_BANDS` — per-source confidence thresholds
- `EVENT_APPROVAL_MODE` — "propose" (default) or "auto"
- `PROPOSAL_EXPIRY_HOURS` — auto-expire stale proposals
- `DASHBOARD_REPOST_AFTER_N` — when the dashboard reposts
- `TODOIST_API_TOKEN` / `TODOIST_PROJECT_NAME` /
  `TODOIST_TODO_MIN_CONFIDENCE`
- `IMAGE_CONFIDENCE_MIN` — confidence floor before image is filed in
  "Documents"
- `USER_TIMEZONE`, `DIGEST_DAILY_HOUR`, `DIGEST_WEEKLY_DOW`

---

## Future improvements (out of current scope)

### Messenger / Instagram intake (deferred 2026-04-28)

Removed from `_CONNECTOR_REGISTRY` because macOS Sequoia 15+ removed the
per-app Notification Center DB at
`~/Library/Application Support/NotificationCenter/*.db`. The
`NotificationCenterConnector` class (`connectors/notifications.py`),
mock fixtures (`tests/mock_data.py:notification_messages`), and the
extractor's `"messenger"`/`"instagram"` source-type entries
(`extractor.py`) are kept intact for future re-enablement.

To re-enable: add the two registry entries back to `main.py:_CONNECTOR_REGISTRY`
and stale `connector_health` entries on the mini will get refreshed on
the next fetch cycle.

Possible paths to restore (any of):
1. Apple restores per-app NC DB in a future macOS update — re-enable with
   no code change.
2. Use `osascript` / Apple Script Bridge to query Messages.app directly
   via the Messenger / Instagram macOS desktop apps. Heavyweight; bundle
   identifier sniffing was the lighter path.
3. Native Messenger/Instagram APIs (Meta Graph API). Requires app
   review; unsuitable for a personal home tool.
4. macOS `UNUserNotificationCenter` shim via a small SwiftUI helper
   that exposes notifications over a local socket. Most promising
   long-term; meaningful build effort.

Priority: low. The user's traffic on these channels rarely contains
events worth surfacing. Revisit if Apple restores the DB or a
zero-build path emerges.

### Other deferred items (recorded by Tier 2 audit on 2026-04-28)

- **`cli intake-ack <source>`** — manual acknowledgement to suppress a
  steady-state intake-health item from the Slack dashboard for 24h.
  Reduces dashboard noise once a broken state is known and accepted.
- **Centralized `ConnectorAuthManager`** — unified credential lifecycle
  for Slack/Discord/Google. Defer until token rotation becomes a real
  burden (currently each source manages its own).
- **Per-connector unit tests with realistic API fixtures** — Tier 3.1's
  `test_connector_contract.py` covers smoke-level conformance only.
- **Worker watchdog + claim-timeout** — guard against an Ollama hang
  leaving a job claimed indefinitely. The 2026-04-28 stuck-job report
  resolved itself after the worker restart but the underlying race
  remains.
- **`connector_health` pruning in `state.prune()`** — stale entries
  (e.g. for sources removed from the registry) currently linger
  forever. Manual cleanup works for now; auto-prune by
  `last_status_at < now - 7d` would tidy.
