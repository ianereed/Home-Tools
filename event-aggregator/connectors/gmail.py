"""
Gmail connector — Phase 2.

Fetches emails since `since` via Gmail API (OAuth2).
Returns RawMessage with body_text = plain-text email body.

Triage policy: every gmail message is forwarded to the worker. The
pre-classifier (qwen3, 2k ctx) decides yes/no/maybe — that's the right
tool to distinguish event-bearing mail from newsletters and noise.
Gmail's CATEGORY_PROMOTIONS / CATEGORY_UPDATES labels are unreliable
(genuine event mail routinely lands in UPDATES), so we no longer drop
on them.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone

from googleapiclient.discovery import build

import config
from connectors import google_auth
from connectors.base import BaseConnector, ConnectorStatus, ConnectorStatusCode, FetchResult
from models import RawMessage

logger = logging.getLogger(__name__)

_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_MAX_RESULTS = 100

# Thread digest caps — keep last N messages, body snippet K chars each.
_THREAD_DIGEST_MAX_MESSAGES = 5
_THREAD_DIGEST_SNIPPET_CHARS = 500


class GmailConnector(BaseConnector):
    source_name = "gmail"

    def __init__(self) -> None:
        self._user_email: str = ""  # cached after first fetch; never changes

    def fetch(self, since: datetime, mock: bool = False) -> FetchResult:
        if mock:
            from tests.mock_data import gmail_messages
            return gmail_messages(since), ConnectorStatus.ok()

        try:
            creds = google_auth.get_credentials(
                scopes=_GMAIL_SCOPES,
                token_path=config.GMAIL_TOKEN_JSON,
                credentials_path=config.GMAIL_CREDENTIALS_JSON,
                keyring_key="gmail_token",
            )
            service = build("gmail", "v1", credentials=creds)

            if not self._user_email:
                profile = service.users().getProfile(userId="me").execute()
                self._user_email = profile.get("emailAddress", "").lower()
            user_email = self._user_email

            after_ts = int(since.timestamp())
            result = (
                service.users()
                .messages()
                .list(userId="me", q=f"after:{after_ts}", maxResults=_MAX_RESULTS)
                .execute()
            )
            refs = result.get("messages", [])
            logger.debug("gmail: %d message(s) since %s", len(refs), since.date())

            messages = []
            for ref in refs:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute()
                )
                thread = _fetch_thread(service, msg.get("threadId"))
                raw = _parse_message(msg, thread, user_email)
                if raw:
                    messages.append(raw)
            return messages, ConnectorStatus.ok()

        except FileNotFoundError as exc:
            logger.warning("gmail: credentials not set up — %s", exc)
            return [], ConnectorStatus(
                ConnectorStatusCode.NO_CREDENTIALS, "client secrets file missing",
            )
        except Exception as exc:
            err_name = type(exc).__name__
            err_str = str(exc).lower()
            if "refresh" in err_name.lower() or "invalid_grant" in err_str or "401" in err_str or "403" in err_str:
                logger.warning("gmail: auth error — %s", err_name)
                return [], ConnectorStatus(ConnectorStatusCode.AUTH_ERROR, err_name)
            if "timeout" in err_name.lower() or "timeout" in err_str or "connection" in err_str:
                logger.warning("gmail: network error — %s", err_name)
                return [], ConnectorStatus(ConnectorStatusCode.NETWORK_ERROR, err_name)
            logger.warning("gmail connector error: %s", exc)
            return [], ConnectorStatus(ConnectorStatusCode.UNKNOWN_ERROR, err_name)


def _fetch_thread(service, thread_id: str | None) -> dict | None:
    """Fetch the full thread for a message. Returns the thread dict, or None on error."""
    if not thread_id:
        return None
    try:
        return (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
    except Exception as exc:
        logger.debug("gmail: could not fetch thread %s: %s", thread_id, exc)
        return None


def _is_from_me(msg: dict, user_email: str) -> bool:
    """True if this gmail message was sent by the user.

    Primary signal: 'SENT' label. Fallback: From header contains user email.
    Either alone is sufficient — Gmail tags every message the user sent with
    SENT, and From-match catches edge cases where the label is missing.
    """
    if "SENT" in msg.get("labelIds", []):
        return True
    if user_email:
        payload = msg.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        from_header = headers.get("from", "").lower()
        if user_email in from_header:
            return True
    return False


def _build_thread_digest(thread: dict | None, user_email: str) -> list[dict]:
    """Compact, ordered (oldest-first) digest of the last N thread messages.

    Each entry: {from_me, ts, subject, snippet}. Bodies are truncated to
    _THREAD_DIGEST_SNIPPET_CHARS. The full bodies never appear in logs or
    state — only the snippet is persisted in metadata so the worker can
    feed it to the LLM.
    """
    if not thread:
        return []
    msgs = thread.get("messages", []) or []
    # Take the last N messages so the LLM always sees the most recent context.
    tail = msgs[-_THREAD_DIGEST_MAX_MESSAGES:]
    digest: list[dict] = []
    for tm in tail:
        payload = tm.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        from_header = headers.get("from", "").lower()
        from_me = bool(user_email and user_email in from_header) or (
            "SENT" in tm.get("labelIds", [])
        )
        ts = datetime.fromtimestamp(
            int(tm.get("internalDate", 0)) / 1000, tz=timezone.utc
        )
        body = _extract_plain_text(payload) or ""
        digest.append({
            "from_me": from_me,
            "ts": ts.isoformat(),
            "subject": headers.get("subject", "")[:200],
            "snippet": body[:_THREAD_DIGEST_SNIPPET_CHARS],
        })
    return digest


def _parse_message(msg: dict, thread: dict | None, user_email: str) -> RawMessage | None:
    """Extract a RawMessage from a Gmail API full message object. Returns None if no text body."""
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    body_text = _extract_plain_text(payload)
    if not body_text:
        return None

    # internalDate is milliseconds since epoch
    ts = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc)

    is_from_me = _is_from_me(msg, user_email)
    thread_digest = _build_thread_digest(thread, user_email)

    return RawMessage(
        id=msg["id"],
        source="gmail",
        timestamp=ts,
        body_text=body_text,
        metadata={
            "from": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "source_url": f"https://mail.google.com/mail/u/0/#all/{msg['id']}",
            "thread_id": msg.get("threadId", ""),
            "is_from_me": is_from_me,
            "thread_digest": thread_digest,
        },
    )


def _extract_plain_text(part: dict) -> str:
    """Recursively find and decode the first text/plain MIME part."""
    if part.get("mimeType") == "text/plain":
        data = part.get("body", {}).get("data", "")
        if data:
            # Gmail uses URL-safe base64 without padding; add '==' to be safe
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    for sub in part.get("parts", []):
        result = _extract_plain_text(sub)
        if result:
            return result

    return ""
