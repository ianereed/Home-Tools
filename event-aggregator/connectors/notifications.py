"""
macOS Notification Center connector — Phase 5.

Covers Facebook Messenger and Instagram DMs via their macOS notification payloads.

SECURITY NOTE: The Notification Center DB contains notifications from ALL apps
(banking, health, etc.). This connector filters STRICTLY by bundle ID — nothing
else is read or processed. A warning is logged at every startup.

Requires Full Disk Access for Terminal/Python process.
Notification text is often truncated to 40–80 chars by macOS; treat as best-effort.
"""
from __future__ import annotations

import glob
import logging
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from connectors.base import BaseConnector
from models import RawMessage

logger = logging.getLogger(__name__)

_NC_DB_GLOB = str(
    Path("~/Library/Application Support/NotificationCenter/*.db").expanduser()
)
_ALLOWED_BUNDLE_IDS = frozenset({
    "com.facebook.Messenger",
    "com.burbn.instagram",
})
_SOURCE_BY_BUNDLE = {
    "com.facebook.Messenger": "messenger",
    "com.burbn.instagram": "instagram",
}

# macOS Notification Center uses CoreData timestamps (seconds since 2001-01-01)
_APPLE_EPOCH_UTC = datetime(2001, 1, 1, tzinfo=timezone.utc)


class NotificationCenterConnector(BaseConnector):
    source_name = "notifications"  # internal; individual messages get "messenger"/"instagram"

    def __init__(self) -> None:
        logger.debug(
            "notifications connector: will read macOS Notification Center DB "
            "filtered strictly to Messenger/Instagram bundle IDs"
        )

    def fetch(self, since: datetime, mock: bool = False) -> list[RawMessage]:
        if mock:
            from tests.mock_data import notification_messages
            return notification_messages(since)

        db_paths = glob.glob(_NC_DB_GLOB)
        if not db_paths:
            logger.debug(
                "Notification Center DB not found at %s — "
                "Messenger/Instagram notifications unavailable (macOS Sequoia "
                "removed this DB; or grant Full Disk Access to enable it)",
                _NC_DB_GLOB,
            )
            return []

        messages = []
        for db_path_str in db_paths:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                shutil.copy2(db_path_str, tmp_path)
                messages.extend(self._query(tmp_path, since))
            except Exception as exc:
                logger.warning("notifications: failed to read %s: %s", db_path_str, exc)
            finally:
                tmp_path.unlink(missing_ok=True)

        logger.debug("notifications: fetched %d messages since %s", len(messages), since.date())
        return messages

    def _query(self, db_path: Path, since: datetime) -> list[RawMessage]:
        since_apple = (since - _APPLE_EPOCH_UTC).total_seconds()
        results = []

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                # Schema varies by macOS version; ZNOTIFICATION is common
                rows = conn.execute(
                    """
                    SELECT ZIDENTIFIER, ZBUNDLEID, ZTITLE, ZBODY, ZDATE
                    FROM ZNOTIFICATION
                    WHERE ZBUNDLEID IN ({})
                      AND ZDATE > ?
                    ORDER BY ZDATE ASC
                    LIMIT 500
                    """.format(",".join("?" * len(_ALLOWED_BUNDLE_IDS))),
                    (*_ALLOWED_BUNDLE_IDS, since_apple),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                logger.warning("notifications: DB schema not as expected: %s", exc)
                return []

        for row in rows:
            bundle_id = row["ZBUNDLEID"]
            if bundle_id not in _ALLOWED_BUNDLE_IDS:
                continue  # paranoia check — belt and suspenders
            source = _SOURCE_BY_BUNDLE[bundle_id]
            title = row["ZTITLE"] or ""
            body = row["ZBODY"] or ""
            text = f"{title}: {body}".strip(": ").strip()
            if not text:
                continue

            from datetime import timedelta
            ts = _APPLE_EPOCH_UTC + timedelta(seconds=float(row["ZDATE"]))
            results.append(
                RawMessage(
                    id=f"{source}_{row['ZIDENTIFIER']}",
                    source=source,
                    timestamp=ts,
                    body_text=text,
                    metadata={},
                )
            )
        return results
