"""Persist MyFitnessPal's rotated session cookie. Runs in the MFP venv: stdlib only.

MFP re-issues a new NextAuth session-token (Set-Cookie, host cookie on www.myfitnesspal.com) on
every /api/auth/session hit. mfp-mcp keeps that token only in its in-memory curl_cffi jar and never
writes it back to cookies.json, so every fresh process reloads the pre-rotation token. That token
carries a short-lived upstream access token with a rotating refresh token: the first holder to
refresh after it expires wins, and every older copy is rejected from then on (probed 2026-10-15,
journal-187: sessions died within hours while the cookie itself was valid for 30 days). Saving the
newest token after each run keeps the mini the sole, current holder.

The optional headless auto-refresh profile (mfp-mcp[autorefresh]) holds its own older copy of the
token and can only lose that race, so mfp_fetch.py / mfp_goal.py no longer call refresh_session():
after a transient 403 it would overwrite a good token with the profile's revoked one.
"""
from __future__ import annotations

SESSION_COOKIE = "__Secure-next-auth.session-token"
HOST = "www.myfitnesspal.com"


def newest_session_token(jar) -> str | None:
    """The rotated token arrives as a host cookie on www.; the one we loaded from cookies.json sits on
    .myfitnesspal.com. Prefer the host cookie, fall back to whatever is there."""
    fallback = None
    for ck in jar:
        if getattr(ck, "name", None) != SESSION_COOKIE or not getattr(ck, "value", None):
            continue
        if getattr(ck, "domain", None) == HOST:
            return ck.value
        fallback = fallback or ck.value
    return fallback


def persist_rotated(client, auth=None, expect_saved=None) -> str:
    """Write the newest session token to mfp-mcp's cookie file when it changed.

    `expect_saved`: when given, only write if the file's current token is one of these (tokens this
    process has already seen), so a late write never clobbers a token another process just saved.
    Returns "saved", "unchanged", "no token", "skipped: ..." or "error: ..." and never raises:
    losing the rotated token is bad, but failing the run over it would be worse.
    """
    try:
        if auth is None:
            from myfitnesspal_mcp import auth  # noqa
        token = newest_session_token(client.session.cookies.jar)
        if not token:
            return "no token"
        saved = dict(auth.load_cookies() or {})
        if saved.get(SESSION_COOKIE) == token:
            return "unchanged"
        if expect_saved is not None and saved.get(SESSION_COOKIE) not in expect_saved:
            return "skipped: cookie file changed by another process"
        saved[SESSION_COOKIE] = token
        auth.save_cookies(saved, username=auth.saved_username())
        return "saved"
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}: {str(e)[:120]}"
