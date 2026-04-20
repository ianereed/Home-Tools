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
OLLAMA_MODEL: str = _get("OLLAMA_MODEL", "qwen2.5:7b")
LOCAL_VISION_MODEL: str = _get("LOCAL_VISION_MODEL", "qwen2.5vl:7b")
GEMINI_FALLBACK_MODELS: str = _get(
    "GEMINI_FALLBACK_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash"
)

# ── Idle gating (skip heavy phases when user is active) ────────────────────
IDLE_MIN_SECONDS: int = int(_get("IDLE_MIN_SECONDS", "300"))  # 5 minutes

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
GCAL_TARGET_CALENDAR_ID: str = _get("GCAL_TARGET_CALENDAR_ID", "primary")

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

# ── Gemini (image/PDF analysis, optional) ────────────────────────────────────
GEMINI_API_KEY: str = _get("GEMINI_API_KEY")
GEMINI_MODEL: str = _get("GEMINI_MODEL", "gemini-2.5-pro")

# ── NAS / staging ────────────────────────────────────────────────────────────
NAS_ROOT: str = _get("NAS_ROOT", "/Volumes/Share1")
LOCAL_STAGING_DIR: str = _get(
    "LOCAL_STAGING_DIR", "~/Documents/event-aggregator-intake"
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
