"""Local state for the watcher: stability gate + dedup + large-file tracking.

Persisted in state.json (LOCAL on the mini, NOT on NAS):
- seen[path] = [size, mtime] — for two-tick stability gate
- processed_sha256[] — LRU list capped at DEDUP_HISTORY (most-recent-first)
- timeout_counts[sha] = n — # of subprocess timeouts on the small-file path;
    triggers large-file mode at LARGE_FILE_TRIGGER_TIMEOUTS
- in_flight_large[sha] = {started_at, staging_id, source_path} — set while
    the large-file subprocess is running, cleared on success or wedge
- wedged[sha] = {path, reason, wedged_at, staging_id} — files where hangup
    detection killed the subprocess; source renamed `_WEDGED_<orig>` in place
- health = computed snapshot for the dashboard

Atomic save: write to .tmp, os.replace.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class State:
    def __init__(self, path: Path = config.STATE_PATH) -> None:
        self.path = path
        self.seen: dict[str, list[float]] = {}
        self.processed_sha256: list[str] = []
        self.timeout_counts: dict[str, int] = {}
        self.in_flight_large: dict[str, dict] = {}
        self.wedged: dict[str, dict] = {}

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.seen = dict(data.get("seen", {}))
            self.processed_sha256 = list(data.get("processed_sha256", []))
            self.timeout_counts = dict(data.get("timeout_counts", {}))
            self.in_flight_large = dict(data.get("in_flight_large", {}))
            self.wedged = dict(data.get("wedged", {}))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("state.load: %s; starting empty", exc)
            self.seen, self.processed_sha256 = {}, []
            self.timeout_counts, self.in_flight_large, self.wedged = {}, {}, {}

    def save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({
            "seen": self.seen,
            "processed_sha256": self.processed_sha256,
            "timeout_counts": self.timeout_counts,
            "in_flight_large": self.in_flight_large,
            "wedged": self.wedged,
            "health": self.health_snapshot(),
        }, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ── stability gate ─────────────────────────────────────────────────
    def stable_key(self, p: Path) -> tuple[int, float] | None:
        """Return (size, mtime) if file looks stable, else None."""
        try:
            st = p.stat()
            return (st.st_size, st.st_mtime)
        except OSError:
            return None

    def stability_check(self, p: Path) -> str:
        """Returns 'first_sighting' | 'stable' | 'changed' | 'unreadable'.

        - first_sighting: not in seen → record + skip this tick
        - stable: same (size, mtime) as last seen → OK to process
        - changed: differs from last seen → re-record + skip (still uploading)
        - unreadable: stat failed → skip
        """
        cur = self.stable_key(p)
        if cur is None:
            return "unreadable"
        key = str(p)
        prev = self.seen.get(key)
        if prev is None:
            self.seen[key] = list(cur)
            return "first_sighting"
        if list(cur) == list(prev):
            return "stable"
        self.seen[key] = list(cur)
        return "changed"

    def forget(self, p: Path) -> None:
        self.seen.pop(str(p), None)

    # ── dedup ──────────────────────────────────────────────────────────
    def is_processed(self, sha: str) -> bool:
        return sha in self.processed_sha256

    def remember(self, sha: str) -> None:
        if sha in self.processed_sha256:
            return
        self.processed_sha256.insert(0, sha)
        if len(self.processed_sha256) > config.DEDUP_HISTORY:
            del self.processed_sha256[config.DEDUP_HISTORY:]

    # ── large-file tracking ────────────────────────────────────────────
    def record_timeout(self, sha: str) -> int:
        n = self.timeout_counts.get(sha, 0) + 1
        self.timeout_counts[sha] = n
        return n

    def clear_timeout(self, sha: str) -> None:
        self.timeout_counts.pop(sha, None)

    def should_use_large_file_path(self, sha: str) -> bool:
        return self.timeout_counts.get(sha, 0) >= config.LARGE_FILE_TRIGGER_TIMEOUTS

    def mark_in_flight_large(self, sha: str, source: Path, staging_id: str) -> None:
        self.in_flight_large[sha] = {
            "started_at": _utcnow(),
            "staging_id": staging_id,
            "source_path": str(source),
        }

    def clear_in_flight_large(self, sha: str) -> None:
        self.in_flight_large.pop(sha, None)

    def mark_wedged(self, sha: str, source: Path, reason: str, staging_id: str = "") -> None:
        self.wedged[sha] = {
            "path": str(source),
            "reason": reason,
            "wedged_at": _utcnow(),
            "staging_id": staging_id,
        }
        # Stop trying — clear in-flight, keep timeout_counts for forensics.
        self.in_flight_large.pop(sha, None)

    def clear_wedged(self, sha: str) -> None:
        self.wedged.pop(sha, None)

    # ── health snapshot (read by the service-monitor collector) ───────
    def health_snapshot(self) -> dict:
        """Compact view computed every save(); the dashboard reads this dict
        instead of recomputing from the underlying maps. Cheap to build."""
        # files_pending = "seen" entries that aren't yet processed by sha
        # (we don't know each seen-entry's sha cheaply, so we just expose the
        # raw count of files we've spotted that aren't archived yet — close
        # enough for the dashboard).
        return {
            "files_seen": len(self.seen),
            "files_processed_total": len(self.processed_sha256),
            "files_in_flight_large": len(self.in_flight_large),
            "files_wedged": len(self.wedged),
            "files_with_timeouts": len(self.timeout_counts),
            "computed_at": _utcnow(),
        }
