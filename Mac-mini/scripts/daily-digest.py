#!/usr/bin/env python3
"""Daily Slack digest of Mac mini service health.

Runs at 07:00 daily via com.home-tools.daily-digest.plist. Reads
incidents.jsonl (state-change events from heartbeat.py over the last 24h),
queries live primitives for the "now" snapshot, formats a Slack message,
and posts to #ian-event-aggregator via slack-post.sh.

  --dry-run    Print the message to stdout instead of posting.

Phase 6 — see Mac-mini/PHASE6.md.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path(os.environ["HOME"])
RUN_DIR = HOME / "Home-Tools" / "run"
LOGS_DIR = HOME / "Home-Tools" / "logs"
INCIDENTS_FILE = LOGS_DIR / "incidents.jsonl"
STATE_FILE = RUN_DIR / "heartbeat-state.json"
SCRIPT_DIR = Path(__file__).resolve().parent
SLACK_POST = SCRIPT_DIR / "slack-post.sh"
DEFAULT_CHANNEL = "#ian-event-aggregator"
LOOKBACK_HOURS = 24

DBS_TO_REPORT = [
    ("health.db", HOME / "Home-Tools" / "health-dashboard" / "data" / "health.db"),
    ("finance.db", HOME / "Home-Tools" / "finance-monitor" / "data" / "finance.db"),
    (
        "event-aggregator state",
        HOME / "Home-Tools" / "event-aggregator" / "state.json",
    ),
]


def parse_iso(s: str) -> datetime:
    # Heartbeat writes ISO-8601 with timezone offset; fromisoformat handles it on 3.11+
    return datetime.fromisoformat(s)


def read_recent_incidents(hours: int) -> list[dict]:
    if not INCIDENTS_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: list[dict] = []
    with INCIDENTS_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = parse_iso(obj["ts"])
                if ts.astimezone(timezone.utc) >= cutoff:
                    out.append(obj)
            except (json.JSONDecodeError, KeyError, ValueError):
                # Bad lines are not fatal; skip them.
                continue
    return out


def read_current_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def humanize_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def db_freshness_lines() -> list[str]:
    out: list[str] = []
    now = time.time()
    for label, path in DBS_TO_REPORT:
        if path.exists():
            age = humanize_age(now - path.stat().st_mtime)
            out.append(f"{label} {age}")
        else:
            out.append(f"{label} MISSING")
    return out


def short_key(key: str) -> str:
    """Drop the namespace prefix for human-readable output."""
    return key.split(":", 1)[-1] if ":" in key else key


def build_message(
    incidents: list[dict],
    current: dict[str, str],
    now: datetime,
) -> tuple[str, bool]:
    """Return (slack-text, attention_flag)."""
    bad_now = {k: v for k, v in current.items() if v not in ("up", "fresh", "ok")}
    state_changes = [i for i in incidents if i.get("kind") == "state_change"]
    first_seen_bad = [i for i in incidents if i.get("kind") == "first_seen_bad"]

    attention = bool(bad_now or first_seen_bad)
    date_str = now.strftime("%Y-%m-%d")
    title = f"*Mac mini daily digest — {date_str}*"
    if attention:
        title += " [ATTENTION]"

    lines: list[str] = [title]

    if not incidents and not bad_now:
        n_keys = len(current) if current else "?"
        lines.append(f":white_check_mark: All {n_keys} services healthy.")
        lines.append(f"0 incidents in last {LOOKBACK_HOURS}h.")
    else:
        if state_changes:
            lines.append(
                f":bar_chart: {len(state_changes)} state-change event(s) in last {LOOKBACK_HOURS}h:"
            )
            # Show the most recent N events to keep the digest scannable.
            for ev in state_changes[-10:]:
                ts = ev.get("ts", "?")
                key = short_key(ev.get("key", "?"))
                pri = ev.get("prior", "?")
                cur = ev.get("current", "?")
                lines.append(f"  • {ts} {key}: {pri} → {cur}")
        if first_seen_bad:
            lines.append(
                f":warning: {len(first_seen_bad)} first-seen-bad observation(s):"
            )
            for ev in first_seen_bad[-10:]:
                key = short_key(ev.get("key", "?"))
                cur = ev.get("current", "?")
                lines.append(f"  • {key} = {cur}")
        if bad_now:
            lines.append(":x: Currently unhealthy:")
            for k, v in sorted(bad_now.items()):
                lines.append(f"  • {short_key(k)} = {v}")
        else:
            lines.append(":white_check_mark: Currently green.")

    db_lines = db_freshness_lines()
    if db_lines:
        lines.append("DBs: " + ", ".join(db_lines))

    return "\n".join(lines), attention


def post_to_slack(channel: str, body: str) -> int:
    if not SLACK_POST.exists():
        print(f"slack-post.sh not found at {SLACK_POST}", file=sys.stderr)
        return 1
    proc = subprocess.run(
        ["bash", str(SLACK_POST), channel, body],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(
            f"slack-post failed rc={proc.returncode} stderr={proc.stderr.strip()}",
            file=sys.stderr,
        )
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print message to stdout instead of posting",
    )
    parser.add_argument(
        "--channel",
        default=DEFAULT_CHANNEL,
        help=f"Slack channel to post to (default: {DEFAULT_CHANNEL})",
    )
    args = parser.parse_args()

    incidents = read_recent_incidents(LOOKBACK_HOURS)
    current = read_current_state()
    now = datetime.now(timezone.utc).astimezone()
    message, attention = build_message(incidents, current, now)

    if args.dry_run:
        print(message)
        print(f"\n[dry-run] attention={attention} channel={args.channel}", file=sys.stderr)
        return 0

    rc = post_to_slack(args.channel, message)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
