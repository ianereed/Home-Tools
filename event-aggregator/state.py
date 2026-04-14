"""
Persistent state management.

state.json (gitignored) tracks:
- last_run timestamps per source
- seen message IDs for API-based sources (pruned to 30-day rolling window)
- written event fingerprints (pruned once event date has passed)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).parent / "state.json"

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
        return fp in self._data.get("written_fingerprints", [])

    def add_fingerprint(self, fp: str) -> None:
        fps = self._data.setdefault("written_fingerprints", [])
        if fp not in fps:
            fps.append(fp)

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
    ) -> None:
        bucket = self._data.setdefault("written_events", {})
        bucket[gcal_id] = {
            "title": title,
            "start": start_iso,
            "fingerprint": fingerprint,
            "created_at": _utcnow().isoformat(),
            "is_tentative": is_tentative,
        }

    def get_written_events(self) -> dict[str, dict]:
        return self._data.get("written_events", {})

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
