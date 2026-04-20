"""
Shared Google OAuth2 credential helper.

Token storage priority:
  1. macOS Keychain via keyring (survives reboots, no plaintext on disk)
  2. JSON token file (fallback, always gitignored)

Both are kept in sync on every refresh or new authorization.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import keyring
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "home-tools-event-aggregator"
_HERE = Path(__file__).parent.parent  # event-aggregator/


def _resolve(path_str: str) -> Path:
    """Resolve a path relative to event-aggregator/ if not absolute."""
    p = Path(path_str).expanduser()
    return p if p.is_absolute() else _HERE / p


def get_credentials(
    scopes: list[str],
    token_path: str,
    credentials_path: str,
    keyring_key: str,
) -> Credentials:
    """
    Load or acquire OAuth2 credentials for the given scopes.

    Tries (in order):
      1. Keyring entry for keyring_key
      2. JSON token file at token_path
      3. Refreshes if expired
      4. Runs the OAuth2 browser flow if no valid token exists

    Args:
        scopes: OAuth2 scopes to request.
        token_path: Path to token JSON file (relative to event-aggregator/ or absolute).
        credentials_path: Path to client secrets JSON from Google Cloud Console.
        keyring_key: Key name in macOS Keychain (e.g. "gmail_token", "gcal_token").

    Returns:
        Valid Credentials object.

    Raises:
        FileNotFoundError: If credentials_path doesn't exist (setup not complete).
    """
    token_file = _resolve(token_path)
    creds_file = _resolve(credentials_path)
    creds: Credentials | None = None

    # 1. Try keyring
    stored = keyring.get_password(_KEYRING_SERVICE, keyring_key)
    if stored:
        try:
            creds = Credentials.from_authorized_user_info(json.loads(stored), scopes)
        except Exception as exc:
            logger.debug("keyring token invalid for %s, ignoring: %s", keyring_key, exc)
            creds = None

    # 2. Fall back to JSON token file
    if not creds and token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        except Exception as exc:
            logger.debug("token file invalid for %s, ignoring: %s", keyring_key, exc)
            creds = None

    # 3. Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        logger.debug("refreshing OAuth token for %s", keyring_key)
        try:
            creds.refresh(Request())
            _persist(creds, token_file, keyring_key)
            return creds
        except Exception as exc:
            # invalid_grant (revoked/expired refresh token) or invalid_scope —
            # fall through to the browser OAuth flow to get a fresh token.
            logger.debug("token refresh failed for %s (%s) — re-authorizing", keyring_key, exc)
            creds = None

    if creds and creds.valid:
        return creds

    # 4. Run the OAuth2 browser flow (first-time setup)
    if not creds_file.exists():
        raise FileNotFoundError(
            f"Google client secrets file not found: {creds_file}\n"
            "Download it from Google Cloud Console (OAuth2 client credentials)\n"
            "and place it at that path."
        )
    logger.info(
        "Running Google OAuth2 flow for %s — a browser window will open", keyring_key
    )
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), scopes)
    creds = flow.run_local_server(port=0)
    _persist(creds, token_file, keyring_key)
    return creds


def _persist(creds: Credentials, token_file: Path, keyring_key: str) -> None:
    """Save credentials to both keyring and JSON file."""
    token_json = creds.to_json()
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token_json)
        token_file.chmod(0o600)
    except OSError as exc:
        logger.warning("could not write token file %s: %s", token_file, exc)
    try:
        keyring.set_password(_KEYRING_SERVICE, keyring_key, token_json)
    except Exception as exc:
        logger.warning("could not save token to keyring: %s", exc)
