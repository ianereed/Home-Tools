"""
Event extractor: sends message body_text to local Ollama and parses the response
into a list of CandidateEvent objects.

Privacy: body_text never appears in logs. Log only source/id/count metadata.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

import config
from models import CandidateEvent, CandidateTodo, RawMessage

logger = logging.getLogger(__name__)

_TITLE_MAX_CHARS = 200
_UNSAFE_TITLE_RE = re.compile(r"[<>\"'`]|ignore.*instruction|system prompt", re.IGNORECASE)
_FUTURE_YEARS = 2
_BODY_MAX_CHARS = 2000  # truncate before sending to Ollama


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _years(n: int) -> object:
    from datetime import timedelta
    return timedelta(days=365 * n)


# ── Prompt templates ──────────────────────────────────────────────────────────

# Schema block — uses TIMEZONE_PLACEHOLDER so we can substitute without conflicting
# with JSON braces.
_TIMEZONE_PLACEHOLDER = "<<USER_TIMEZONE>>"
_TODAY_PLACEHOLDER = "<<TODAY>>"

_SCHEMA = (
    "Respond with JSON matching exactly this schema:\n"
    '{"events": [{\n'
    '  "title": "...",\n'
    '  "date_certainty": "specific|approximate|unknown",\n'
    '  "start": "YYYY-MM-DDTHH:MM:SS+HH:MM (omit or null when date_certainty=unknown)",\n'
    '  "end": "YYYY-MM-DDTHH:MM:SS+HH:MM or null",\n'
    '  "location": "... or null",\n'
    '  "confidence": 0.0,\n'
    '  "is_update": false,\n'
    '  "original_title_hint": "name of existing event this updates, or null",\n'
    '  "is_cancellation": false,\n'
    '  "is_recurring": false,\n'
    '  "recurrence_hint": "e.g. weekly on Tuesdays, or null",\n'
    '  "attendees": [{"name": "...", "email": "... or null"}],\n'
    '  "category": "work|personal|social|health|travel|other",\n'
    '  "event_description": "natural-language description of what is being planned (used when date_certainty is unknown)",\n'
    '  "confirmation_status": "confirmed|awaiting_me|proposed_by_me"\n'
    "}],\n"
    '"todos": [{\n'
    '  "title": "short action item (max 200 chars)",\n'
    '  "context": "who this involves and what it is about, or null",\n'
    '  "due_date": "YYYY-MM-DD or null",\n'
    '  "priority": "urgent|high|normal|low",\n'
    '  "confidence": 0.0\n'
    "}]}\n"
    'If no events are found, use: "events": []\n'
    'If no action items are found, use: "todos": []\n'
    "\n"
    "Event field instructions:\n"
    '- date_certainty: "specific" if a precise date and time are explicitly given; '
    '"approximate" if you must guess (e.g. "next week" → pick a sensible date but flag it); '
    '"unknown" if the message is clearly proposing/discussing an event but no date is determinable yet '
    '(e.g. "let\'s plan a beach trip soon"). When unknown, you may omit `start` or set it to null.\n'
    "- event_description: short natural-language description of what is being planned. REQUIRED when "
    "date_certainty is \"unknown\"; optional otherwise (used as a hint to the user).\n"
    "- confidence: 0.0–1.0. How certain are you this is a real scheduled event (with or without a specific date)?\n"
    "- is_update: true if this message reschedules or changes details of a previously-mentioned event\n"
    "- original_title_hint: your best guess at the existing event title, REQUIRED when is_update or is_cancellation is true so the tool can find the matching calendar event\n"
    "- is_cancellation: true if this message explicitly cancels a scheduled event\n"
    "- is_recurring: true for events that repeat (weekly, monthly, etc.) — prevents duplicate creation\n"
    "- attendees: list people mentioned as participants; include email if visible in the message\n"
    "- category: best-fit category for GCal color coding\n"
    "- confirmation_status: classify the agreement state shown by the message + thread context.\n"
    '    "awaiting_me" — someone proposed this to the user and the user has not yet acknowledged; default for inbound mail without a reply.\n'
    '    "proposed_by_me" — the user proposed this and the other party has not yet confirmed; default when the message itself is from the user.\n'
    '    "confirmed" — both sides have acknowledged this happens (e.g. user said "yes/sounds good/see you then" AND the other party also acknowledged, or vice-versa).\n'
    "    Treat ambiguous threads as awaiting_me / proposed_by_me — only output \"confirmed\" with explicit mutual agreement.\n"
    f"- Today's date is {_TODAY_PLACEHOLDER}. Use this to resolve relative dates like 'next Friday' or 'this Thursday'.\n"
    f"- The user's local timezone is {_TIMEZONE_PLACEHOLDER}. "
    "Interpret all relative times in that timezone and return ISO8601 with UTC offset.\n"
    "\n"
    "Todo field instructions:\n"
    "- Extract: commitments you made, tasks assigned to you, things to follow up on\n"
    "- Do NOT put scheduled calendar events in todos — those belong in events only\n"
    "- title: short, actionable description (e.g. 'Send Q2 report to Sarah', 'Review draft proposal')\n"
    "- context: who you were talking with and what message or thread this came from\n"
    "- due_date: only if an explicit deadline is mentioned, otherwise null\n"
    "- priority: urgent=must do immediately, high=important, normal=standard, low=nice-to-have\n"
    "- confidence: 0.0–1.0. How certain are you this is a real action item you need to act on?"
)

# Intro lines per source type
_SOURCE_INTROS: dict[str, str] = {
    "email": (
        "You are an event extraction assistant. Extract scheduled events from the email below.\n"
        "Context: This is a formal email. You may also see a Thread digest summarizing the most recent messages in the same email thread, with [me] marking messages the user sent and [them] marking inbound messages. Use the digest to decide confirmation_status: awaiting_me if someone proposed and the user hasn't agreed, proposed_by_me if the user proposed and the other party hasn't replied, confirmed if both sides have explicitly acknowledged."
    ),
    "calendar": (
        "You are an event extraction assistant. Analyze this calendar invite.\n"
        "Context: This is a structured calendar invite. "
        "The attendees listed are people who have been invited."
    ),
    "chat": (
        "You are an event extraction assistant. Extract scheduled events from the chat message below.\n"
        'Context: This is an informal chat message. Dates may be relative (e.g., "this Thursday", "next week").'
    ),
    "default": (
        "You are an event extraction assistant. Extract scheduled events from the message below."
    ),
}

# Map source names to intro keys
_SOURCE_TO_INTRO: dict[str, str] = {
    "gmail":     "email",
    "gcal":      "calendar",
    "slack":     "chat",
    "imessage":  "chat",
    "whatsapp":  "chat",
    "discord":   "chat",
    "messenger": "chat",
    "instagram": "chat",
}


def _build_context_block(msg: RawMessage) -> str:
    """Build a metadata context header to prepend to the message body in the prompt."""
    lines = []
    m = msg.metadata

    if msg.source == "gmail":
        if m.get("from"):
            lines.append(f"From: {m['from'][:200]}")
        if m.get("subject"):
            lines.append(f"Subject: {m['subject'][:200]}")
        if m.get("to"):
            lines.append(f"To: {m['to'][:300]}")
        if m.get("cc"):
            lines.append(f"CC: {m['cc'][:300]}")
        if m.get("is_from_me"):
            lines.append("Direction: this message was sent by the user (outbound)")
        else:
            lines.append("Direction: this message was sent to the user (inbound)")
        digest = m.get("thread_digest") or []
        if digest:
            lines.append("Thread digest (oldest → newest):")
            for entry in digest:
                marker = "[me]" if entry.get("from_me") else "[them]"
                ts = (entry.get("ts") or "")[:16].replace("T", " ")
                subject = (entry.get("subject") or "")[:120]
                snippet = (entry.get("snippet") or "").replace("\n", " ")[:400]
                lines.append(f"  {marker} {ts} | {subject} | {snippet}")

    elif msg.source == "gcal":
        if m.get("summary"):
            lines.append(f"Event title: {m['summary'][:200]}")
        if m.get("start"):
            lines.append(f"When: {m['start']}")
        if m.get("location"):
            lines.append(f"Location: {m['location'][:200]}")
        if m.get("attendees"):
            emails = ", ".join(str(e) for e in m["attendees"][:20])
            lines.append(f"Attendees: {emails}")

    elif msg.source == "slack":
        if m.get("sender_name"):
            lines.append(f"Sender: {m['sender_name'][:100]}")
        if m.get("channel"):
            lines.append(f"Channel: {m['channel'][:100]}")

    elif msg.source == "imessage":
        if m.get("handle_id"):
            lines.append(f"Sender: {m['handle_id'][:100]}")

    return "\n".join(lines)


_CALENDAR_CONTEXT_MAX_CHARS = 1500


def _build_prompt(msg: RawMessage, calendar_context: str = "") -> str:
    """Construct the full prompt string for this message."""
    intro_key = _SOURCE_TO_INTRO.get(msg.source, "default")
    intro = _SOURCE_INTROS[intro_key]
    context_block = _build_context_block(msg)

    today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    schema = (
        _SCHEMA
        .replace(_TIMEZONE_PLACEHOLDER, config.USER_TIMEZONE)
        .replace("<<TODAY>>", today_str)
    )

    parts = [intro]
    if context_block:
        parts.append(context_block)
    parts.append(schema)
    if calendar_context:
        cal_block = calendar_context[:_CALENDAR_CONTEXT_MAX_CHARS]
        parts.append(
            "\nYour calendar for the next few weeks (use this to detect updates, "
            "avoid proposing duplicates, and resolve relative references like "
            "'move the meeting'):\n" + cal_block
        )
    parts.append("")
    parts.append("Message:")
    parts.append(msg.body_text[:_BODY_MAX_CHARS])

    return "\n".join(parts)


# ── Validation ────────────────────────────────────────────────────────────────

_VALID_CATEGORIES = {"work", "personal", "social", "health", "travel", "other"}


def _validate_category(raw_category) -> str:
    cat = str(raw_category or "other").lower().strip()
    return cat if cat in _VALID_CATEGORIES else "other"


def _validate_event(
    raw: dict[str, Any],
    default_confirmation_status: str = "awaiting_me",
) -> CandidateEvent | None:
    """Validate and sanitize a single LLM-extracted event dict. Returns None if invalid.

    `default_confirmation_status` is used when the LLM omits the field (legacy
    responses or sources that don't pass thread context). Caller passes the
    appropriate default based on metadata.is_from_me for gmail sources.
    """
    try:
        title = str(raw.get("title", "")).strip()[:_TITLE_MAX_CHARS]
        if not title or _UNSAFE_TITLE_RE.search(title):
            return None

        # date_certainty: specific|approximate|unknown. Default to specific so
        # legacy LLM responses (without the field) keep the original behavior.
        date_certainty = str(raw.get("date_certainty") or "specific").lower().strip()
        if date_certainty not in {"specific", "approximate", "unknown"}:
            date_certainty = "specific"

        event_description_raw = raw.get("event_description")
        event_description = (
            str(event_description_raw).strip()[:500] if event_description_raw else None
        )

        start_str = raw.get("start")
        now = _utcnow()
        if date_certainty == "unknown" or not start_str:
            # No specific date — route to the fuzzy_event proposal flow.
            # Use a placeholder start_dt so downstream code that expects a
            # datetime still works; the dashboard renders these specially.
            if not event_description:
                # Without a description, we have nothing useful to show — drop.
                return None
            start_dt = now  # placeholder; caller treats date_certainty=unknown as fuzzy
            return CandidateEvent(
                title=title,
                start_dt=start_dt,
                end_dt=None,
                location=None,
                confidence=max(0.0, min(1.0, float(raw.get("confidence", 0.0)))),
                source="",
                source_id="",
                date_certainty="unknown",
                event_description=event_description,
                category=_validate_category(raw.get("category")),
            )

        start_dt = datetime.fromisoformat(str(start_str))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        if not (now <= start_dt <= now + _years(_FUTURE_YEARS)):
            return None

        end_dt = None
        end_str = raw.get("end")
        if end_str:
            try:
                end_dt = datetime.fromisoformat(str(end_str))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                if end_dt <= start_dt:
                    end_dt = None
            except (ValueError, TypeError):
                end_dt = None

        location = raw.get("location")
        if location:
            location = str(location)[:300].strip() or None

        confidence = float(raw.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        # Recurrence
        is_recurring = bool(raw.get("is_recurring", False))
        recurrence_hint = str(raw.get("recurrence_hint") or "").strip()[:200] or None

        # Update / cancel signals
        is_update = bool(raw.get("is_update", False))
        original_title_hint_raw = raw.get("original_title_hint")
        original_title_hint = (
            str(original_title_hint_raw).strip()[:200]
            if original_title_hint_raw
            else None
        )
        is_cancellation = bool(raw.get("is_cancellation", False))

        # Attendees
        raw_attendees = raw.get("attendees") or []
        attendees: list[dict] = []
        if isinstance(raw_attendees, list):
            for a in raw_attendees[:20]:
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name") or "").strip()[:100]
                email = str(a.get("email") or "").strip()[:200] or None
                if email and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
                    email = None
                if name or email:
                    attendees.append({"name": name, "email": email})

        # Category
        category = str(raw.get("category") or "other").lower().strip()
        if category not in _VALID_CATEGORIES:
            category = "other"

        # Confirmation status — caller-provided default kicks in when the LLM
        # omits the field (e.g. older models or sources that don't pass thread
        # context). Default is awaiting_me; gmail outbound flips it to
        # proposed_by_me via the caller.
        raw_status = raw.get("confirmation_status")
        if raw_status is None:
            confirmation_status = default_confirmation_status
        else:
            confirmation_status = str(raw_status).lower().strip()
        if confirmation_status not in {"confirmed", "awaiting_me", "proposed_by_me"}:
            confirmation_status = default_confirmation_status

        return CandidateEvent(
            title=title,
            start_dt=start_dt,
            end_dt=end_dt,
            location=location,
            confidence=confidence,
            source="",    # filled in by caller
            source_id="",
            is_update=is_update,
            original_title_hint=original_title_hint,
            is_cancellation=is_cancellation,
            is_recurring=is_recurring,
            recurrence_hint=recurrence_hint,
            suggested_attendees=attendees,
            category=category,
            date_certainty=date_certainty,
            event_description=event_description,
            confirmation_status=confirmation_status,
        )
    except (ValueError, TypeError, KeyError):
        return None


# ── Ollama call ───────────────────────────────────────────────────────────────

_PRE_CLASSIFIER_PROMPT = (
    "You are a triage assistant. Decide whether the message below contains a "
    "scheduled event (something happening at a specific time) OR an actionable "
    "todo (a task or commitment).\n\n"
    "Reply with ONLY a JSON object: "
    '{{"verdict": "yes" | "no" | "maybe", "reason": "short justification (<=120 chars)"}}\n'
    "- \"yes\" if you are confident there is at least one event or todo to extract\n"
    "- \"no\" if it is clearly nothing actionable (newsletter, autoreply, FYI, banter, etc.)\n"
    "- \"maybe\" if uncertain — when in doubt, prefer \"maybe\" over \"no\"\n\n"
    "Message source: {source}\n"
    "Message body:\n{body}\n"
)


def pre_classify(msg: RawMessage) -> tuple[str, str]:
    """
    Cheap pre-classification call. Returns (verdict, reason).
    verdict ∈ {"yes", "no", "maybe"}. On any error, returns ("maybe", "<error>")
    so we never silently drop a real event due to a transient classifier glitch.
    """
    if not config.PRE_CLASSIFIER_ENABLED:
        return "maybe", "pre-classifier disabled"
    body = msg.body_text[:1500]  # tight context for triage
    prompt = _PRE_CLASSIFIER_PROMPT.format(source=msg.source, body=body)
    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": config.OLLAMA_KEEP_ALIVE_TEXT,
        "think": False,
        "options": {"num_ctx": config.PRE_CLASSIFIER_NUM_CTX},
    }
    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        parsed = json.loads(text)
        verdict = str(parsed.get("verdict", "maybe")).lower().strip()
        if verdict not in {"yes", "no", "maybe"}:
            verdict = "maybe"
        reason = str(parsed.get("reason", ""))[:200]
        return verdict, reason
    except (requests.RequestException, json.JSONDecodeError, KeyError) as exc:
        # Fail-open: if the classifier glitches, fall through to full extraction
        # so we never drop a real event due to a transient error.
        logger.debug("pre-classify failed for %s/%s: %s", msg.source, msg.id, exc)
        return "maybe", f"classifier_error: {type(exc).__name__}"


def _call_ollama(prompt: str) -> dict[str, Any]:
    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": config.OLLAMA_KEEP_ALIVE_TEXT,
        "think": False,  # disable qwen3 chain-of-thought; safe no-op on other models
        "options": {"num_ctx": config.OLLAMA_NUM_CTX_TEXT},
    }
    resp = requests.post(
        f"{config.OLLAMA_BASE_URL}/api/generate",
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json().get("response", "")
    return json.loads(text)


_VALID_PRIORITIES = {"urgent", "high", "normal", "low"}
_DUE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_todo(raw: dict[str, Any]) -> CandidateTodo | None:
    """Validate and sanitize a single LLM-extracted todo dict. Returns None if invalid."""
    try:
        title = str(raw.get("title", "")).strip()[:_TITLE_MAX_CHARS]
        if not title or _UNSAFE_TITLE_RE.search(title):
            return None

        confidence = float(raw.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        context = raw.get("context")
        if context:
            context = str(context).strip()[:500] or None

        due_date = raw.get("due_date")
        if due_date:
            due_date = str(due_date).strip()
            if not _DUE_DATE_RE.match(due_date):
                due_date = None

        priority = str(raw.get("priority") or "normal").lower().strip()
        if priority not in _VALID_PRIORITIES:
            priority = "normal"

        return CandidateTodo(
            title=title,
            source="",      # filled in by caller
            source_id="",
            source_url=None,
            confidence=confidence,
            context=context,
            due_date=due_date,
            priority=priority,
        )
    except (ValueError, TypeError, KeyError):
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def extract(message: RawMessage, calendar_context: str = "") -> tuple[list[CandidateEvent], list[CandidateTodo]]:
    """
    Extract candidate events and todo items from a single RawMessage via Ollama.
    Returns (events, todos); both lists may be empty. Never raises.

    calendar_context: optional compact string of upcoming calendar events,
    injected into the prompt to help Ollama detect updates and avoid duplicates.

    Event confidence banding (per config.CONFIDENCE_BANDS):
    - below medium threshold → event dropped
    - medium to high         → event returned with confidence_band="medium"
    - at or above high       → event returned with confidence_band="high"

    Todo items below config.TODOIST_TODO_MIN_CONFIDENCE are dropped.
    """
    bands = config.CONFIDENCE_BANDS.get(message.source, config.CONFIDENCE_BANDS["default"])
    medium_threshold = bands["medium"]
    high_threshold = bands["high"]

    prompt = _build_prompt(message, calendar_context=calendar_context)

    raw_data: dict[str, Any] = {}
    for attempt in range(3):
        try:
            raw_data = _call_ollama(prompt)
            break
        except (requests.RequestException, json.JSONDecodeError, KeyError) as exc:
            if attempt < 2:
                delay = 2 ** attempt  # 1s, 2s
                logger.warning(
                    "extractor: attempt %d failed for source=%s id=%s: %s — retrying in %ds",
                    attempt + 1, message.source, message.id, type(exc).__name__, delay,
                )
                time.sleep(delay)
            else:
                logger.warning(
                    "extractor: skipping source=%s id=%s after 3 failures",
                    message.source, message.id,
                )
                return [], []

    # ── Extract events ────────────────────────────────────────────────────────
    # For gmail outbound mail, default to proposed_by_me when the LLM doesn't
    # specify; for everything else, awaiting_me.
    default_status = "awaiting_me"
    if message.source == "gmail" and message.metadata.get("is_from_me"):
        default_status = "proposed_by_me"

    candidates: list[CandidateEvent] = []
    for raw in raw_data.get("events", []):
        event = _validate_event(raw, default_confirmation_status=default_status)
        if event is None:
            continue

        if event.confidence < medium_threshold:
            logger.debug(
                "extractor: dropping low-confidence event %r (%.2f < %.2f) source=%s",
                event.title, event.confidence, medium_threshold, message.source,
            )
            continue

        event.confidence_band = "high" if event.confidence >= high_threshold else "medium"
        event.source = message.source
        event.source_id = message.id
        event.source_url = message.metadata.get("source_url")
        if message.source == "gmail":
            event.thread_id = message.metadata.get("thread_id") or None

        if message.source == "gmail":
            _merge_gmail_attendees(event, message.metadata)

        if message.source == "gcal" and not event.suggested_attendees:
            gcal_attendees = message.metadata.get("attendees", [])
            event.suggested_attendees = [
                {"name": "", "email": email} for email in gcal_attendees if email
            ]

        candidates.append(event)

    # ── Extract todos ─────────────────────────────────────────────────────────
    todos: list[CandidateTodo] = []
    for raw in raw_data.get("todos", []):
        todo = _validate_todo(raw)
        if todo is None:
            continue

        if todo.confidence < config.TODOIST_TODO_MIN_CONFIDENCE:
            logger.debug(
                "extractor: dropping low-confidence todo %r (%.2f < %.2f) source=%s",
                todo.title, todo.confidence, config.TODOIST_TODO_MIN_CONFIDENCE, message.source,
            )
            continue

        todo.source = message.source
        todo.source_id = message.id
        todo.source_url = message.metadata.get("source_url")
        todos.append(todo)

    logger.debug(
        "extractor: source=%s id=%s → %d event(s), %d todo(s)",
        message.source, message.id, len(candidates), len(todos),
    )
    return candidates, todos


def _merge_gmail_attendees(event: CandidateEvent, metadata: dict) -> None:
    """
    Supplement LLM-extracted attendees with To/CC addresses from email headers.
    Header-sourced emails are authoritative — deduplicate by email address.
    """
    existing_emails = {
        a["email"].lower() for a in event.suggested_attendees if a.get("email")
    }
    for header_key in ("to", "cc"):
        header_val = metadata.get(header_key, "")
        if not header_val:
            continue
        # Headers may be "Name <email@example.com>, Other <other@example.com>"
        for part in header_val.split(","):
            part = part.strip()
            # Extract email from "Name <email>" format
            match = re.search(r"<([^>]+)>", part)
            email = match.group(1).strip() if match else part.strip()
            if "@" not in email:
                continue
            name_match = re.match(r"^(.+?)\s*<", part)
            name = name_match.group(1).strip() if name_match else ""
            if email.lower() not in existing_emails:
                event.suggested_attendees.append({"name": name, "email": email})
                existing_emails.add(email.lower())


def check_ollama_available() -> bool:
    """Returns True if Ollama is reachable. Called at startup."""
    try:
        resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False
