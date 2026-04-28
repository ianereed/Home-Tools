"""
CLI subcommands invoked by the dispatcher (and usable directly).

  python main.py classify --file <path>        # JSON to stdout
  python main.py ingest-image --file <path>    # full event-extract + NAS stage + proposal
  python main.py approve [--nums "1,3"]        # approve pending proposals
  python main.py reject [--nums "2"]           # reject pending proposals
  python main.py add-event --text "<desc>"     # manual event via extractor → proposal
  python main.py status [--json|--pending|--last-run]
  python main.py query --calendar "<timeframe>" | --conflicts "<timeframe>"

Running `python main.py` with no subcommand preserves existing behavior
(full scan of all sources).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(prog="main.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("classify", help="Classify a file locally; emit JSON to stdout")
    p.add_argument("--file", required=True)

    p = sub.add_parser("ingest-image", help="Full event-extraction pipeline on a local file (inline; used by worker)")
    p.add_argument("--file", required=True)

    p = sub.add_parser("enqueue-image", help="Enqueue a file path into the OCR queue for the worker to process")
    p.add_argument("--file", required=True)

    p = sub.add_parser("approve", help="Approve pending proposals")
    p.add_argument("--nums", default="", help="Comma-separated numbers; omit for all")

    p = sub.add_parser("reject", help="Reject pending proposals")
    p.add_argument("--nums", default="")

    p = sub.add_parser("decide", help="Apply a mixed batch of approves and rejects in one transaction")
    p.add_argument("--approve", default="", help="Comma-separated proposal numbers, or 'all'")
    p.add_argument("--reject", default="", help="Comma-separated proposal numbers, or 'all'")

    p = sub.add_parser("add-event", help="Manual event via extractor")
    p.add_argument("--text", required=True)

    p = sub.add_parser("status", help="Inspect state.json")
    p.add_argument("--json", action="store_true")
    p.add_argument("--pending", action="store_true")
    p.add_argument("--last-run", action="store_true")

    p = sub.add_parser("query", help="Ask qwen3 about the calendar")
    p.add_argument("--calendar", default="")
    p.add_argument("--conflicts", default="")

    p = sub.add_parser("config", help="Toggle SLACK_MONITOR_CHANNELS in .env atomically")
    p.add_argument("--mute", default="", help="Channel name to remove")
    p.add_argument("--watch", default="", help="Channel name to add")
    p.add_argument("--list-channels", action="store_true", help="List currently watched channels")

    p = sub.add_parser("undo-last", help="Delete the most recently written GCal event")

    p = sub.add_parser("changes", help="Show calendar changes from event_log.jsonl")
    p.add_argument("--since", default="1d", help="ISO date/datetime, or relative like 1d/12h/30m")

    p = sub.add_parser("forget", help="Wipe a previously-rejected fingerprint so the event can be re-proposed")
    p.add_argument("--fp", default="", help="Specific fingerprint to forget; omit to list rejected fps")
    p.add_argument("--title", default="", help="Substring match against rejected event title")

    p = sub.add_parser("swap", help="Resolve a pending OCR swap-decision (used by Slack [Wait]/[Interrupt] buttons)")
    p.add_argument("--decision-id", required=True)
    p.add_argument("--decision", choices=["wait", "interrupt"], required=True)

    p = sub.add_parser("bump-dashboard", help="Increment the dashboard-burial counter (called by the dispatcher when a non-bot message arrives in the interactive channel)")

    args = parser.parse_args()

    # Quiet default logging when emitting JSON on stdout; the dispatcher parses it.
    if args.cmd in ("classify",):
        logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )

    if args.cmd == "classify":
        return _cmd_classify(Path(args.file))
    if args.cmd == "ingest-image":
        return _cmd_ingest_image(Path(args.file))
    if args.cmd == "enqueue-image":
        return _cmd_enqueue_image(Path(args.file))
    if args.cmd == "approve":
        return _cmd_approve_or_reject(args.nums, approve=True)
    if args.cmd == "reject":
        return _cmd_approve_or_reject(args.nums, approve=False)
    if args.cmd == "decide":
        return _cmd_decide(args.approve, args.reject)
    if args.cmd == "add-event":
        return _cmd_add_event(args.text)
    if args.cmd == "status":
        return _cmd_status(as_json=args.json, only_pending=args.pending, only_last_run=args.last_run)
    if args.cmd == "query":
        return _cmd_query(calendar=args.calendar, conflicts=args.conflicts)
    if args.cmd == "config":
        return _cmd_config(mute=args.mute, watch=args.watch, list_channels=args.list_channels)
    if args.cmd == "undo-last":
        return _cmd_undo_last()
    if args.cmd == "changes":
        return _cmd_changes(since=args.since)
    if args.cmd == "forget":
        return _cmd_forget(fp=args.fp, title=args.title)
    if args.cmd == "swap":
        return _cmd_swap(decision_id=args.decision_id, decision=args.decision)
    if args.cmd == "bump-dashboard":
        return _cmd_bump_dashboard()
    return 1


# ── classify ──────────────────────────────────────────────────────────────────

def _cmd_classify(file: Path) -> int:
    if not file.exists():
        _json_err(f"file not found: {file}")
        return 2

    from analyzers import image_analyzer

    mimetype = _mimetype_for(file)
    file_bytes = file.read_bytes()
    try:
        pages = image_analyzer.rasterize_to_pages(file_bytes, file.name, mimetype)
    except Exception as exc:
        _json_err(f"rasterize failed: {exc}")
        return 2

    # Classify via vision model; skip calendar detection (classify is a cheap probe
    # called by the dispatcher; full event extraction happens in ingest-image).
    if len(pages) == 1:
        page_dict = image_analyzer._analyze_page_local(*pages[0])
        if not page_dict:
            _json_err("local vision failed (qwen2.5vl unavailable or returned no data)")
            return 2
        merged = image_analyzer._merge_page_results([page_dict], [pages[0][1]])
    else:
        merged = image_analyzer._analyze_local_no_calendar(pages)

    if merged is None:
        _json_err("analysis returned None")
        return 2

    print(json.dumps({
        "category": merged.primary_category,
        "subcategory": merged.subcategory,
        "doc_type": merged.document_type,
        "confidence": merged.confidence,
        "title": merged.title,
        "date": merged.date,
        "suggested_filename": _slug(merged.title) + file.suffix,
    }))
    return 0


# ── ingest-image ──────────────────────────────────────────────────────────────

def _cmd_ingest_image(file: Path) -> int:
    """Run the full image_pipeline on a local file: classify, stage, NAS-copy,
    extract calendar items, post proposals to Slack."""
    if not file.exists():
        print(f"file not found: {file}", file=sys.stderr)
        return 2

    import state as state_module
    from image_pipeline import ingest_local_file

    state = state_module.load()
    try:
        summary = ingest_local_file(file, state, dry_run=False, mock=False)
    except Exception as exc:
        logger.warning("ingest-image: failed: %s: %s", type(exc).__name__, exc)
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 2

    state_module.save(state)
    print(summary)
    return 0


def _cmd_enqueue_image(file: Path) -> int:
    """Enqueue a file path for the worker. Used by the dispatcher when an
    image lands in #ian-image-intake — keeps the dispatcher's path quick
    and lets the worker handle the heavy lifting."""
    if not file.exists():
        print(f"file not found: {file}", file=sys.stderr)
        return 2
    import state as state_module
    with state_module.locked():
        state = state_module.load()
        state.enqueue_ocr_job(str(file.resolve()))
        state_module.save(state)
    print(f":inbox_tray: enqueued {file.name} (ocr_queue depth: {state.ocr_queue_depth()})")
    return 0


# ── approve / reject ──────────────────────────────────────────────────────────

def _cmd_approve_or_reject(nums_raw: str, approve: bool) -> int:
    import state as state_module

    state = state_module.load()
    nums = _parse_nums(nums_raw)
    pending = _pending_items(state)

    if not pending:
        print("No pending proposals.")
        return 0

    if not nums:
        # All pending items
        targets = [item["num"] for item in pending]
    else:
        targets = nums

    if approve:
        return _do_approve(state, targets)
    return _do_reject(state, targets)


def _apply_approve(state, nums: list[int]) -> tuple[int, list[str]]:
    """Mutate state by approving each num. Returns (approved_count, errors).
    Saves state. Does NOT refresh the dashboard or print."""
    from datetime import timezone as tz
    from googleapiclient.discovery import build
    import config
    from connectors import google_auth
    from dedup import fingerprint as _fingerprint
    from logs.event_log import record as log_event, record_cancellation, record_decision
    import state as state_module
    from writers import google_calendar as gcal_writer

    # Reuse main.py's _proposal_item_to_candidate
    from main import _proposal_item_to_candidate

    approved = 0
    errors: list[str] = []
    now = datetime.now(timezone.utc)
    snapshot = state.calendar_snapshot()

    for num in nums:
        item = state.approve_proposal(num)
        if item is None:
            errors.append(f"#{num}: not pending")
            continue

        # Fuzzy events: there's no calendar entry to write — approval just
        # marks the item resolved (the user typically used `cli add-event`
        # already, or decided not to). The proposal status is already set
        # to "approved" by state.approve_proposal.
        if item.get("kind") == "fuzzy_event":
            record_decision("approved", item)
            approved += 1
            continue

        # Todo proposals: create the Todoist task on approve. v1 routes to
        # inbox (project_id=None). Tier 4.1 follow-up will add per-project
        # picking via Slack interactive selects.
        if item.get("kind") == "todo":
            from models import CandidateTodo
            from writers import todoist_writer
            if not config.TODOIST_API_TOKEN:
                errors.append(f"#{num}: TODOIST_API_TOKEN not configured")
                continue
            todo = CandidateTodo(
                title=item.get("title", ""),
                source=item.get("source", ""),
                source_id=item.get("source_id", ""),
                source_url=item.get("source_url"),
                confidence=item.get("confidence", 0.5),
                context=item.get("context"),
                due_date=item.get("due_date"),
                priority=item.get("priority", "normal"),
            )
            if todoist_writer.create_task(
                config.TODOIST_API_TOKEN, project_id=None, todo=todo, dry_run=False
            ):
                record_decision("approved", item)
                approved += 1
            else:
                errors.append(f"#{num}: Todoist create_task failed")
            continue

        # Merge proposals (additive patches to primary) take a different path
        if item.get("kind") == "merge":
            target_cal = item.get("target_calendar_id") or config.GCAL_PRIMARY_CALENDAR_ID
            gcal_event_id = item.get("gcal_event_id")
            additions = item.get("additions") or {}
            if not gcal_event_id or not additions:
                errors.append(f"#{num}: merge proposal missing gcal_event_id/additions")
                continue
            candidate = _proposal_item_to_candidate(item)
            if gcal_writer.merge_event(target_cal, gcal_event_id, candidate, additions, dry_run=False):
                record_decision("approved", item)
                approved += 1
            else:
                errors.append(f"#{num}: merge patch failed")
            continue

        candidate = _proposal_item_to_candidate(item)
        if candidate.start_dt < now and not candidate.is_cancellation:
            errors.append(f"#{num}: event time has passed")
            continue

        action = None
        if candidate.is_cancellation and candidate.gcal_event_id_to_update:
            target_cal = candidate.gcal_calendar_id_to_update or config.GCAL_WEEKEND_CALENDAR_ID
            if gcal_writer.delete_event(target_cal, candidate.gcal_event_id_to_update, dry_run=False):
                record_cancellation(
                    gcal_id=candidate.gcal_event_id_to_update,
                    title=candidate.original_title_hint or candidate.title,
                    source=candidate.source,
                )
                action = "cancelled"
        elif candidate.gcal_event_id_to_update:
            target_cal = candidate.gcal_calendar_id_to_update or config.GCAL_WEEKEND_CALENDAR_ID
            written, _conflicts = gcal_writer.update_event(target_cal, candidate.gcal_event_id_to_update, candidate, dry_run=False)
            if written:
                state.add_written_event(
                    gcal_id=written.gcal_event_id,
                    title=candidate.title,
                    start_iso=candidate.start_dt.isoformat(),
                    fingerprint=written.fingerprint,
                    is_tentative=(candidate.confidence_band == "medium"),
                    calendar_id=target_cal,
                )
                log_event(written, action="updated")
                action = "updated"
        else:
            outcome = gcal_writer.write_event(candidate, dry_run=False, snapshot=snapshot)
            if isinstance(outcome, gcal_writer.Inserted):
                written = outcome.written
                state.add_fingerprint(written.fingerprint)
                state.add_written_event(
                    gcal_id=written.gcal_event_id,
                    title=candidate.title,
                    start_iso=candidate.start_dt.isoformat(),
                    fingerprint=written.fingerprint,
                    is_tentative=(candidate.confidence_band == "medium"),
                    calendar_id=config.GCAL_WEEKEND_CALENDAR_ID,
                )
                log_event(written, action="created")
                action = "created"
            elif isinstance(outcome, gcal_writer.Merged):
                # Approval triggered a silent merge (rare — usually caught at propose time)
                action = "merged"
            elif isinstance(outcome, gcal_writer.MergeRequired):
                errors.append(
                    f"#{num}: matched primary event {outcome.matched_title!r}; "
                    "this should have been a merge proposal — check state"
                )

        if action:
            record_decision("approved", item)
            approved += 1

    state_module.save(state)
    return approved, errors


def _do_approve(state, nums: list[int]) -> int:
    """CLI entry point: apply, refresh dashboard, print, return exit code 0."""
    approved, errors = _apply_approve(state, nums)
    _refresh_proposal_dashboard(state)
    msg = f":white_check_mark: {approved} approved"
    if errors:
        msg += "\n" + "\n".join(errors)
    print(msg)
    return 0


def _apply_reject(state, nums: list[int]) -> tuple[int, list[str]]:
    """Mutate state by rejecting each num. Returns (rejected_count, errors).
    Saves state. Does NOT refresh the dashboard or print."""
    from logs.event_log import record_decision
    import state as state_module

    rejected = 0
    errors: list[str] = []

    for num in nums:
        item = state.reject_proposal(num)
        if item is None:
            errors.append(f"#{num}: not pending")
            continue
        fp = item.get("fingerprint")
        if fp:
            # Move the fingerprint from "written" to "rejected" so the same
            # event re-detected from another source stays suppressed (90-day
            # window). User can `cli forget <fp>` if they change their mind.
            state.remove_proposal_fingerprint(fp)
            state.add_rejected_fingerprint(
                fp,
                title=item.get("title", ""),
                source=item.get("source", ""),
            )
        record_decision("rejected", item)
        rejected += 1

    state_module.save(state)
    return rejected, errors


def _do_reject(state, nums: list[int]) -> int:
    """CLI entry point: apply, refresh dashboard, print, return exit code 0."""
    rejected, errors = _apply_reject(state, nums)
    _refresh_proposal_dashboard(state)
    msg = f":x: {rejected} rejected"
    if errors:
        msg += "\n" + "\n".join(errors)
    print(msg)
    return 0


def _refresh_proposal_dashboard(state) -> None:
    """Force-repost the proposal dashboard so it reflects current state.
    Saves state again afterward to persist any new dashboard ts."""
    from notifiers import slack_notifier
    import state as state_module
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_items = state.get_all_proposal_items_for_dashboard(today_str)
    slack_notifier.post_or_update_dashboard(all_items, state, force_repost=True)
    state_module.save(state)


def _cmd_decide(approve_raw: str, reject_raw: str) -> int:
    """Apply a mixed batch of approves and rejects in one transaction.

    Exit codes are structured so the dispatcher can pick a reaction emoji:
       0 — full match (every requested num was actioned)
       1 — zero match (nothing actioned)
       2 — partial match (some requested nums skipped)
    """
    import re as _re
    import state as state_module

    state = state_module.load()
    pending_nums = sorted({item["num"] for item in state.pending_proposals()})

    def _resolve(raw: str) -> list[int]:
        if not raw:
            return []
        if raw.strip().lower() in ("all", "everything"):
            return list(pending_nums)
        return [int(m) for m in _re.findall(r"\d+", raw)]

    a_nums = _resolve(approve_raw)
    r_nums = _resolve(reject_raw)

    if not a_nums and not r_nums:
        print(":x: nothing to do")
        return 1

    a_count, a_errors = _apply_approve(state, a_nums) if a_nums else (0, [])
    r_count, r_errors = _apply_reject(state, r_nums) if r_nums else (0, [])
    _refresh_proposal_dashboard(state)

    requested = len(a_nums) + len(r_nums)
    matched = a_count + r_count
    parts: list[str] = []
    if a_nums:
        parts.append(f"{a_count}/{len(a_nums)} approved")
    if r_nums:
        parts.append(f"{r_count}/{len(r_nums)} rejected")
    head = ", ".join(parts)
    errors = a_errors + r_errors

    if matched == 0:
        print(f":x: nothing matched — {head}")
        if errors:
            print("\n".join(errors))
        return 1
    if matched < requested:
        print(f":warning: {head}")
        if errors:
            print("\n".join(errors))
        return 2
    print(f":white_check_mark: {head}")
    return 0


# ── add-event ────────────────────────────────────────────────────────────────

def _cmd_add_event(text: str) -> int:
    """Pipe freeform text through the extractor and propose any events it finds."""
    import config
    import extractor
    import state as state_module
    from dedup import fingerprint as _fingerprint
    from models import RawMessage
    from notifiers import slack_notifier
    from main import _candidate_to_proposal_item

    state = state_module.load()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    msg = RawMessage(
        id=f"manual_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        source="manual",
        timestamp=datetime.now(timezone.utc),
        body_text=text,
        metadata={"channel": "manual", "sender_name": "ian"},
    )
    events, _todos = extractor.extract(msg)

    if not events:
        print("_No events found in that description. Try including a date and time._")
        return 0

    batch_items = []
    for candidate in events:
        fp = _fingerprint(candidate)
        if state.has_fingerprint(fp):
            continue
        num = state.next_proposal_num()
        batch_items.append(_candidate_to_proposal_item(candidate, num, []))
        state.add_fingerprint(fp)

    if not batch_items:
        print("_Found events, but all look like duplicates of existing ones._")
        state_module.save(state)
        return 0

    batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M:%S_manual")
    batch = {
        "batch_id": batch_id,
        "slack_ts": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": batch_items,
    }
    state.add_proposal_batch(batch)

    # Post/update the live dashboard
    all_items = state.get_all_proposal_items_for_dashboard(today_str)
    slack_notifier.post_or_update_dashboard(all_items, state)

    state_module.save(state)
    lines = [f":memo: proposed {len(batch_items)} event(s):"]
    for item in batch_items:
        start = item.get("start_dt", "")[:16].replace("T", " ")
        lines.append(f"  • #{item['num']} *{item['title']}* — {start}")
    print("\n".join(lines))
    return 0


# ── status ───────────────────────────────────────────────────────────────────

def _cmd_status(as_json: bool, only_pending: bool, only_last_run: bool) -> int:
    import state as state_module

    state = state_module.load()

    if only_pending:
        items = _pending_items(state)
        if not items:
            print("_No pending proposals._")
            return 0
        lines = [f"*{len(items)} pending proposal(s)*"]
        for item in items:
            start = item.get("start_dt", "")[:16].replace("T", " ")
            lines.append(f"  #{item['num']} *{item['title']}* — {start} (conf {item.get('confidence', 0):.2f})")
        print("\n".join(lines))
        return 0

    # Build a compact status dict
    sources = ["gmail", "gcal", "slack", "imessage", "whatsapp", "discord"]
    last_runs = {}
    for s in sources:
        try:
            dt = state.last_run(s)
            last_runs[s] = dt.isoformat() if dt else None
        except Exception:
            last_runs[s] = None
    last_runs["_max"] = max((v for v in last_runs.values() if v), default=None)

    pending_count = len(_pending_items(state))

    status = {
        "last_runs": last_runs,
        "pending_proposals": pending_count,
        "ollama_reachable": _check_ollama(),
    }

    if only_last_run:
        print(f"Last run (any source): {last_runs.get('_max', 'never')}")
        return 0

    if as_json:
        print(json.dumps(status, indent=2))
        return 0

    print(f"*status*  last-run={last_runs.get('_max', 'never')}  "
          f"pending={pending_count}  ollama={'up' if status['ollama_reachable'] else 'down'}")
    return 0


# ── query ────────────────────────────────────────────────────────────────────

def _cmd_query(calendar: str, conflicts: str) -> int:
    """Natural-language GCal Q&A via qwen3."""
    import config
    from googleapiclient.discovery import build
    from connectors import google_auth
    from analyzers import calendar_analyzer
    import requests

    question = calendar or conflicts
    if not question:
        print("_Provide --calendar or --conflicts with a timeframe._")
        return 1

    try:
        creds = google_auth.get_credentials(
            scopes=["https://www.googleapis.com/auth/calendar.events"],
            token_path=config.GCAL_TOKEN_JSON,
            credentials_path=config.GMAIL_CREDENTIALS_JSON,
            keyring_key="gcal_token",
        )
        service = build("calendar", "v3", credentials=creds)
        events = calendar_analyzer.fetch_upcoming(service, weeks=config.CALENDAR_CONTEXT_WEEKS)
    except Exception as exc:
        print(f":x: could not fetch calendar: {exc}")
        return 2

    if not events:
        print("_No upcoming events in the next {}w._".format(config.CALENDAR_CONTEXT_WEEKS))
        return 0

    event_lines = []
    for e in events[:80]:
        try:
            start = e.start_dt.strftime("%Y-%m-%d %H:%M")
            end = e.end_dt.strftime("%H:%M") if e.end_dt else ""
        except Exception:
            continue
        event_lines.append(f"- {start}-{end}: {e.title}" + (f" @ {e.location}" if e.location else ""))

    today = datetime.now().strftime("%Y-%m-%d %a")
    mode = "conflicts" if conflicts else "summary"
    prompt = (
        f"Today is {today}. Answer this calendar question concisely in 1-3 short sentences "
        f"or a short bulleted list. Mode: {mode}.\n\n"
        f"Question: {question}\n\n"
        f"Upcoming events:\n" + "\n".join(event_lines)
    )

    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "keep_alive": "10s",
                "think": False,
                "options": {"temperature": 0.2},
            },
            timeout=120,
        )
        resp.raise_for_status()
        answer = resp.json().get("response", "").strip()
    except Exception as exc:
        print(f":x: ollama query failed: {exc}")
        return 2

    print(answer or "_(no answer)_")
    return 0


# ── config (mute/watch SLACK_MONITOR_CHANNELS) ───────────────────────────────

def _cmd_config(mute: str, watch: str, list_channels: bool) -> int:
    """Atomically toggle a channel in event-aggregator/.env.

    Reuses python-dotenv's set_key() which writes via tempfile + os.replace().
    After mutation, kickstart event-aggregator so the change applies on the
    next launchd tick (or the immediate kickstart, whichever comes first).
    """
    import os
    import subprocess

    import config as _cfg
    from dotenv import dotenv_values, set_key

    env_path = Path(_cfg.__file__).parent / ".env"
    if not env_path.exists():
        # Create empty .env if missing — set_key would create it but more
        # explicitly here so the user sees a useful message.
        env_path.touch()

    if list_channels:
        current = dotenv_values(env_path).get("SLACK_MONITOR_CHANNELS") or ""
        chans = [c.strip() for c in current.split(",") if c.strip()]
        if not chans:
            print("_No channels currently watched._")
        else:
            print("*Currently watching:* " + ", ".join(f"`{c}`" for c in chans))
        return 0

    if not mute and not watch:
        print(":warning: Specify --mute, --watch, or --list-channels", file=sys.stderr)
        return 1

    # Read current value (.env wins over env vars; if neither, start empty)
    raw = dotenv_values(env_path).get("SLACK_MONITOR_CHANNELS", "")
    if raw is None:
        raw = ""
    chans = [c.strip() for c in raw.split(",") if c.strip()]

    target = (mute or watch).lstrip("#").strip()
    target_lc = target.lower()

    if mute:
        new_chans = [c for c in chans if c.lower() != target_lc]
        if len(new_chans) == len(chans):
            print(f":information_source: `{target}` was not in the watch list.")
            return 0
        action = "muted"
    else:
        if any(c.lower() == target_lc for c in chans):
            print(f":information_source: `{target}` already watched.")
            return 0
        new_chans = chans + [target]
        action = "watching"

    new_csv = ",".join(new_chans)
    set_key(str(env_path), "SLACK_MONITOR_CHANNELS", new_csv, quote_mode="never")

    # Kickstart event-aggregator so the next run picks up the new value.
    # Best-effort: a kickstart failure shouldn't fail the command.
    kickstart_ok = False
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.home-tools.event-aggregator"],
            capture_output=True, text=True, timeout=5,
        )
        kickstart_ok = result.returncode == 0
    except Exception:
        pass

    note = "" if kickstart_ok else " _(kickstart failed; change applies on next 10-min tick)_"
    print(f":white_check_mark: {action} `{target}` — `SLACK_MONITOR_CHANNELS` is now `{new_csv or '(empty)'}`{note}")
    return 0


# ── undo-last ────────────────────────────────────────────────────────────────

def _cmd_undo_last() -> int:
    """Delete the most-recently-written GCal event."""
    import config
    import state as state_module
    from writers import google_calendar as gcal_writer

    state = state_module.load()
    last = state.last_written_event()
    if last is None:
        print("_No events have been written yet._")
        return 0
    gcal_id, info = last

    title = info.get("title", "(no title)")
    start = info.get("start", "")
    created_at = info.get("created_at", "")
    target_cal = info.get("calendar_id") or config.GCAL_WEEKEND_CALENDAR_ID

    deleted = gcal_writer.delete_event(target_cal, gcal_id, dry_run=False)
    if not deleted:
        print(f":x: Could not delete `{gcal_id}` — `{title}` (may already be gone in GCal). State NOT modified.")
        return 1

    state.remove_written_event(gcal_id)
    state_module.save(state)

    when = ""
    try:
        # Render start as "Apr 25 2pm" if it parses
        dt = datetime.fromisoformat(start)
        when = f" | {dt.strftime('%b %-d %-I:%M%p').lower()}"
    except Exception:
        pass

    age_note = ""
    try:
        ca = datetime.fromisoformat(created_at)
        secs = (datetime.now(timezone.utc) - ca).total_seconds()
        if secs < 60:
            age_note = "\n_GCal may show propagation lag for a few seconds._"
    except Exception:
        pass

    print(f":wastebasket: undid: *{title}*{when}{age_note}")
    return 0


# ── changes (event_log.jsonl reader) ─────────────────────────────────────────

def _cmd_changes(since: str) -> int:
    """Read event_log.jsonl, filter by ts >= cutoff, group by action, format."""
    cutoff = _parse_since(since)
    if cutoff is None:
        print(f":x: could not parse `--since {since}`. Use ISO date, ISO datetime, or relative like `1d`/`12h`/`30m`.", file=sys.stderr)
        return 1

    import config as _cfg
    log_path = Path(_cfg.__file__).parent / "event_log.jsonl"
    if not log_path.exists():
        print("_event_log.jsonl is empty — no changes recorded yet._")
        return 0

    created: list[dict] = []
    updated: list[dict] = []
    cancelled: list[dict] = []

    try:
        with log_path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = entry.get("ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
                action = entry.get("action", "")
                if action == "created":
                    created.append(entry)
                elif action == "updated":
                    updated.append(entry)
                elif action == "cancelled":
                    cancelled.append(entry)
    except OSError as exc:
        print(f":x: could not read event_log.jsonl: {exc}", file=sys.stderr)
        return 1

    if not (created or updated or cancelled):
        print(f"_No changes since {cutoff.isoformat(timespec='minutes')}._")
        return 0

    lines: list[str] = [f"*Calendar changes since {cutoff.isoformat(timespec='minutes')}:*"]
    if created:
        lines.append(f":white_check_mark: created ({len(created)}):")
        for e in created[:20]:
            lines.append("  • " + _format_change_line(e))
        if len(created) > 20:
            lines.append(f"  …and {len(created) - 20} more")
    if updated:
        lines.append(f":pencil2: updated ({len(updated)}):")
        for e in updated[:20]:
            lines.append("  • " + _format_change_line(e))
        if len(updated) > 20:
            lines.append(f"  …and {len(updated) - 20} more")
    if cancelled:
        lines.append(f":wastebasket: cancelled ({len(cancelled)}):")
        for e in cancelled[:20]:
            lines.append("  • " + _format_change_line(e))
        if len(cancelled) > 20:
            lines.append(f"  …and {len(cancelled) - 20} more")

    print("\n".join(lines))
    return 0


def _cmd_bump_dashboard() -> int:
    """Increment the burial counter for today (called by dispatcher per
    non-bot top-level message in the interactive channel)."""
    import state as state_module
    state = state_module.load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_count = state.bump_dashboard_buried(today)
    state_module.save(state)
    print(f":bookmark_tabs: dashboard burial count → {new_count}")
    return 0


def _cmd_swap(decision_id: str, decision: str) -> int:
    """Resolve a pending OCR swap-decision."""
    import state as state_module
    state = state_module.load()
    if state.resolve_swap_decision(decision_id, decision):
        state_module.save(state)
        print(f":white_check_mark: swap decision `{decision_id[:8]}…` → {decision}")
        return 0
    print(f":x: swap decision `{decision_id[:8]}…` not found")
    return 1


def _cmd_forget(fp: str, title: str) -> int:
    """List or wipe a rejected fingerprint so the matching event can be re-proposed."""
    import state as state_module

    state = state_module.load()
    bucket = state._data.get("rejected_fingerprints", {})

    if not fp and not title:
        if not bucket:
            print(":white_check_mark: no rejected fingerprints recorded.")
            return 0
        lines = [":no_entry_sign: rejected fingerprints (newest first):"]
        for f, info in sorted(
            bucket.items(),
            key=lambda kv: kv[1].get("rejected_at", ""),
            reverse=True,
        ):
            t = info.get("title", "(untitled)")
            src = info.get("source", "")
            lines.append(f"  • `{f[:12]}…` — {t} _({src})_")
        print("\n".join(lines))
        return 0

    if fp:
        if state.forget_rejected_fingerprint(fp):
            state_module.save(state)
            print(f":white_check_mark: forgot `{fp[:12]}…` — can be re-proposed if re-detected.")
            return 0
        print(f":x: fingerprint `{fp[:12]}…` not found in rejected list.")
        return 1

    matches = [
        (f, info) for f, info in bucket.items()
        if title.lower() in info.get("title", "").lower()
    ]
    if not matches:
        print(f":x: no rejected fingerprints match title containing {title!r}.")
        return 1
    if len(matches) > 1:
        print(f":warning: {len(matches)} matches — pass --fp to disambiguate:")
        for f, info in matches:
            print(f"  • `{f[:12]}…` — {info.get('title', '(untitled)')}")
        return 1
    f, info = matches[0]
    state.forget_rejected_fingerprint(f)
    state_module.save(state)
    print(f":white_check_mark: forgot `{f[:12]}…` — {info.get('title', '(untitled)')}")
    return 0


def _format_change_line(entry: dict) -> str:
    title = entry.get("title", "(untitled)")
    start = entry.get("start", "")
    when = ""
    try:
        dt = datetime.fromisoformat(start)
        when = dt.strftime("%b %-d %-I:%M%p").lower() + " — "
    except Exception:
        pass
    src = entry.get("source")
    src_note = f" _({src})_" if src else ""
    return f"{when}*{title}*{src_note}"


def _parse_since(raw: str) -> datetime | None:
    """Parse `1d` / `7d` / `12h` / `30m` / ISO date / ISO datetime → UTC datetime."""
    import re
    raw = (raw or "").strip()
    if not raw:
        return None

    m = re.fullmatch(r"(\d+)\s*([dhm])", raw, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        seconds = n * {"d": 86400, "h": 3600, "m": 60}[unit]
        from datetime import timedelta
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)

    # ISO date (no time) → midnight UTC of that date
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ── helpers ──────────────────────────────────────────────────────────────────

def _pending_items(state) -> list[dict]:
    items = []
    for batch in state.get_pending_proposals():
        for item in batch.get("items", []):
            if item.get("status") == "pending":
                items.append(item)
    return items


def _parse_nums(raw: str) -> list[int]:
    import re
    if not raw or raw.strip().lower() in ("all", "everything"):
        return []
    return [int(m) for m in re.findall(r"\d+", raw)]


def _check_ollama() -> bool:
    import config
    import requests
    try:
        r = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _mimetype_for(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


def _slug(text: str) -> str:
    import re
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return (text.strip("-") or "untitled")[:80]


def _json_err(msg: str) -> None:
    """Emit a null-shaped classification with error on stdout so the dispatcher
    can parse a consistent shape even on failure."""
    print(json.dumps({
        "category": "Documents",
        "subcategory": None,
        "doc_type": "",
        "confidence": 0.0,
        "title": "",
        "date": None,
        "suggested_filename": "",
        "error": msg,
    }))
