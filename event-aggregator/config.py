"""
Configuration loader. Reads from .env at project root.
Validates all required variables at startup and raises clearly if any are missing.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load from event-aggregator/.env (standalone); also check parent repo root as fallback
# so this works whether you run from event-aggregator/ or from the Home-Tools root.
_here = Path(__file__).parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")  # no-op if vars already set


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"See event-aggregator/.env.example for documentation."
        )
    return val


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ── Ollama ──────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL: str = _get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = _get("OLLAMA_MODEL", "qwen3:14b")
LOCAL_VISION_MODEL: str = _get("LOCAL_VISION_MODEL", "qwen2.5vl:7b")

# Context ceilings (tokens). 16k for both — see plan B.6.2 for memory math.
OLLAMA_NUM_CTX_TEXT: int = int(_get("OLLAMA_NUM_CTX_TEXT", "16384"))
OLLAMA_NUM_CTX_VISION: int = int(_get("OLLAMA_NUM_CTX_VISION", "16384"))

# keep_alive: "-1" keeps the primary model resident; vision unloads quickly
# after a swap-in finishes so the primary can come back hot.
OLLAMA_KEEP_ALIVE_TEXT: str = _get("OLLAMA_KEEP_ALIVE_TEXT", "-1")
OLLAMA_KEEP_ALIVE_VISION: str = _get("OLLAMA_KEEP_ALIVE_VISION", "30s")

# Pre-classifier: a cheap qwen3:14b call (small ctx, tight prompt) that
# decides yes/no/maybe before the full extraction. "no" → skip. Saves
# the 16k-ctx call on obvious non-event traffic. Defaults on; set to "0"
# to bypass and always run the full extraction.
PRE_CLASSIFIER_ENABLED: bool = _get("PRE_CLASSIFIER_ENABLED", "1") not in {"0", "false", "False"}
PRE_CLASSIFIER_NUM_CTX: int = int(_get("PRE_CLASSIFIER_NUM_CTX", "2048"))

# ── Google (Gmail + GCal) ───────────────────────────────────────────────────
GMAIL_CREDENTIALS_JSON: str = _get(
    "GMAIL_CREDENTIALS_JSON", "credentials/gmail_oauth.json"
)
GMAIL_TOKEN_JSON: str = _get(
    "GMAIL_TOKEN_JSON", "credentials/gmail_token.json"
)
GCAL_TOKEN_JSON: str = _get(
    "GCAL_TOKEN_JSON", "credentials/gcal_token.json"
)
# Primary calendar — read for context and dedup; written ONLY via approved
# merge proposals. Default "primary" resolves to the user's @gmail address.
GCAL_PRIMARY_CALENDAR_ID: str = _get("GCAL_PRIMARY_CALENDAR_ID", "primary")

# Weekend calendar — write target. Auto-creates events here, silent-patches
# additive merges here. The legacy `GCAL_TARGET_CALENDAR_ID` env var is
# accepted as a fallback so existing .env files keep working.
GCAL_WEEKEND_CALENDAR_ID: str = _get(
    "GCAL_WEEKEND_CALENDAR_ID",
    _get("GCAL_TARGET_CALENDAR_ID", "primary"),
)
# Backwards-compat shim: many call sites still reference GCAL_TARGET_CALENDAR_ID.
# Wire it to the weekend calendar so behavior is preserved until the rename
# fully propagates.
GCAL_TARGET_CALENDAR_ID: str = GCAL_WEEKEND_CALENDAR_ID

# ── Slack ───────────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN: str = _get("SLACK_BOT_TOKEN")
SLACK_MONITOR_CHANNELS: list[str] = [
    c.strip() for c in _get("SLACK_MONITOR_CHANNELS").split(",") if c.strip()
]
SLACK_NOTIFY_CHANNEL: str = _get("SLACK_NOTIFY_CHANNEL", "ian-event-aggregator")

# ── Discord ─────────────────────────────────────────────────────────────────
DISCORD_BOT_TOKEN: str = _get("DISCORD_BOT_TOKEN")
DISCORD_MONITOR_CHANNELS: list[str] = [
    c.strip() for c in _get("DISCORD_MONITOR_CHANNELS").split(",") if c.strip()
]

# ── Local DB paths (Mac) ────────────────────────────────────────────────────
IMESSAGE_DB_PATH: str = _get(
    "IMESSAGE_DB_PATH", "~/Library/Messages/chat.db"
)
WHATSAPP_DB_PATH: str = _get(
    "WHATSAPP_DB_PATH",
    "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite",
)

# ── Digest schedule ─────────────────────────────────────────────────────────
DIGEST_DAILY_HOUR: int = int(_get("DIGEST_DAILY_HOUR", "7"))
DIGEST_WEEKLY_DOW: int = int(_get("DIGEST_WEEKLY_DOW", "0"))  # 0 = Monday

# ── Timezone ─────────────────────────────────────────────────────────────────
USER_TIMEZONE: str = _get("USER_TIMEZONE", "America/Los_Angeles")

# ── Confidence bands ─────────────────────────────────────────────────────────
# medium = minimum confidence to create any event (with [?] prefix)
# high   = minimum confidence to create event without prefix
CONFIDENCE_BANDS: dict[str, dict[str, float]] = {
    "gmail":    {"medium": 0.50, "high": 0.75},
    "gcal":     {"medium": 0.50, "high": 0.75},
    "slack":    {"medium": 0.60, "high": 0.80},
    "imessage": {"medium": 0.65, "high": 0.82},
    "whatsapp": {"medium": 0.65, "high": 0.82},
    "discord":  {"medium": 0.60, "high": 0.80},
    "default":  {"medium": 0.55, "high": 0.78},
}

# ── Todoist (optional) ───────────────────────────────────────────────────────
TODOIST_API_TOKEN: str = _get("TODOIST_API_TOKEN")
TODOIST_PROJECT_NAME: str = _get("TODOIST_PROJECT_NAME", "automated todo aggregation")
TODOIST_TODO_MIN_CONFIDENCE: float = float(_get("TODOIST_TODO_MIN_CONFIDENCE", "0.65"))

# ── NAS / staging ────────────────────────────────────────────────────────────
NAS_ROOT: str = _get("NAS_ROOT", "/Volumes/Share1")
LOCAL_STAGING_DIR: str = _get(
    "LOCAL_STAGING_DIR", str(Path(__file__).parent / "staging")
)
IMAGE_CONFIDENCE_MIN: float = float(_get("IMAGE_CONFIDENCE_MIN", "0.3"))

# ── Event approval mode ──────────────────────────────────────────────────────
# "propose" = post to Slack for approval before writing to GCal (default)
# "auto"    = write to GCal immediately (original behavior)
EVENT_APPROVAL_MODE: str = _get("EVENT_APPROVAL_MODE", "propose")
# Hours before an unacted proposal expires and is cleaned up
PROPOSAL_EXPIRY_HOURS: int = int(_get("PROPOSAL_EXPIRY_HOURS", "48"))
# Weeks of upcoming calendar events to inject into Ollama extraction prompt
CALENDAR_CONTEXT_WEEKS: int = int(_get("CALENDAR_CONTEXT_WEEKS", "4"))

# ── GCal category colors ─────────────────────────────────────────────────────
# Values are GCal colorId strings (1–11)
CATEGORY_COLORS: dict[str, str] = {
    "work":     "9",   # blueberry
    "personal": "10",  # basil
    "social":   "6",   # tangerine
    "health":   "4",   # flamingo
    "travel":   "5",   # banana
    "other":    "1",   # lavender
}


def validate_for_sources(sources: list[str]) -> None:
    """Raise EnvironmentError for any missing vars required by the given sources."""
    required_by_source: dict[str, list[tuple[str, str]]] = {
        "gmail": [("GMAIL_CREDENTIALS_JSON", GMAIL_CREDENTIALS_JSON)],
        "gcal": [("GCAL_TOKEN_JSON", GCAL_TOKEN_JSON)],
        "slack": [("SLACK_BOT_TOKEN", SLACK_BOT_TOKEN)],
        # discord: token is optional — connector self-skips with a warning if unset
    }
    missing = []
    for src in sources:
        for name, val in required_by_source.get(src, []):
            if not val:
                missing.append(name)
    if missing:
        raise EnvironmentError(
            f"Missing required env vars for selected sources: {', '.join(missing)}"
        )
