"""Venv-wide hook for the MFP venv: persist MFP's rotated session cookie after every HTTP response.

Install (Homebrew's own sitecustomize.py shadows a venv one, so use a .pth file instead):

    SP=~/.fuel-planner/mfp-venv/lib/python3.12/site-packages
    ln -sf ~/Home-Tools/health-dashboard/collectors/mfp_sitecustomize.py $SP/mfp_persist_hook.py
    echo "import mfp_persist_hook" > $SP/mfp_persist.pth

site.py runs the .pth import in every interpreter of that venv, including under `python -I`
(isolated mode only skips the user site), so it covers every code path: mfp_fetch.py / mfp_goal.py,
`mfp-mcp auth --check`, the MCP server and ad-hoc scripts. Without it, any process that touches the
session can advance MFP's token lineage and leave cookies.json holding a copy that is rejected within
hours (journal-187, 2026-10-16: an `auth --check` plus one read-only script at 08:00 killed a session
that had survived the night's scheduled runs).

It wraps curl_cffi's Session.request: after each response, if the jar's newest session token differs
from the last one this session persisted, write it back through mfp_session.persist_rotated. It also
re-persists at interpreter exit: `mfp-mcp auth` validates the pasted token (which may already carry an
expired inner token, so that very request refreshes it and the hook saves the result) and then saves the
pasted token over it. The exit write only happens when the file still holds a token this process has
seen, so it never clobbers one written by another process.
"""
from __future__ import annotations

import atexit
import importlib.util
from pathlib import Path
from types import SimpleNamespace

MARK = "_mfp_persist_hook"
_ms = None
_state: dict = {"seen": set(), "last": None}   # every session token seen in a jar; the last Session used


def _load_mfp_session():
    """Load the sibling helper by path: this file is reached through a symlink from site-packages, and
    collectors/ must not go on sys.path for every process in the venv."""
    global _ms
    if _ms is None:
        p = Path(__file__).resolve().with_name("mfp_session.py")
        spec = importlib.util.spec_from_file_location("mfp_session_hook", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _ms = mod
    return _ms


def install(requests_mod=None) -> bool:
    """Wrap Session.request once. Returns False when curl_cffi isn't importable (not the MFP venv)."""
    if requests_mod is None:
        try:
            from curl_cffi import requests as requests_mod  # noqa
        except Exception:
            return False
    session_cls = requests_mod.Session
    if getattr(session_cls, MARK, False):
        return True
    ms = _load_mfp_session()
    orig = session_cls.request
    last_seen: dict[int, str] = {}

    def request(self, *args, **kwargs):
        resp = orig(self, *args, **kwargs)
        try:
            jar = self.cookies.jar
            _state["seen"].update(ck.value for ck in jar if getattr(ck, "name", None) == ms.SESSION_COOKIE)
            _state["last"] = self
            tok = ms.newest_session_token(jar)
            if tok and last_seen.get(id(self)) != tok:
                last_seen[id(self)] = tok
                ms.persist_rotated(SimpleNamespace(session=self))
        except Exception:  # noqa: BLE001 -- never break a request over bookkeeping
            pass
        return resp

    session_cls.request = request
    setattr(session_cls, MARK, True)
    atexit.register(persist_at_exit)
    return True


def persist_at_exit() -> str:
    """Last word at interpreter exit: write the newest token seen, unless another process wrote since."""
    try:
        session = _state["last"]
        if session is None:
            return "no session"
        return _load_mfp_session().persist_rotated(SimpleNamespace(session=session), expect_saved=_state["seen"])
    except Exception as e:  # noqa: BLE001
        return f"error: {type(e).__name__}"


try:
    install()
except Exception:  # noqa: BLE001 -- a broken hook must never break the interpreter
    pass
