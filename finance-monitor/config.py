"""Load and validate configuration from environment / .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE_DIR = Path(__file__).parent


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Required env var {key!r} is not set")
    return val


OLLAMA_BASE_URL: str = _get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = _get("OLLAMA_MODEL", "qwen3:14b")

SLACK_APP_TOKEN: str = _get("SLACK_APP_TOKEN")   # xapp-... Socket Mode token
SLACK_BOT_TOKEN: str = _get("SLACK_BOT_TOKEN")   # xoxb-... Bot token

# Comma-separated Slack user IDs allowed to DM the bot. If empty, the bot
# accepts DMs from anyone in the workspace (logs a warning at startup).
ALLOWED_SLACK_USER_IDS: frozenset[str] = frozenset(
    uid.strip()
    for uid in _get("ALLOWED_SLACK_USER_IDS", "").split(",")
    if uid.strip()
)

DB_PATH: Path = BASE_DIR / "data" / "finance.db"
INTAKE_DIR: Path = BASE_DIR / "intake"
IMPORTED_DIR: Path = BASE_DIR / "imported"
