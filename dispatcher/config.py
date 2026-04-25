"""
Configuration for the dispatcher bot.

Tokens are loaded from .env first; if a token is absent, fall through to the
login keychain (service="dispatcher-slack") so the mini can keep secrets out
of .env the same way finance-monitor does.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _keychain(account: str, service: str = "dispatcher-slack") -> str:
    """Read a password from the macOS login keychain via the `security` CLI.

    On the mini, the `security` CLI from non-aqua sessions can't reach the
    default keychain via the search list — the keychain path must be passed
    positionally. Fall through to the standard login keychain when
    KEYCHAIN_PATH isn't set so this works in both LaunchAgent and SSH-shell
    contexts.
    """
    keychain_path = os.environ.get("KEYCHAIN_PATH") or os.path.expanduser(
        "~/Library/Keychains/login.keychain-db"
    )
    cmd = ["security", "find-generic-password", "-s", service, "-a", account, "-w", keychain_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as exc:
        logger.debug("keychain lookup failed for %s/%s: %s", service, account, exc)
    return ""


SLACK_APP_TOKEN: str = _get("SLACK_APP_TOKEN") or _keychain("app_token")
SLACK_BOT_TOKEN: str = _get("SLACK_BOT_TOKEN") or _keychain("bot_token")

INTERACTIVE_CHANNEL: str = _get("INTERACTIVE_CHANNEL", "ian-event-aggregator")
IMAGE_INTAKE_CHANNEL: str = _get("IMAGE_INTAKE_CHANNEL", "ian-image-intake")

ALLOWED_SLACK_USER_IDS: frozenset[str] = frozenset(
    uid.strip()
    for uid in _get("ALLOWED_SLACK_USER_IDS", "").split(",")
    if uid.strip()
)

EVENT_AGGREGATOR_DIR: Path = Path(
    _get("EVENT_AGGREGATOR_DIR", str(BASE_DIR.parent / "event-aggregator"))
)
EVENT_AGGREGATOR_PYTHON: str = _get(
    "EVENT_AGGREGATOR_PYTHON", str(EVENT_AGGREGATOR_DIR / ".venv" / "bin" / "python3")
)
FINANCE_MONITOR_INTAKE: Path = Path(
    _get("FINANCE_MONITOR_INTAKE", str(BASE_DIR.parent / "finance-monitor" / "intake"))
)

NAS_STAGING_DIR: Path = Path(_get("NAS_STAGING_DIR") or str(BASE_DIR / "nas-staging"))

TMP_DIR: Path = BASE_DIR / "tmp"


def validate() -> list[str]:
    """Return a list of human-readable problems; empty list = ready to run."""
    problems: list[str] = []
    if not SLACK_APP_TOKEN:
        problems.append(
            "SLACK_APP_TOKEN not found in .env or keychain (service=dispatcher-slack, account=app_token)"
        )
    if not SLACK_BOT_TOKEN:
        problems.append(
            "SLACK_BOT_TOKEN not found in .env or keychain (service=dispatcher-slack, account=bot_token)"
        )
    if not EVENT_AGGREGATOR_DIR.exists():
        problems.append(f"EVENT_AGGREGATOR_DIR does not exist: {EVENT_AGGREGATOR_DIR}")
    return problems
