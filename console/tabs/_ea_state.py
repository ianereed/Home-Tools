"""Read-only view into event-aggregator's state for the console Decisions tab.

The console is a separate project/venv from event-aggregator but shares the
filesystem on the mini. event-aggregator's `state.py:save()` writes via an atomic
`os.replace`, so a single read sees the complete old-or-new file, never a torn
write — a foreign reader needs **no flock** (taking one would block EA writers and
risk the orphan-fd class of bug the project avoids). All readers here degrade to
empty/default on any failure so the tab never crashes.

Kept out of `decisions.py` so the loaders + `health_badge` are unit-testable
without importing Streamlit.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

EA_DIR = Path.home() / "Home-Tools" / "event-aggregator"
STATE_PATH = EA_DIR / "state.json"
EVENT_LOG_PATH = EA_DIR / "event_log.jsonl"

# Health thresholds — shared with the Phase 6 alert-card kind so the strip and the
# card agree on what "unhealthy" means.
ERR_THRESHOLD = 3       # consecutive_errors at/above this → RED
STALE_HOURS = 6.0       # no successful fetch in this many hours → RED

# Sources that are intentionally unconfigured on the mini — never alarm on these.
# whatsapp: needs Full Disk Access; discord: no bot token provisioned.
IGNORED_SOURCES = frozenset({"whatsapp", "discord"})

_GREEN, _RED, _YELLOW = "🟢", "🔴", "🟡"


def _read_json_tolerant(path: Path, attempts: int = 3) -> dict:
    """Read a JSON file read-only. `save()` does an atomic `os.replace`, so a single
    read is never torn; the retry only covers the microscopic ENOENT/empty window
    while the inode is swapped between open() and read(). Returns {} on persistent
    failure (missing file, truncated/invalid JSON) — callers treat that as no-data."""
    for i in range(attempts):
        try:
            txt = path.read_text()
            if not txt.strip():
                raise ValueError("empty")
            return json.loads(txt)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            if i == attempts - 1:
                return {}
            time.sleep(0.05)
    return {}


def load_pending_items() -> list[dict]:
    """Flatten every still-pending proposal item across all batches, newest first."""
    data = _read_json_tolerant(STATE_PATH)
    items: list[dict] = []
    for batch in data.get("pending_proposals", []):
        batch_created = batch.get("created_at")
        for it in batch.get("items", []):
            if it.get("status") == "pending":
                # carry the batch timestamp so the UI can show age / order
                items.append({**it, "_batch_created_at": batch_created})
    items.sort(key=lambda it: it.get("_batch_created_at") or "", reverse=True)
    return items


def load_written_events() -> list[dict]:
    """Return auto-written calendar events (state.written_events), newest first.

    Each entry is the stored dict plus a `gcal_id` key so the UI can offer undo."""
    data = _read_json_tolerant(STATE_PATH)
    written = data.get("written_events", {})
    out = [{**info, "gcal_id": gid} for gid, info in written.items()]
    out.sort(key=lambda e: e.get("created_at") or e.get("start") or "", reverse=True)
    return out


def load_connector_health() -> dict[str, dict]:
    """Return the per-source connector_health map (may be {})."""
    return _read_json_tolerant(STATE_PATH).get("connector_health", {})


def load_recent_log(limit: int = 50) -> list[dict]:
    """Tail the append-only event_log.jsonl, newest first. Drops unparseable lines
    (e.g. a partial final line if a writer crashed mid-append)."""
    if not EVENT_LOG_PATH.exists():
        return []
    try:
        lines = EVENT_LOG_PATH.read_text().splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


def _age_hours(iso_ts: str | None, now: datetime | None = None) -> float | None:
    """Hours since `iso_ts`, or None if absent/unparseable. Clamped at 0 for skew."""
    if not iso_ts:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def _fmt_age(age_h: float | None) -> str:
    if age_h is None:
        return "never"
    if age_h < 1:
        return f"{int(age_h * 60)}m"
    if age_h < 48:
        return f"{age_h:.1f}h"
    return f"{age_h / 24:.0f}d"


def health_badge(
    h: dict,
    *,
    err_threshold: int = ERR_THRESHOLD,
    stale_hours: float = STALE_HOURS,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Return (icon, caption) for one source's health dict.

    RED    consecutive_errors >= err_threshold, OR last success older than stale_hours
    YELLOW degraded (1..err_threshold-1 errors) or never succeeded
    GREEN  clean and fresh
    """
    errs = int(h.get("consecutive_errors", 0) or 0)
    age_h = _age_hours(h.get("last_ok_at"), now)
    if errs >= err_threshold or (age_h is not None and age_h > stale_hours):
        icon = _RED
    elif errs > 0 or age_h is None:
        icon = _YELLOW
    else:
        icon = _GREEN
    caption = f"ok {_fmt_age(age_h)} ago · err {errs} · {h.get('last_status_code', '?')}"
    return icon, caption


def is_unhealthy(
    src: str,
    h: dict,
    *,
    err_threshold: int = ERR_THRESHOLD,
    stale_hours: float = STALE_HOURS,
    now: datetime | None = None,
) -> str | None:
    """Return a human reason string if `src` is unhealthy and not intentionally
    unconfigured, else None. Shared by the strip and the Phase 6 alert card."""
    if src in IGNORED_SOURCES:
        return None
    errs = int(h.get("consecutive_errors", 0) or 0)
    if errs >= err_threshold:
        return f"{errs} consecutive errors"
    age_h = _age_hours(h.get("last_ok_at"), now)
    if age_h is not None and age_h > stale_hours:
        return f"no success in {_fmt_age(age_h)}"
    return None
