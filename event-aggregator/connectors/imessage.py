"""
iMessage/SMS connector — Phase 3.

Two ingestion paths:
  1. Direct chat.db read (default). Requires the host to have iMessage signed
     in and Full Disk Access granted to the Python invoking this connector.
  2. Shipped JSONL — when `config.IMESSAGE_EXPORT_FILE` is set, reads a JSONL
     file produced by `tools/imessage_export.py` running on a host that *does*
     have iMessage. This is how the headless Mac mini gets messages: a
     LaunchAgent on the user's laptop ships the JSONL via Tailscale SSH.

Direct chat.db path: copies DB to a tempfile before opening to avoid SQLite
lock errors while Messages.app is running.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from connectors.base import BaseConnector, ConnectorStatus, ConnectorStatusCode, FetchResult
import config
from models import RawMessage

logger = logging.getLogger(__name__)

# Apple epoch: seconds since 2001-01-01 00:00:00 UTC
_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
# macOS 10.13+ uses nanoseconds; older versions use seconds
_APPLE_EPOCH_NS_THRESHOLD = 1_000_000_000  # if value > this, treat as nanoseconds


def _apple_ts_to_utc(ts: int | float) -> datetime:
    if ts > _APPLE_EPOCH_NS_THRESHOLD:
        ts = ts / 1e9
    return _APPLE_EPOCH + timedelta(seconds=ts)


class IMessageConnector(BaseConnector):
    source_name = "imessage"

    def fetch(self, since: datetime, mock: bool = False) -> FetchResult:
        if mock:
            from tests.mock_data import imessage_messages
            return imessage_messages(since), ConnectorStatus.ok()

        if config.IMESSAGE_EXPORT_FILE:
            return self._fetch_from_export_file(since)

        db_path = Path(config.IMESSAGE_DB_PATH).expanduser()
        if not db_path.exists():
            logger.warning(
                "iMessage DB not found at %s — Full Disk Access likely missing for launchd",
                db_path,
            )
            return [], ConnectorStatus(
                ConnectorStatusCode.PERMISSION_DENIED,
                "chat.db unreadable — grant FDA to launchd python",
            )

        # Copy to temp file to avoid locking Messages.app
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            shutil.copy2(db_path, tmp_path)
            return self._query(tmp_path, since), ConnectorStatus.ok()
        except PermissionError:
            return [], ConnectorStatus(
                ConnectorStatusCode.PERMISSION_DENIED, "chat.db copy denied",
            )
        except sqlite3.OperationalError as exc:
            err = str(exc).lower()
            if "no such" in err or "column" in err:
                return [], ConnectorStatus(ConnectorStatusCode.SCHEMA_ERROR, type(exc).__name__)
            return [], ConnectorStatus(ConnectorStatusCode.UNKNOWN_ERROR, type(exc).__name__)
        except Exception as exc:
            logger.warning("iMessage fetch failed: %s", exc)
            return [], ConnectorStatus(ConnectorStatusCode.UNKNOWN_ERROR, type(exc).__name__)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _query(self, db_path: Path, since: datetime) -> list[RawMessage]:
        # Convert since to Apple epoch seconds for the query
        since_apple = (since - _APPLE_EPOCH).total_seconds() * 1e9  # nanoseconds

        messages = []
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            # TODO (Phase 3): verify column names on target macOS version
            # chat.db schema: message table with ROWID, text, date, handle_id, is_from_me
            rows = conn.execute(
                """
                SELECT ROWID, text, date, handle_id, is_from_me
                FROM message
                WHERE date > ?
                  AND text IS NOT NULL
                  AND text != ''
                ORDER BY date ASC
                LIMIT 500
                """,
                (since_apple,),
            ).fetchall()

        for row in rows:
            ts = _apple_ts_to_utc(row["date"])
            messages.append(
                RawMessage(
                    id=f"imessage_{row['ROWID']}",
                    source=self.source_name,
                    timestamp=ts,
                    body_text=row["text"],
                    metadata={"handle_id": row["handle_id"], "is_from_me": bool(row["is_from_me"])},
                )
            )

        logger.debug("imessage: fetched %d messages since %s", len(messages), since.date())
        return messages

    def _fetch_from_export_file(self, since: datetime) -> FetchResult:
        """
        JSONL ingestion path. Each line is a RawMessage-shaped dict produced by
        tools/imessage_export.py on a host with iMessage signed in.

        Never raises. When the file is older than `IMESSAGE_EXPORT_MAX_AGE_MIN`,
        still returns parsed messages (state.is_seen() prevents reprocessing
        old-but-already-handled rows) but tags the status as PERMISSION_DENIED
        so the dashboard surfaces the staleness — the laptop's exporter has
        likely stopped shipping, and that's a human-actionable signal.

        Privacy invariant from connectors/base.py:32-37 applies: status messages
        contain only file-system state, integer counts, exception class names —
        never bodies, contacts, or timestamps.
        """
        path = Path(config.IMESSAGE_EXPORT_FILE).expanduser()
        if not path.exists():
            return [], ConnectorStatus(
                ConnectorStatusCode.PERMISSION_DENIED,
                "export file missing — laptop exporter not running",
            )

        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            return [], ConnectorStatus(
                ConnectorStatusCode.UNKNOWN_ERROR, type(exc).__name__,
            )

        age_min = (time.time() - mtime) / 60.0

        try:
            messages = self._parse_export_file(path, since)
        except json.JSONDecodeError:
            return [], ConnectorStatus(
                ConnectorStatusCode.SCHEMA_ERROR, "export jsonl parse failed",
            )
        except (KeyError, TypeError, ValueError):
            return [], ConnectorStatus(
                ConnectorStatusCode.SCHEMA_ERROR, "export schema mismatch",
            )
        except OSError as exc:
            return [], ConnectorStatus(
                ConnectorStatusCode.UNKNOWN_ERROR, type(exc).__name__,
            )

        if age_min > config.IMESSAGE_EXPORT_MAX_AGE_MIN:
            return messages, ConnectorStatus(
                ConnectorStatusCode.PERMISSION_DENIED,
                f"export file stale — {int(age_min)} min old; check laptop launchd",
            )

        logger.debug(
            "imessage: %d export-file messages since %s (file age %d min)",
            len(messages), since.date(), int(age_min),
        )
        return messages, ConnectorStatus.ok()

    def _parse_export_file(self, path: Path, since: datetime) -> list[RawMessage]:
        messages: list[RawMessage] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                ts = datetime.fromisoformat(obj["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts <= since:
                    continue
                messages.append(
                    RawMessage(
                        id=obj["id"],
                        source=obj["source"],
                        timestamp=ts,
                        body_text=obj["body_text"],
                        metadata=obj.get("metadata", {}),
                    )
                )
        return messages
