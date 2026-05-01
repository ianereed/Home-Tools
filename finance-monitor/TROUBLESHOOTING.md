# Finance Monitor — Troubleshooting Runbook

Quick start: SSH to the mini (`ssh homeserver@homeserver`). All commands below assume that session.

---

## Symptom: Slack DM stuck on "_Thinking..._" (or staged label) for >2 min

Expected upper bound:
- Transaction-mode answer: ~30s after first token.
- Doc-mode answer: ~60–90s.
- First query after model unload: add ~10s for qwen3:14b cold-load.

If you've waited longer, work through these in order.

### 1. Is the bot process alive?

```sh
launchctl list | grep finance-monitor
```

Expected: a row with a numeric PID and exit `0`. If status is `-`, the bot is dead.
- `launchctl unload && launchctl load ~/Library/LaunchAgents/com.home-tools.finance-monitor.plist` to restart.

### 2. Is Ollama up and the model resident?

```sh
curl -sS --max-time 5 http://localhost:11434/api/ps
curl -sS --max-time 5 http://localhost:11434/api/tags | python3 -c "import sys,json; print([m['name'] for m in json.load(sys.stdin).get('models',[])])"
```

- `/api/ps` lists models currently loaded in memory. After the keep_alive 30m change, qwen3:14b should stay resident between queries.
- `/api/tags` lists installed models. Need at least `qwen3:14b` and `qwen2.5vl:7b`.

If Ollama isn't responding: `brew services restart ollama` (or `ollama serve` in a screen if using non-brew).

### 3. What stage was the bot in when it stuck?

```sh
tail -n 30 ~/Library/Logs/home-tools-finance-monitor.log
```

Look for:
- `received DM from Uxxx (len=N)` — bot got the message.
- `prompt chars=N (~tokens), num_ctx=…` — bot reached `_call_ollama`. If this is the last line, Ollama hung.
- No log past `received DM` — bot is somewhere in `query_engine.answer` (DB fetch, mode classification, etc.) — almost always Ollama.

### 4. Is the worker thread genuinely blocked?

```sh
PID=$(launchctl list | awk '/com.home-tools.finance-monitor\b/ {print $1}')
sample $PID 5 2>&1 | head -80
```

What you want to see:
- One thread in `_ssl__SSLSocket_read` → idle Slack Socket Mode WS read (normal).
- A worker thread in `_pthread_cond_wait` waiting on a `requests` call → that's the Ollama call hanging.
- All threads idle → bot already finished and posted; you're looking at the wrong thread.

### 5. Force-unblock

If Ollama is wedged: `kill $(pgrep -f ollama_runner)` — runs free and the next request reloads the model fresh.

If the bot itself is wedged with `_in_flight` stuck for a sender (you'll see "_Still working on your last question_" forever):
```sh
launchctl unload ~/Library/LaunchAgents/com.home-tools.finance-monitor.plist
launchctl load ~/Library/LaunchAgents/com.home-tools.finance-monitor.plist
```
KeepAlive=true means it'll come right back; in-memory `_in_flight` set resets.

---

## Symptom: bot doesn't reply at all (no "Thinking..." appears)

### 1. Did the bot even see the DM?

```sh
tail -n 30 ~/Library/Logs/home-tools-finance-monitor.log
```

If there's no `received DM from Uxxx` log: the message didn't reach the handler. Check:
- Bot's Socket Mode connection — last log line should be a recent `Bolt app is running` / `new connection`.
- Slack workspace token validity (rotate `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` in `.env` if expired).

### 2. Allowlist mismatch

If you see `rejected DM from unauthorized user U…` — your Slack user ID changed (rare) or the `.env` was edited. `.env` line: `ALLOWED_SLACK_USER_IDS=U…`.

### 3. Greeting filter caught it

`hi` / `help` / `?` / `thanks` / `thank you` / `hey` get the static help text instead of the LLM. By design.

---

## Symptom: image upload to #ian-image-intake — no thread callback after 10+ min

The flow: dispatcher → finance-monitor/intake → watcher (every 5 min) → image_importer → Slack callback.

### 1. Did the dispatcher receive and route the file?

```sh
tail -n 30 ~/Library/Logs/home-tools-dispatcher.log
ls -la ~/Home-Tools/dispatcher/tmp/   # files here = downloaded but not yet routed
ls -la ~/Home-Tools/finance-monitor/intake/   # files here = waiting for watcher
```

If file is stuck in `dispatcher/tmp/` with no log activity: dispatcher was killed mid-classify (e.g., during a deploy). See **Recovery: stuck file in dispatcher/tmp/** below.

### 2. Did the watcher run?

```sh
tail -n 30 ~/Library/Logs/home-tools-finance-monitor-watcher.log
```

Watcher fires every 5 min. Each run logs `ynab sync → {...}` and (if files present) `importing image: …`. If you don't see a recent run, the LaunchAgent is broken — `launchctl list | grep watcher`.

### 3. Did OCR succeed?

In the watcher log, look for:
- `transaction <file> | <date> <merchant> (<amount>)` → success path, transaction inserted.
- `<file> looks non-textual — leaving as document` → OCR returned `NOT_A_DOCUMENT` / very short. File saved as document, not transaction.
- `OCR failed for <file> (attempt N/3)` → Ollama unreachable. Will retry; quarantines after 3.

### 4. Is the sidecar present?

The dispatcher writes `<file>.thread.json` next to the file in intake/. Without it, no Slack callback fires (the file still gets processed, you just don't see a confirmation).

```sh
ls ~/Home-Tools/finance-monitor/intake/ ~/Home-Tools/finance-monitor/imported/2026-*/  | grep thread.json
```

If a sidecar exists for an in-flight file, the callback should fire after the watcher processes it. If no sidecar exists, the dispatcher route happened before the sidecar code shipped, OR `slack_thread` wasn't passed (check `dispatcher/router.py` and `dispatcher/slack_bot.py`).

### 5. DB sanity check — did the row actually land?

```sh
cd ~/Home-Tools/finance-monitor
.venv/bin/python3 -c "
import db
conn = db.get_connection()
for r in conn.execute(\"SELECT date, payee, amount, raw_file FROM transactions WHERE source='image_import' ORDER BY imported_at DESC LIMIT 10\").fetchall():
    print(dict(r))
"
```

Pre-fix bug (commit `724ce65`): `_insert_transaction` omitted `imported_at`, so `INSERT OR IGNORE` silently dropped every receipt. If you see a "transaction inserted" log line but no DB row, that bug regressed.

---

## Symptom: receipt OCR'd correctly but DB row missing

See "DB sanity check" above. The post-fix `_insert_transaction` returns `True` even on dedup hits (so the watcher moves the file out of intake/). Distinguish:
- Log says `transaction <file> | …` → fresh insert; should be in DB.
- Log says `<file> already present — skipping insert` → dedup. Look for the older row by date+payee+amount.
- If neither log line appears → fell through to `_insert_document` (check `documents` table).

---

## Symptom: watcher hasn't synced YNAB

```sh
tail -n 50 ~/Library/Logs/home-tools-finance-monitor-watcher.log | grep -i "ynab"
```

Each tick logs `ynab sync → {'txn_upserts': N, 'txn_deletes': M, 'months_snapshotted': 1}`. If you see `error` instead:
- `403 Unauthorized` → `YNAB_API_TOKEN` expired. Rotate in YNAB → Account Settings → Developer Settings.
- `Connection refused` → outbound network broken on mini.
- `RuntimeError: budget...` → budget ID resolution failed; multi-budget account requires `YNAB_BUDGET_ID` in `.env`.

---

## Recovery procedures

### Stuck file in dispatcher/tmp/

```sh
INTAKE=~/Home-Tools/finance-monitor/intake
TMP=~/Home-Tools/dispatcher/tmp

# Move and re-stamp so the watcher picks it up. Original prefix is unix-ts_filename.
for f in "$TMP"/*_*.{jpg,jpeg,png,heic,heif,pdf,gif,webp,tiff,tif}; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    new_name="$(date +%Y%m%d_%H%M%S)_${base#*_}"
    mv "$f" "$INTAKE/$new_name"
    echo "moved $f → intake/$new_name"
done
```

Then trigger the watcher manually instead of waiting up to 5 min:

```sh
cd ~/Home-Tools/finance-monitor && .venv/bin/python3 main.py watch
```

Note: this path can't recreate the Slack thread context — no callback will fire. Use only when the dispatcher abandoned a file, not for normal operation.

### Reset the watcher's flock

The watcher holds a non-blocking flock on `data/watcher.lock` for the duration of a run. If the process crashed without releasing (unusual — POSIX releases flock on exit), simply delete the file.

```sh
rm -f ~/Home-Tools/finance-monitor/data/watcher.lock
```

### Quarantined images

Images that fail OCR 3 times land in `intake/quarantine/`. To retry one:

```sh
mv ~/Home-Tools/finance-monitor/intake/quarantine/<file> ~/Home-Tools/finance-monitor/intake/
rm ~/Home-Tools/finance-monitor/intake/quarantine/<file>.thread.json  # or move it back too
```

---

## Log locations (post 2026-04-24)

All under `~/Library/Logs/`:
- `home-tools-finance-monitor.log` — Slack bot (DMs).
- `home-tools-finance-monitor-watcher.log` — 5-min intake loop + YNAB sync.
- `home-tools-dispatcher.log` — image-intake routing + interactive commands.
- Each has a matching `-error.log` (usually empty; only stderr-only output lands there).

`/tmp/home-tools-*.log` is no longer used; safe to `rm -f /tmp/home-tools-*.log` if old files reappear.

---

## Useful diagnostic commands

```sh
# What's running?
launchctl list | grep home-tools

# Stack-sample a stuck Python (5s capture)
sample <PID> 5 2>&1 | head -100

# Subprocess children of a Python (e.g., dispatcher mid-classify)
pgrep -P <PID> -lf

# Ollama health + loaded models
curl -sS --max-time 5 http://localhost:11434/api/ps
curl -sS --max-time 5 http://localhost:11434/api/tags | python3 -m json.tool | head -40

# Manual one-shot watcher tick
cd ~/Home-Tools/finance-monitor && .venv/bin/python3 main.py watch

# Manual one-shot YNAB sync
cd ~/Home-Tools/finance-monitor && .venv/bin/python3 main.py sync

# DB stats
cd ~/Home-Tools/finance-monitor && .venv/bin/python3 main.py stats
```
