#!/usr/bin/env python3
"""
One-shot script: delete duplicate messages from the ian-event-aggregator channel.

Duplicates defined as:
  1. Top-level day-thread openers with the same date string (keep oldest, delete rest)
  2. Within each thread: messages with identical text (keep oldest, delete rest)

Run with --dry-run first to preview what would be deleted.

Usage:
  python cleanup_slack_dupes.py --dry-run
  python cleanup_slack_dupes.py
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

import config
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def get_channel_id(client: WebClient, name: str) -> str:
    """Resolve a channel name to its ID, checking public and private channels."""
    name = name.lstrip("#")
    for channel_type in ("public_channel", "private_channel"):
        cursor = None
        while True:
            resp = client.conversations_list(
                types=channel_type,
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )
            for ch in resp["channels"]:
                if ch["name"] == name:
                    return ch["id"]
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    raise SystemExit(f"Channel '{name}' not found — is the bot in that channel?")


def fetch_all_messages(client: WebClient, channel_id: str) -> list[dict]:
    """Fetch all top-level messages (no thread replies) from the channel."""
    messages = []
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=channel_id,
            limit=200,
            cursor=cursor,
        )
        messages.extend(resp["messages"])
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return messages


def fetch_thread_replies(client: WebClient, channel_id: str, thread_ts: str) -> list[dict]:
    """Fetch all replies in a thread (excludes the parent message itself)."""
    replies = []
    cursor = None
    while True:
        resp = client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            limit=200,
            cursor=cursor,
        )
        msgs = resp["messages"]
        # First item is the parent; skip it
        replies.extend(msgs[1:] if cursor is None else msgs)
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return replies


def delete_message(client: WebClient, channel_id: str, ts: str, dry_run: bool, label: str) -> None:
    if dry_run:
        print(f"  [DRY RUN] would delete: {label}")
        return
    try:
        client.chat_delete(channel=channel_id, ts=ts)
        print(f"  deleted: {label}")
    except SlackApiError as e:
        print(f"  ERROR deleting {ts}: {e.response['error']}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up duplicate Slack messages")
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    args = parser.parse_args()

    if not config.SLACK_BOT_TOKEN:
        raise SystemExit("SLACK_BOT_TOKEN is not set")

    client = WebClient(token=config.SLACK_BOT_TOKEN)
    channel_id = get_channel_id(client, config.SLACK_NOTIFY_CHANNEL)
    print(f"Channel: #{config.SLACK_NOTIFY_CHANNEL} ({channel_id})")

    messages = fetch_all_messages(client, channel_id)
    print(f"Fetched {len(messages)} top-level message(s)")

    total_deleted = 0

    # ── 1. Deduplicate day-thread openers ────────────────────────────────────
    # Format: "Event Aggregator — <Month D, YYYY>"
    # Group by the date portion; keep the oldest (smallest ts), delete the rest.
    day_thread_by_date: dict[str, list[dict]] = defaultdict(list)
    other_messages: list[dict] = []

    for msg in messages:
        text = msg.get("text", "")
        if text.startswith("Event Aggregator — "):
            date_part = text[len("Event Aggregator — "):]
            day_thread_by_date[date_part].append(msg)
        else:
            other_messages.append(msg)

    print(f"\n── Day-thread openers ──")
    for date_str, group in sorted(day_thread_by_date.items()):
        # Sort oldest-first (smallest ts = oldest)
        group.sort(key=lambda m: float(m["ts"]))
        print(f"  '{date_str}': {len(group)} thread(s)")
        for msg in group[1:]:  # keep first, delete rest
            snippet = msg.get("text", "")[:60]
            delete_message(client, channel_id, msg["ts"], args.dry_run, f"[day thread dupe] {snippet}")
            total_deleted += 1

    # ── 2. Deduplicate replies within each thread ────────────────────────────
    # Collect all thread_ts values: surviving day-thread openers + any threaded msgs
    surviving_threads: set[str] = set()
    for group in day_thread_by_date.values():
        group_sorted = sorted(group, key=lambda m: float(m["ts"]))
        surviving_threads.add(group_sorted[0]["ts"])
    # Also include any other top-level messages that have replies
    for msg in other_messages:
        if msg.get("reply_count", 0) > 0:
            surviving_threads.add(msg["ts"])

    print(f"\n── Thread reply deduplication ({len(surviving_threads)} thread(s)) ──")
    for thread_ts in sorted(surviving_threads):
        replies = fetch_thread_replies(client, channel_id, thread_ts)
        if not replies:
            continue

        seen_texts: dict[str, str] = {}  # text → first ts seen
        dupes_in_thread = 0
        for reply in sorted(replies, key=lambda m: float(m["ts"])):
            text = reply.get("text", "").strip()
            ts = reply["ts"]
            if text in seen_texts:
                snippet = text[:80].replace("\n", " ")
                delete_message(
                    client, channel_id, ts, args.dry_run,
                    f"[thread {thread_ts}] dupe reply: {snippet!r}"
                )
                total_deleted += 1
                dupes_in_thread += 1
            else:
                seen_texts[text] = ts
        if dupes_in_thread:
            print(f"  thread {thread_ts}: {dupes_in_thread} dupe(s) removed")

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Total {'to delete' if args.dry_run else 'deleted'}: {total_deleted}")


if __name__ == "__main__":
    main()
