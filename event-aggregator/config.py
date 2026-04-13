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
OLLAMA_MODEL: str = _get("OLLAMA_MODEL", "llama3.2")

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
SLACK_DIGEST_USER_ID: str = _get("SLACK_DIGEST_USER_ID")  # your Slack user ID for DMs
SLACK_EVENT_LOG_CHANNEL: str = _get("SLACK_EVENT_LOG_CHANNEL", "#event-log")

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


def validate_for_sources(sources: list[str]) -> None:
    """Raise EnvironmentError for any missing vars required by the given sources."""
    required_by_source: dict[str, list[tuple[str, str]]] = {
        "gmail": [("GMAIL_CREDENTIALS_JSON", GMAIL_CREDENTIALS_JSON)],
        "gcal": [("GCAL_TOKEN_JSON", GCAL_TOKEN_JSON)],
        "slack": [("SLACK_BOT_TOKEN", SLACK_BOT_TOKEN)],
        "discord": [("DISCORD_BOT_TOKEN", DISCORD_BOT_TOKEN)],
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
