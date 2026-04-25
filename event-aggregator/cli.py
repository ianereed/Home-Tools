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

    p = sub.add_parser("ingest-image", help="Full event-extraction pipeline on a local file")
    p.add_argument("--file", required=True)

    p = sub.add_parser("approve", help="Approve pending proposals")
    p.add_argument("--nums", default="", help="Comma-separated numbers; omit for all")

    p = sub.add_parser("reject", help="Reject pending proposals")
    p.add_argument("--nums", default="")

    p = sub.add_parser("add-event", help="Manual event via extractor")
    p.add_argument("--text", required=True)

    p = sub.add_parser("status", help="Inspect state.json")
    p.add_argument("--json", action="store_true")
    p.add_argument("--pending", action="store_true")
    p.add_argument("--last-run", action="store_true")

    p = sub.add_parser("query", help="Ask qwen3 about the calendar")
    p.add_argument("--calendar", default="")
    p.add_argument("--conflicts", default="")

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
    if args.cmd == "approve":
        return _cmd_approve_or_reject(args.nums, approve=True)
    if args.cmd == "reject":
        return _cmd_approve_or_reject(args.nums, approve=False)
    if args.cmd == "add-event":
        return _cmd_add_event(args.text)
    if args.cmd == "status":
        return _cmd_status(as_json=args.json, only_pending=args.pending, only_last_run=args.last_run)
    if args.cmd == "query":
        return _cmd_query(calendar=args.calendar, conflicts=args.conflicts)
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
        logger.exception("ingest-image: failed")
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 2

    state_module.save(state)
    print(summary)
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


def _do_approve(state, nums: list[int]) -> int:
    from datetime import timezone as tz
    from googleapiclient.discovery import build
    import config
    from connectors import google_auth
    from dedup import fingerprint as _fingerprint
    from logs.event_log import record as log_event, record_cancellation
    from notifiers import slack_notifier
    import state as state_module
    from writers import google_calendar as gcal_writer

    # Reuse main.py's _proposal_item_to_candidate
    from main import _proposal_item_to_candidate

    approved = 0
    errors: list[str] = []
    now = datetime.now(timezone.utc)
    snapshot = state.calendar_snapshot()

    thread_ts, _ = state.get_day_thread()

    for num in nums:
        item = state.approve_proposal(num)
        if item is None:
            errors.append(f"#{num}: not pending")
            continue
        candidate = _proposal_item_to_candidate(item)
        if candidate.start_dt < now and not candidate.is_cancellation:
            errors.append(f"#{num}: event time has passed")
            continue

        action = None
        if candidate.is_cancellation and candidate.gcal_event_id_to_update:
            if gcal_writer.delete_event(candidate.gcal_event_id_to_update, dry_run=False):
                record_cancellation(
                    gcal_id=candidate.gcal_event_id_to_update,
                    title=candidate.original_title_hint or candidate.title,
                    source=candidate.source,
                )
                action = "cancelled"
        elif candidate.gcal_event_id_to_update:
            written, _conflicts = gcal_writer.update_event(candidate.gcal_event_id_to_update, candidate, dry_run=False)
            if written:
                state.add_written_event(
                    gcal_id=written.gcal_event_id,
                    title=candidate.title,
                    start_iso=candidate.start_dt.isoformat(),
                    fingerprint=written.fingerprint,
                    is_tentative=(candidate.confidence_band == "medium"),
                )
                log_event(written, action="updated")
                action = "updated"
        else:
            written, _conflicts = gcal_writer.write_event(candidate, dry_run=False, snapshot=snapshot)
            if written:
                state.add_fingerprint(written.fingerprint)
                state.add_written_event(
                    gcal_id=written.gcal_event_id,
                    title=candidate.title,
                    start_iso=candidate.start_dt.isoformat(),
                    fingerprint=written.fingerprint,
                    is_tentative=(candidate.confidence_band == "medium"),
                )
                log_event(written, action="created")
                action = "created"

        if action:
            approved += 1
            if thread_ts:
                start_str = ""
                if not candidate.is_cancellation:
                    try:
                        start_str = f" | {candidate.start_dt.strftime('%b %-d %-I:%M%p').lower()}"
                    except Exception:
                        pass
                icon = {"created": ":white_check_mark:", "updated": ":pencil2:", "cancelled": ":wastebasket:"}.get(action, ":white_check_mark:")
                slack_notifier.post_to_thread(thread_ts, f"{icon} #{num} {action}: *{candidate.title}*{start_str}")

    state_module.save(state)

    msg = f":white_check_mark: {approved} approved"
    if errors:
        msg += f"\n" + "\n".join(errors)
    print(msg)
    return 0


def _do_reject(state, nums: list[int]) -> int:
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
            state.remove_proposal_fingerprint(fp)
        rejected += 1

    state_module.save(state)
    msg = f":x: {rejected} rejected"
    if errors:
        msg += "\n" + "\n".join(errors)
    print(msg)
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

    # Post to Slack day thread
    thread_ts = slack_notifier.get_or_create_day_thread(state)
    if thread_ts:
        posted_ts = slack_notifier.post_proposals(thread_ts, batch_items)
        if posted_ts:
            state.set_proposal_slack_ts(batch_id, posted_ts)

    state_module.save(state)
    lines = [f":memo: proposed {len(batch_items)} event(s):"]
    for item in batch_items:
        start = item.get("start_dt", "")[:16].replace("T", " ")
        lines.append(f"  • #{item['num']} *{item['title']}* — {start}")
    lines.append(f"_Reply `approve {batch_items[0]['num']}` (or `approve all`)._")
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
