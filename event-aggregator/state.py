"""
Persistent state management.

state.json (gitignored) tracks:
- last_run timestamps per source
- seen message IDs for API-based sources (pruned to 30-day rolling window)
- written event fingerprints (pruned once event date has passed)
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).parent / "state.json"
_LOCK_PATH = STATE_PATH.parent / ".state.lock"


_lock_depth = 0  # >0 means we hold the flock; save() asserts this.


def _holding_lock() -> bool:
    return _lock_depth > 0


@contextlib.contextmanager
def locked():
    """Acquire an exclusive write lock on state.json for the block's duration.

    Use this to wrap any load() + mutate + save() sequence that must not
    interleave with another process (e.g. the worker popping a job while
    the dispatcher CLI is approving a proposal).

    Reentrant within a single process: nesting `with locked():` blocks is a
    no-op for the inner block (it sees the flock already held). save() checks
    the depth counter to enforce that callers wrapped their mutations.
    """
    global _lock_depth
    if _lock_depth > 0:
        # Already holding the flock in this process. Reentrant no-op.
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
        return
    with _LOCK_PATH.open("a") as _lf:
        fcntl.flock(_lf.fileno(), fcntl.LOCK_EX)
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
            # flock released when _lf closes


@contextlib.contextmanager
def _allow_unlocked_save():
    """Test-only escape hatch: temporarily allow save() without locked().

    Used by tests that exercise save()/load() without spinning up the full
    flock machinery. Production code must never call this.
    """
    global _lock_depth
    _lock_depth += 1
    try:
        yield
    finally:
        _lock_depth -= 1

_DEFAULT_LOOKBACK_DAYS = 7  # first-run default when no last_run is recorded

ALL_SOURCES = [
    "gmail", "gcal", "slack", "imessage", "whatsapp",
    "discord", "messenger", "instagram",
]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


class State:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    # ── last_run ─────────────────────────────────────────────────────────────

    def last_run(self, source: str) -> datetime:
        """Return last run time for source, defaulting to 7 days ago on first run."""
        raw = self._data.get("last_run", {}).get(source)
        if raw:
            return _parse_dt(raw)
        return _utcnow() - timedelta(days=_DEFAULT_LOOKBACK_DAYS)

    def set_last_run(self, source: str, dt: datetime | None = None) -> None:
        self._data.setdefault("last_run", {})[source] = (dt or _utcnow()).isoformat()

    # ── seen IDs ─────────────────────────────────────────────────────────────

    def is_seen(self, source: str, msg_id: str) -> bool:
        for entry in self._data.get("seen_message_ids", {}).get(source, []):
            eid = entry["id"] if isinstance(entry, dict) else entry
            if eid == msg_id:
                return True
        return False

    def mark_seen(self, source: str, msg_id: str) -> None:
        bucket = self._data.setdefault("seen_message_ids", {}).setdefault(source, [])
        if not self.is_seen(source, msg_id):
            bucket.append({"id": msg_id, "ts": _utcnow().isoformat()})

    # ── fingerprints ─────────────────────────────────────────────────────────

    def has_fingerprint(self, fp: str) -> bool:
        """True if the fp matches a previously-written event OR a previously-
        rejected proposal. Rejected fingerprints stay alive so the same event
        re-detected from a different source doesn't get re-proposed."""
        if fp in self._data.get("written_fingerprints", []):
            return True
        if fp in self._data.get("rejected_fingerprints", {}):
            return True
        return False

    def add_fingerprint(self, fp: str) -> None:
        fps = self._data.setdefault("written_fingerprints", [])
        if fp not in fps:
            fps.append(fp)

    # ── Job queues (text extraction + OCR) ──────────────────────────────────

    def enqueue_text_job(
        self, source: str, msg_id: str, body_text: str,
        metadata: dict, timestamp_iso: str,
    ) -> None:
        """
        Enqueue a RawMessage for text extraction. Persists body_text + metadata
        so the worker can run independently of the fetch loop. Privacy: state.json
        is gitignored and chmod 600 — body_text never leaves disk.
        """
        # Dedup against in-flight queue: don't add if same source+id already queued.
        queue = self._data.setdefault("text_queue", [])
        for existing in queue:
            if existing.get("source") == source and existing.get("id") == msg_id:
                return
        queue.append({
            "source": source,
            "id": msg_id,
            "body_text": body_text,
            "metadata": metadata or {},
            "timestamp": timestamp_iso,
            "queued_at": _utcnow().isoformat(),
        })

    def pop_text_job(self) -> dict | None:
        queue = self._data.get("text_queue", [])
        return queue.pop(0) if queue else None

    def text_queue_depth(self) -> int:
        return len(self._data.get("text_queue", []))

    def enqueue_ocr_job(self, file_path: str, metadata: dict | None = None) -> None:
        queue = self._data.setdefault("ocr_queue", [])
        for existing in queue:
            if existing.get("file_path") == file_path:
                return
        queue.append({
            "file_path": file_path,
            "metadata": metadata or {},
            "queued_at": _utcnow().isoformat(),
        })

    def pop_ocr_job(self) -> dict | None:
        queue = self._data.get("ocr_queue", [])
        return queue.pop(0) if queue else None

    def ocr_queue_depth(self) -> int:
        return len(self._data.get("ocr_queue", []))

    def peek_ocr_job(self) -> dict | None:
        queue = self._data.get("ocr_queue", [])
        return queue[0] if queue else None

    def worker_status(self) -> dict:
        return self._data.get("worker_status", {})

    def update_worker_status(self, **kwargs) -> None:
        bucket = self._data.setdefault("worker_status", {})
        bucket.update(kwargs)
        bucket["updated_at"] = _utcnow().isoformat()

    # ── Swap decisions (Slack [Wait]/[Interrupt] interactive proposals) ──────

    def add_swap_decision(self, ocr_path: str, text_queue_depth: int) -> str:
        """Record a pending OCR-swap decision. Returns a unique decision_id."""
        import secrets
        bucket = self._data.setdefault("swap_decisions", {})
        decision_id = secrets.token_hex(8)
        bucket[decision_id] = {
            "ocr_path": ocr_path,
            "text_queue_depth_at_request": text_queue_depth,
            "decision": "pending",  # "pending" | "wait" | "interrupt"
            "created_at": _utcnow().isoformat(),
        }
        return decision_id

    def resolve_swap_decision(self, decision_id: str, decision: str) -> bool:
        """Set decision to 'wait' or 'interrupt'. Returns True if found."""
        bucket = self._data.get("swap_decisions", {})
        if decision_id not in bucket:
            return False
        bucket[decision_id]["decision"] = decision
        bucket[decision_id]["resolved_at"] = _utcnow().isoformat()
        return True

    def get_swap_decision(self, decision_id: str) -> dict | None:
        return self._data.get("swap_decisions", {}).get(decision_id)

    # ── Dashboard burial tracking (Tier 3.2: repost when buried) ────────────

    def bump_dashboard_buried(self, date_str: str) -> int:
        """Increment burial counter for the given date. Returns new count."""
        bucket = self._data.setdefault("dashboard_buried", {})
        bucket[date_str] = int(bucket.get(date_str, 0)) + 1
        return bucket[date_str]

    def dashboard_buried_count(self, date_str: str) -> int:
        return int(self._data.get("dashboard_buried", {}).get(date_str, 0))

    def reset_dashboard_buried(self, date_str: str) -> None:
        bucket = self._data.get("dashboard_buried", {})
        if date_str in bucket:
            bucket[date_str] = 0

    # ── Recurring-event notices (surfaced to the dashboard for 24h) ─────────

    def add_recurring_notice(
        self, title: str, source: str, recurrence_hint: str | None = None
    ) -> bool:
        """Add a "saw something recurring" notice. Returns True if newly added,
        False if a matching notice was added in the last 24h (suppressed)."""
        import hashlib
        key = hashlib.sha256(
            f"{title.lower().strip()}|{source}".encode()
        ).hexdigest()[:16]
        now = _utcnow()
        cutoff = (now - timedelta(hours=24)).isoformat()
        bucket = self._data.setdefault("recurring_notices", [])
        for entry in bucket:
            if entry.get("key") == key and entry.get("seen_at", "") >= cutoff:
                return False
        bucket.append({
            "key": key,
            "title": title,
            "source": source,
            "recurrence_hint": recurrence_hint or "",
            "seen_at": now.isoformat(),
        })
        return True

    def recurring_notices(self) -> list[dict]:
        """Return notices from the last 24h."""
        cutoff = (_utcnow() - timedelta(hours=24)).isoformat()
        return [
            n for n in self._data.get("recurring_notices", [])
            if n.get("seen_at", "") >= cutoff
        ]

    # ── Ollama health (surfaced to the Slack dashboard) ─────────────────────

    def mark_ollama_down(self, skipped: int = 0) -> None:
        """Record that an Ollama call is failing. Adds to running skipped count."""
        bucket = self._data.setdefault("ollama_health", {})
        if not bucket.get("down_since"):
            bucket["down_since"] = _utcnow().isoformat()
        bucket["skipped_count"] = int(bucket.get("skipped_count", 0)) + skipped

    def mark_ollama_up(self) -> bool:
        """Clear the down state. Returns True if a down state was actually cleared."""
        bucket = self._data.get("ollama_health", {})
        if bucket.get("down_since"):
            self._data["ollama_health"] = {}
            return True
        return False

    def ollama_health(self) -> dict:
        return self._data.get("ollama_health", {})

    # ── rejected fingerprints (stay alive to suppress cross-source repeats) ──

    def add_rejected_fingerprint(
        self, fp: str, title: str, source: str
    ) -> None:
        bucket = self._data.setdefault("rejected_fingerprints", {})
        bucket[fp] = {
            "rejected_at": _utcnow().isoformat(),
            "title": title,
            "source": source,
        }

    def forget_rejected_fingerprint(self, fp: str) -> bool:
        """Remove a fingerprint from the rejected list. Returns True if removed."""
        bucket = self._data.get("rejected_fingerprints", {})
        if fp in bucket:
            del bucket[fp]
            return True
        return False

    def is_rejected(self, fp: str) -> bool:
        return fp in self._data.get("rejected_fingerprints", {})

    # ── digest schedule tracking ─────────────────────────────────────────────

    def last_digest_daily(self) -> datetime | None:
        return _parse_dt(self._data.get("last_digest_daily"))

    def set_last_digest_daily(self, dt: datetime | None = None) -> None:
        self._data["last_digest_daily"] = (dt or _utcnow()).isoformat()

    def last_digest_weekly(self) -> datetime | None:
        return _parse_dt(self._data.get("last_digest_weekly"))

    def set_last_digest_weekly(self, dt: datetime | None = None) -> None:
        self._data["last_digest_weekly"] = (dt or _utcnow()).isoformat()

    # ── written events (for update/cancel lookup) ─────────────────────────────

    def add_written_event(
        self,
        gcal_id: str,
        title: str,
        start_iso: str,
        fingerprint: str,
        is_tentative: bool = False,
        calendar_id: str = "",
    ) -> None:
        bucket = self._data.setdefault("written_events", {})
        bucket[gcal_id] = {
            "title": title,
            "start": start_iso,
            "fingerprint": fingerprint,
            "created_at": _utcnow().isoformat(),
            "is_tentative": is_tentative,
            "calendar_id": calendar_id,
        }

    def get_written_events(self) -> dict[str, dict]:
        return self._data.get("written_events", {})

    def last_written_event(self) -> tuple[str, dict] | None:
        """Return the (gcal_id, event_dict) with the most recent created_at, or None."""
        events = self._data.get("written_events", {})
        if not events:
            return None
        gcal_id = max(events, key=lambda k: events[k].get("created_at", ""))
        return gcal_id, events[gcal_id]

    def remove_written_event(self, gcal_id: str) -> dict | None:
        """Pop an entry from written_events. Returns the removed dict or None."""
        events = self._data.get("written_events", {})
        return events.pop(gcal_id, None)

    # ── day thread tracking (Slack channel threading) ─────────────────────────

    def get_day_thread(self) -> tuple[str | None, str | None]:
        """Returns (thread_ts, date_str) for today's Slack thread, or (None, None)."""
        return (
            self._data.get("day_thread_ts"),
            self._data.get("day_thread_date"),
        )

    def set_day_thread(self, ts: str, date_str: str) -> None:
        self._data["day_thread_ts"] = ts
        self._data["day_thread_date"] = date_str

    # ── calendar snapshot (for digest diffing) ────────────────────────────────

    def calendar_snapshot(self) -> dict[str, dict]:
        """Last-known year-ahead events keyed by gcal_id."""
        return self._data.get("calendar_snapshot", {})

    def update_calendar_snapshot(self, events: list) -> None:
        """Persist current year-ahead events for next-run diff. Accepts CalendarEvent list."""
        self._data["calendar_snapshot"] = {
            e.gcal_id: {
                "title": e.title,
                "start": e.start_dt.isoformat(),
                "end": e.end_dt.isoformat(),
                "location": e.location,
                "source_description": e.source_description,
                "is_all_day": getattr(e, "is_all_day", False),
                "calendar_id": getattr(e, "calendar_id", ""),
                "attendees": getattr(e, "attendees", []) or [],
            }
            for e in events
        }

    # ── todo fingerprints ─────────────────────────────────────────────────────

    def has_todo_fingerprint(self, fp: str) -> bool:
        return fp in self._data.get("written_todo_fingerprints", [])

    def add_todo_fingerprint(self, fp: str) -> None:
        fps = self._data.setdefault("written_todo_fingerprints", [])
        if fp not in fps:
            fps.append(fp)

    # ── todoist project ID cache ──────────────────────────────────────────────

    def get_todoist_project_id(self) -> str | None:
        return self._data.get("todoist_project_id")

    def set_todoist_project_id(self, project_id: str) -> None:
        self._data["todoist_project_id"] = project_id

    # ── warned conflicts ──────────────────────────────────────────────────────

    def is_conflict_warned(self, fp: str) -> bool:
        """Return True if this conflict fingerprint has already been reported."""
        return fp in self._data.get("warned_conflict_ids", {})

    def mark_conflicts_warned(self, fps: list[str]) -> None:
        """Record conflict fingerprints as reported (using today's date)."""
        today = _utcnow().strftime("%Y-%m-%d")
        bucket = self._data.setdefault("warned_conflict_ids", {})
        for fp in fps:
            bucket[fp] = today

    # ── proposal counter ─────────────────────────────────────────────────────────

    def next_proposal_num(self) -> int:
        """Return the next globally unique proposal number (never resets)."""
        n = self._data.get("proposal_counter", 0) + 1
        self._data["proposal_counter"] = n
        return n

    # ── pending proposals ─────────────────────────────────────────────────────

    def get_pending_proposals(self) -> list[dict]:
        """Return all proposal batches that have at least one pending item."""
        all_batches = self._data.get("pending_proposals", [])
        return [b for b in all_batches if any(i["status"] == "pending" for i in b.get("items", []))]

    def add_proposal_batch(self, batch: dict) -> None:
        """Append a new proposal batch to state."""
        self._data.setdefault("pending_proposals", []).append(batch)

    def set_proposal_slack_ts(self, batch_id: str, slack_ts: str) -> None:
        """Store the Slack message ts after a proposal batch is posted."""
        for batch in self._data.get("pending_proposals", []):
            if batch.get("batch_id") == batch_id:
                batch["slack_ts"] = slack_ts
                return

    def approve_proposal(self, num: int) -> dict | None:
        """Mark a proposal item as approved. Returns the item dict or None if not found.

        Sets claimed_at before flipping status so a concurrent second call within
        30 seconds sees the claim and returns None (double-click idempotency).
        """
        now = _utcnow()
        for batch in self._data.get("pending_proposals", []):
            for item in batch.get("items", []):
                if item["num"] == num and item["status"] == "pending":
                    claimed = _parse_dt(item.get("claimed_at"))
                    if claimed and (now - claimed).total_seconds() < 30:
                        return None  # concurrent second click — skip
                    item["claimed_at"] = now.isoformat()
                    item["status"] = "approved"
                    return item
        return None

    def reject_proposal(self, num: int) -> dict | None:
        """Mark a proposal item as rejected. Returns the item or None if not found."""
        for batch in self._data.get("pending_proposals", []):
            for item in batch.get("items", []):
                if item["num"] == num and item["status"] == "pending":
                    item["status"] = "rejected"
                    return item
        return None

    def expire_old_proposals(self, hours: int) -> list[dict]:
        """Mark proposals older than `hours` as expired. Returns list of expired items."""
        cutoff = _utcnow() - timedelta(hours=hours)
        expired = []
        for batch in self._data.get("pending_proposals", []):
            created = _parse_dt(batch.get("created_at"))
            if created and created < cutoff:
                for item in batch.get("items", []):
                    if item["status"] == "pending":
                        item["status"] = "expired"
                        expired.append(item)
        return expired

    def remove_proposal_fingerprint(self, fp: str) -> None:
        """Remove a fingerprint that was added for a proposal (on reject/expire)."""
        fps = self._data.get("written_fingerprints", [])
        if fp in fps:
            fps.remove(fp)

    # ── proposal dashboard (live Block Kit message per day) ──────────────────────

    def get_proposal_dashboard_ts(self, date_str: str) -> str | None:
        """Return the Slack ts of today's live dashboard message, or None."""
        entry = self._data.get("proposal_dashboard", {}).get(date_str)
        if entry is None:
            return None
        return entry["ts"] if isinstance(entry, dict) else entry

    def get_proposal_dashboard_channel(self, date_str: str) -> str | None:
        """Return the channel ID used when this dashboard message was posted, or None."""
        entry = self._data.get("proposal_dashboard", {}).get(date_str)
        if isinstance(entry, dict):
            return entry.get("channel")
        return None

    def set_proposal_dashboard_ts(self, date_str: str, ts: str, channel: str | None = None) -> None:
        """Persist the Slack ts (and optional channel ID) of the dashboard message for date_str."""
        entry: dict | str = {"ts": ts, "channel": channel} if channel else ts
        self._data.setdefault("proposal_dashboard", {})[date_str] = entry

    def get_all_proposal_items_for_dashboard(self, today_str: str | None = None) -> list[dict]:
        """Return items to display on today's dashboard: all pending + today's actioned items.

        "Today" is the user's wall-clock day, not UTC's. created_at is stored
        as a UTC ISO string; convert to USER_TIMEZONE before comparing dates
        so a proposal created at 23:30 PT (UTC tomorrow) still groups under
        today's dashboard.
        """
        import tz_utils
        from zoneinfo import ZoneInfo
        import config
        user_tz = ZoneInfo(config.USER_TIMEZONE)
        if today_str is None:
            today_str = tz_utils.today_user_str()
        result = []
        seen_keys: set[tuple] = set()
        for batch in self._data.get("pending_proposals", []):
            batch_id = batch.get("batch_id", "")
            created_at_raw = batch.get("created_at") or ""
            try:
                created_local_date = (
                    datetime.fromisoformat(created_at_raw).astimezone(user_tz).strftime("%Y-%m-%d")
                )
            except ValueError:
                created_local_date = created_at_raw[:10]
            for item in batch.get("items", []):
                num = item.get("num")
                key = (batch_id, num)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                if item["status"] == "pending":
                    result.append(item)
                elif created_local_date == today_str and item["status"] in ("approved", "rejected", "expired"):
                    result.append(item)
        return sorted(result, key=lambda x: x.get("num", 0))

    # ── pending confirmations (tagged-on-calendar awaiting strip/delete) ─────

    def pending_confirmations(self) -> list[dict]:
        """Return all entries currently tagged on the calendar awaiting confirmation."""
        return self._data.get("pending_confirmations", [])

    def add_pending_confirmation(
        self,
        gcal_event_id: str,
        calendar_id: str,
        original_title: str,
        current_tag: str,
        fingerprint: str,
        start_iso: str,
        num: int,
        thread_id: str | None = None,
        source_url: str | None = None,
        source: str = "",
    ) -> None:
        bucket = self._data.setdefault("pending_confirmations", [])
        bucket.append({
            "gcal_event_id": gcal_event_id,
            "calendar_id": calendar_id,
            "original_title": original_title,
            "current_tag": current_tag,
            "fingerprint": fingerprint,
            "start_dt": start_iso,
            "thread_id": thread_id,
            "source_url": source_url,
            "source": source,
            "created_at": _utcnow().isoformat(),
            "num": num,
        })

    def remove_pending_confirmation_by_gcal_id(self, gcal_event_id: str) -> dict | None:
        bucket = self._data.get("pending_confirmations", [])
        for i, entry in enumerate(bucket):
            if entry.get("gcal_event_id") == gcal_event_id:
                return bucket.pop(i)
        return None

    def remove_pending_confirmation_by_num(self, num: int) -> dict | None:
        bucket = self._data.get("pending_confirmations", [])
        for i, entry in enumerate(bucket):
            if entry.get("num") == num:
                return bucket.pop(i)
        return None

    def find_pending_confirmation_by_gcal_id(self, gcal_event_id: str) -> dict | None:
        for entry in self._data.get("pending_confirmations", []):
            if entry.get("gcal_event_id") == gcal_event_id:
                return entry
        return None

    def find_pending_confirmation_by_thread_id(self, thread_id: str) -> dict | None:
        if not thread_id:
            return None
        for entry in self._data.get("pending_confirmations", []):
            if entry.get("thread_id") == thread_id:
                return entry
        return None

    def find_all_pending_confirmations_by_thread_id(self, thread_id: str) -> list[dict]:
        if not thread_id:
            return []
        return [
            e for e in self._data.get("pending_confirmations", [])
            if e.get("thread_id") == thread_id
        ]

    def find_pending_confirmation_by_num(self, num: int) -> dict | None:
        for entry in self._data.get("pending_confirmations", []):
            if entry.get("num") == num:
                return entry
        return None

    def expire_pending_confirmations(self) -> list[dict]:
        """Remove entries whose start_dt has passed OR whose created_at is
        older than 30 days. Returns the removed entries so the caller can
        delete them from GCal and add their fingerprints to rejected.
        """
        now = _utcnow()
        cutoff_30d = now - timedelta(days=30)
        bucket = self._data.get("pending_confirmations", [])
        kept = []
        expired = []
        for entry in bucket:
            start_dt = _parse_dt(entry.get("start_dt"))
            created_at = _parse_dt(entry.get("created_at"))
            past_start = start_dt is not None and start_dt < now
            stale = created_at is not None and created_at < cutoff_30d
            if past_start or stale:
                expired.append(entry)
            else:
                kept.append(entry)
        self._data["pending_confirmations"] = kept
        return expired

    # ── invite context (native GCal invites — context-only, not written) ─────

    def record_invite_context(
        self,
        gcal_event_id: str,
        title: str,
        start_iso: str,
        attendees: list | None = None,
        source_url: str | None = None,
    ) -> None:
        bucket = self._data.setdefault("invite_context", {})
        bucket[gcal_event_id] = {
            "title": title,
            "start": start_iso,
            "attendees": attendees or [],
            "source_url": source_url,
            "recorded_at": _utcnow().isoformat(),
        }

    def invite_context(self) -> dict[str, dict]:
        return self._data.get("invite_context", {})

    def remove_invite_context(self, gcal_event_id: str) -> dict | None:
        bucket = self._data.get("invite_context", {})
        return bucket.pop(gcal_event_id, None)

    # ── processed Slack files (image/PDF intake) ────────────────────────────────

    def is_file_processed(self, file_id: str) -> bool:
        return file_id in self._data.get("processed_slack_files", {})

    def mark_file_processed(self, file_id: str, info: dict) -> None:
        bucket = self._data.setdefault("processed_slack_files", {})
        bucket[file_id] = {**info, "processed_at": _utcnow().isoformat()}

    # ── connector health (Tier 2 — Intake Audit surfacing) ──────────────────

    def record_connector_status(
        self, source: str, status_code: str, message: str, ts: datetime | None = None,
    ) -> None:
        """Record the outcome of a connector fetch.

        - On non-OK: increments consecutive_errors.
        - On OK: resets consecutive_errors to 0 and updates last_ok_at.
        - Always updates last_status_code, last_status_message, last_status_at.

        connector_health is bounded by the number of connectors (~8) — no
        pruning needed.
        """
        ts = ts or _utcnow()
        bucket = self._data.setdefault("connector_health", {}).setdefault(
            source,
            {
                "consecutive_errors": 0,
                "last_ok_at": None,
                "last_status_code": "ok",
                "last_status_message": "",
                "last_status_at": None,
            },
        )
        bucket["last_status_code"] = status_code
        bucket["last_status_message"] = message
        bucket["last_status_at"] = ts.isoformat()
        if status_code == "ok":
            bucket["consecutive_errors"] = 0
            bucket["last_ok_at"] = ts.isoformat()
        else:
            bucket["consecutive_errors"] = int(bucket.get("consecutive_errors", 0)) + 1

    def connector_health(self) -> dict[str, dict]:
        return self._data.get("connector_health", {})

    # ── pruning ───────────────────────────────────────────────────────────────

    def prune(self) -> None:
        """Remove stale entries to prevent unbounded growth."""
        cutoff = _utcnow() - timedelta(days=30)

        # Prune seen_message_ids by age (30 days), floor at 1000 most recent.
        # Entries are {"id": ..., "ts": ...}; bare strings are migrated on read.
        for source in self._data.get("seen_message_ids", {}):
            bucket = self._data["seen_message_ids"][source]
            recent = []
            for entry in bucket:
                if isinstance(entry, dict):
                    ts = _parse_dt(entry.get("ts"))
                    if ts is None or ts >= cutoff:
                        recent.append(entry)
                else:
                    # Migrate old bare-string format — assign now as ts
                    recent.append({"id": entry, "ts": _utcnow().isoformat()})
            self._data["seen_message_ids"][source] = recent[-1000:]

        # Prune fingerprints: format is sha256(title+date), date embedded as YYYY-MM-DD.
        # We can't decode the hash, so keep fingerprints for up to 30 days past last_run.
        # Simple approach: cap to most recent 5000 entries.
        fps = self._data.get("written_fingerprints", [])
        self._data["written_fingerprints"] = fps[-5000:]

        # Prune written_events: cap to most recent 200 entries (keyed by gcal_id).
        we = self._data.get("written_events", {})
        if len(we) > 200:
            # Sort by created_at and keep newest 200
            sorted_ids = sorted(we, key=lambda k: we[k].get("created_at", ""), reverse=True)
            self._data["written_events"] = {k: we[k] for k in sorted_ids[:200]}

        # Prune written_todo_fingerprints: cap to most recent 5000 entries.
        fps = self._data.get("written_todo_fingerprints", [])
        self._data["written_todo_fingerprints"] = fps[-5000:]

        # Prune warned_conflict_ids: drop entries older than 30 days.
        # Values are ISO date strings (YYYY-MM-DD); string comparison works for ISO dates.
        cutoff_date = cutoff.strftime("%Y-%m-%d")
        warned = self._data.get("warned_conflict_ids", {})
        self._data["warned_conflict_ids"] = {
            fp: date_str
            for fp, date_str in warned.items()
            if date_str >= cutoff_date
        }

        # Prune processed_slack_files: 90-day window, cap 500 entries.
        psf = self._data.get("processed_slack_files", {})
        cutoff_90 = (_utcnow() - timedelta(days=90)).isoformat()
        psf = {
            fid: info for fid, info in psf.items()
            if info.get("processed_at", "") >= cutoff_90
        }
        if len(psf) > 500:
            sorted_ids = sorted(
                psf, key=lambda k: psf[k].get("processed_at", ""), reverse=True
            )
            psf = {k: psf[k] for k in sorted_ids[:500]}
        self._data["processed_slack_files"] = psf

        # Prune recurring_notices: 24h window.
        cutoff_24h = (_utcnow() - timedelta(hours=24)).isoformat()
        self._data["recurring_notices"] = [
            n for n in self._data.get("recurring_notices", [])
            if n.get("seen_at", "") >= cutoff_24h
        ]

        # Prune rejected_fingerprints: 90-day window so a deliberate "no" lapses
        # eventually rather than blocking forever. User can wipe early via
        # `cli forget --fp <hash>` or `cli forget --title <substr>`.
        rejected = self._data.get("rejected_fingerprints", {})
        rej_cutoff = (_utcnow() - timedelta(days=90)).isoformat()
        self._data["rejected_fingerprints"] = {
            fp: info
            for fp, info in rejected.items()
            if info.get("rejected_at", "") >= rej_cutoff
        }

        # Prune proposal_dashboard: keep last 7 days of dashboard message ts entries.
        cutoff_dash = (_utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        self._data["proposal_dashboard"] = {
            k: v for k, v in self._data.get("proposal_dashboard", {}).items()
            if k >= cutoff_dash
        }

        # Prune invite_context: drop entries past start_dt or older than 30 d.
        invites = self._data.get("invite_context", {})
        cutoff_invite_recorded = (_utcnow() - timedelta(days=30)).isoformat()
        kept_invites: dict[str, dict] = {}
        for gcal_id, info in invites.items():
            recorded = info.get("recorded_at", "")
            start = _parse_dt(info.get("start"))
            if start is not None and start < _utcnow():
                continue
            if recorded < cutoff_invite_recorded:
                continue
            kept_invites[gcal_id] = info
        self._data["invite_context"] = kept_invites

        # pending_confirmations is pruned actively via expire_pending_confirmations()
        # (which the worker calls so it can delete from GCal). Belt-and-suspenders
        # safety net here: drop entries older than 90 d that somehow lingered.
        cutoff_90d = (_utcnow() - timedelta(days=90)).isoformat()
        confirmations = self._data.get("pending_confirmations", [])
        self._data["pending_confirmations"] = [
            e for e in confirmations
            if e.get("created_at", "") >= cutoff_90d
        ]

        # Prune pending_proposals: remove batches where all items are non-pending
        # AND the batch is older than 72 hours (3x the default expiry window).
        cutoff_proposals = _utcnow() - timedelta(hours=72)
        proposals = self._data.get("pending_proposals", [])
        kept = []
        for batch in proposals:
            created = _parse_dt(batch.get("created_at"))
            all_done = all(i["status"] != "pending" for i in batch.get("items", []))
            if all_done and created and created < cutoff_proposals:
                continue  # drop stale resolved batch
            kept.append(batch)
        self._data["pending_proposals"] = kept

        logger.debug("state pruned")


def load() -> State:
    if STATE_PATH.exists():
        with STATE_PATH.open() as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                logger.warning("state.json is corrupt; starting fresh")
                data = {}
    else:
        data = {}
    return State(data)


def save(state: State) -> None:
    if not _holding_lock():
        raise RuntimeError(
            "state.save() called without an active locked() block. "
            "Wrap the load+mutate+save sequence in `with state.locked():` "
            "to prevent torn writes across processes."
        )
    state.prune()
    import tempfile
    fd, tmp_path = tempfile.mkstemp(dir=STATE_PATH.parent, prefix=".state_", suffix=".tmp")
    try:
        with open(fd, "w") as f:
            json.dump(state._data, f, indent=2, default=str)
        Path(tmp_path).replace(STATE_PATH)
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    logger.debug("state saved to %s", STATE_PATH)
