# finance-monitor

Local Q&A and read-only YNAB sync for personal finance. A Slack DM bot answers plain-English questions ("how much did I spend on restaurants last month?") by querying a local SQLite that mirrors YNAB transactions, budget snapshots, OCR'd receipts, and PDF advisor docs. All processing local. Financial data never leaves the mini.

## What it is

```
  YNAB API ──5min sync──┐
  Slack image upload ──▶ dispatcher ──▶ image_importer (qwen2.5vl)
  PDF in intake/      ──▶ pdf_importer (pdfplumber)              ──▶ finance.db ──▶ query_engine (qwen3:14b) ──▶ Slack DM
  YNAB CSV (historical) ──────────────▶ ynab_csv ingester
```

## Status

**LIVE** on the Mac mini since 2026-04-24. Phases 1 + 2 complete. Phase 3 deferred.

Two LaunchAgents:
- `com.home-tools.finance-monitor` — KeepAlive Slack DM bot (Socket Mode)
- `com.home-tools.finance-monitor-watcher` — 5-min file-intake watcher + YNAB API delta sync

Comprehensive runbook at [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) covering stuck DMs, image-callback flow, OCR failures, YNAB sync errors, recovery procedures.

## Audience

Single-user (you). Slack DM allowlist-locked. Designed to never become a multi-user system.

## Operational notes

- **`ALLOWED_SLACK_USER_IDS` allowlist in `.env` is mandatory.** The bot refuses to start with an empty allowlist (`config.py:112`). Per-user 60s rate limit. Sender ID logged on every message.
- **YNAB API is read-only by construction.** `YnabClient` exposes ONLY `.get()`. The PAT itself has full read+write at YNAB's level — read-only is enforced **client-side**. Never add write methods.
- **Cutover date**: 2026-04-24. CSV imports retired from that date forward; everything since is YNAB API. Set `YNAB_API_CUTOFF=2026-04-24` in `.env`.
- **No LangChain** (active CVEs: CVE-2025-68664, CVE-2024-36480). Don't add it.
- **All data local**: SQLite at `db/finance.db`, Ollama on `127.0.0.1:11434`. No cloud LLM fallback.
- Slack bot is **DM-only** — never posts to channels. Hard rule.

## Phase 1 — Local Q&A (DONE 2026-04-23)

- YNAB CSV ingestion (`ingest/ynab_csv.py`)
- PDF ingestion via pdfplumber (`ingest/pdf_importer.py`) for advisor plan docs
- Image OCR via qwen2.5vl:7b (`ingest/image_importer.py`)
- Plain-English Q&A via qwen3:14b (`query_engine.py`)
- Slack DM bot (`slack_bot.py`) — Socket Mode, dedicated Finance Bot Slack app

## Phase 2 — Read-only YNAB API sync (DONE 2026-04-24)

- `ingest/ynab_api.py` — `YnabClient` with `.get()` only
- New SQLite tables: `budget_months` (per-month per-category budgeted/activity/balance) + `sync_state` (cursor)
- Delta via YNAB's `last_knowledge_of_server` cursor; deleted-transaction propagation
- Wired into the 5-min watcher LaunchAgent
- Manual: `python main.py sync`
- One-time CSV cleanup deletes `ynab_csv` rows dated ≥ cutoff

## Phase 3 — Deferred

- Amazon order reconciliation via Gmail API
- Daily/weekly Pushover spending digests (will reuse `Mac-mini/PLAN.md` Phase 6 `notify.sh`)
- Anomaly detection

## Usage

```bash
# DM the Finance Bot in Slack:
"How much did I spend on restaurants last month?"
"Analyze the portfolio allocation in the financial plan"

# CLI:
python main.py ask "What were my top 5 categories this month?"
python main.py stats   # by-source counts + month count
python main.py sync    # one-shot YNAB delta sync
```

## Reference

- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — comprehensive runbook
- Memory: `project_finance_monitor.md`
- `Mac-mini/PLAN.md` Phase 8 — implementation history
