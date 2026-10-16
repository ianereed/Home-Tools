"""MFP session-cookie persistence: pick the rotated token from the jar and write it back once."""
from types import SimpleNamespace as NS

import pytest

from collectors import mfp_fetch as F
from collectors import mfp_session as S

OLD, NEW = "old-token", "new-token"


def _ck(domain, value, name=S.SESSION_COOKIE):
    return NS(name=name, domain=domain, value=value)


def _jar(*cookies):
    return NS(session=NS(cookies=NS(jar=list(cookies))))


class FakeAuth:
    def __init__(self, saved=None, username="ian"):
        self.saved, self.username, self.writes = saved, username, []

    def load_cookies(self):
        return self.saved

    def saved_username(self):
        return self.username

    def save_cookies(self, cookies, username=None):
        self.writes.append((cookies, username))


def test_newest_prefers_host_cookie_over_loaded_domain_cookie():
    jar = [_ck(".myfitnesspal.com", OLD), _ck(S.HOST, NEW), _ck(S.HOST, "x", name="__cf_bm")]
    assert S.newest_session_token(jar) == NEW
    assert S.newest_session_token([_ck(".myfitnesspal.com", OLD)]) == OLD
    assert S.newest_session_token([_ck(S.HOST, "x", name="__cf_bm")]) is None


def test_persist_writes_rotated_token_and_keeps_username():
    auth = FakeAuth({S.SESSION_COOKIE: OLD, "other": "1"})
    assert S.persist_rotated(_jar(_ck(".myfitnesspal.com", OLD), _ck(S.HOST, NEW)), auth) == "saved"
    assert auth.writes == [({S.SESSION_COOKIE: NEW, "other": "1"}, "ian")]


def test_persist_is_a_noop_without_rotation():
    auth = FakeAuth({S.SESSION_COOKIE: OLD})
    assert S.persist_rotated(_jar(_ck(".myfitnesspal.com", OLD)), auth) == "unchanged"
    assert S.persist_rotated(_jar(), auth) == "no token"
    assert auth.writes == []


def test_persist_never_raises():
    class Boom(FakeAuth):
        def save_cookies(self, cookies, username=None):
            raise OSError("disk full")
    out = S.persist_rotated(_jar(_ck(S.HOST, NEW)), Boom({S.SESSION_COOKIE: OLD}))
    assert out.startswith("error: OSError")


def test_fetch_persists_session_after_a_successful_run(monkeypatch):
    client = NS(get_date=lambda d: NS(meals=[]), get_report=lambda **k: {})
    seen = []
    monkeypatch.setattr(S, "persist_rotated", lambda c, auth=None: seen.append(c) or "saved")
    rc, out = F._fetch(F.date(2026, 10, 15), F.date(2026, 10, 15), lambda: client, lambda e: False)
    assert rc == 0 and out["ok"] and out["session"] == "saved" and seen == [client]


def test_fetch_reports_auth_failure_without_a_client(monkeypatch):
    class NotConnectedError(RuntimeError):
        pass
    def get_client():
        raise NotConnectedError("expired")
    rc, out = F._fetch(F.date(2026, 10, 15), F.date(2026, 10, 15), get_client, lambda e: pytest.fail("unused"))
    assert rc == 2 and out["auth"] and out["session"] == "no client"


def test_persist_guard_skips_when_another_process_wrote_the_file():
    auth = FakeAuth({S.SESSION_COOKIE: "someone-elses"})
    jar = _jar(_ck(S.HOST, NEW))
    assert S.persist_rotated(jar, auth, expect_saved={OLD, NEW}).startswith("skipped")
    assert auth.writes == []
    auth = FakeAuth({S.SESSION_COOKIE: OLD})
    assert S.persist_rotated(jar, auth, expect_saved={OLD}) == "saved"
