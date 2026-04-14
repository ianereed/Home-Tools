"""
Gmail connector — Phase 2.

Fetches emails since `since` via Gmail API (OAuth2).
Returns RawMessage with body_text = plain-text email body.

Marketing filter: emails labelled CATEGORY_PROMOTIONS or CATEGORY_UPDATES are
silently dropped UNLESS the user has replied to that thread with language that
confirms they intend to attend or participate (e.g. "sounds good", "we'll be there").
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone

from googleapiclient.discovery import build

import config
from connectors import google_auth
from connectors.base import BaseConnector
from models import RawMessage

logger = logging.getLogger(__name__)

_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_MAX_RESULTS = 100

# Gmail labels that indicate marketing / automated mail
_MARKETING_LABELS = frozenset({"CATEGORY_PROMOTIONS", "CATEGORY_UPDATES"})

# Patterns in a user reply that indicate positive confirmation
_CONFIRM_RE = re.compile(
    r"\b("
    r"yes|sure|sounds good|count me in|i['']?ll be there|we['']?ll be there"
    r"|we will|i will|definitely|absolutely|confirmed|looking forward"
    r"|see you there|i['']?m in|we['']?re in|attending|i['']?ll attend"
    r"|we['']?ll attend|let['']?s do it|i['']?m coming|we['']?re coming"
    r"|perfect|great|works for me|works for us"
    r")\b",
    re.IGNORECASE,
)


class GmailConnector(BaseConnector):
    source_name = "gmail"

    def __init__(self) -> None:
        self._user_email: str = ""  # cached after first fetch; never changes

    def fetch(self, since: datetime, mock: bool = False) -> list[RawMessage]:
        if mock:
            from tests.mock_data import gmail_messages
            return gmail_messages(since)

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
                if _is_marketing(msg):
                    if not _thread_has_user_confirmation(service, msg, user_email):
                        logger.debug(
                            "gmail: dropping unconfirmed marketing message id=%s", msg["id"]
                        )
                        continue
                    logger.debug(
                        "gmail: keeping marketing message id=%s — user replied with confirmation",
                        msg["id"],
                    )

                raw = _parse_message(msg)
                if raw:
                    messages.append(raw)
            return messages

        except FileNotFoundError as exc:
            logger.warning("gmail: credentials not set up — %s", exc)
            return []
        except Exception as exc:
            logger.warning("gmail connector error: %s", exc)
            return []


def _is_marketing(msg: dict) -> bool:
    """Return True if Gmail has categorised this message as promotional/automated."""
    return bool(_MARKETING_LABELS & set(msg.get("labelIds", [])))


def _thread_has_user_confirmation(service, msg: dict, user_email: str) -> bool:
    """
    Fetch the thread for this message and check whether the user replied with
    language that confirms they intend to attend or participate.
    """
    thread_id = msg.get("threadId")
    if not thread_id:
        return False

    original_date = int(msg.get("internalDate", 0))

    try:
        thread = (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
    except Exception as exc:
        logger.debug("gmail: could not fetch thread %s: %s", thread_id, exc)
        return False

    for thread_msg in thread.get("messages", []):
        # Only look at messages sent AFTER the original marketing email
        if int(thread_msg.get("internalDate", 0)) <= original_date:
            continue

        payload = thread_msg.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        from_header = headers.get("from", "").lower()

        # Only consider messages sent by the user themselves
        if user_email not in from_header:
            continue

        reply_text = _extract_plain_text(payload)
        if reply_text and _CONFIRM_RE.search(reply_text):
            return True

    return False


def _parse_message(msg: dict) -> RawMessage | None:
    """Extract a RawMessage from a Gmail API full message object. Returns None if no text body."""
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    body_text = _extract_plain_text(payload)
    if not body_text:
        return None

    # internalDate is milliseconds since epoch
    ts = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc)

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
