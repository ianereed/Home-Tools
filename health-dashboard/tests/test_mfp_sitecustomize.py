"""The venv-wide hook: wraps curl_cffi Session.request once and persists the rotated token after responses."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

from collectors import mfp_session as S

HOOK = Path(__file__).resolve().parents[1] / "collectors" / "mfp_sitecustomize.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("mfp_sitecustomize_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # install() runs at import; harmless without curl_cffi
    return mod


def _session_cls():
    """A fresh class per test: install() marks the class it wraps."""
    class FakeSession:
        def __init__(self, jar):
            self.cookies = NS(jar=jar)
            self.calls = 0

        def request(self, method, url, **kw):
            self.calls += 1
            return f"resp:{method}:{url}"
    return FakeSession


def test_hook_wraps_once_and_persists_only_on_change(monkeypatch):
    hook = _load_hook()
    fake_requests = NS(Session=_session_cls())
    assert hook.install(fake_requests) and hook.install(fake_requests)   # idempotent
    persisted = []
    monkeypatch.setattr(hook._ms, "persist_rotated", lambda client, auth=None: persisted.append(
        S.newest_session_token(client.session.cookies.jar)) or "saved")

    jar = [NS(name=S.SESSION_COOKIE, domain=".myfitnesspal.com", value="t1")]
    s = fake_requests.Session(jar)
    assert s.request("GET", "u1") == "resp:GET:u1"
    assert s.request("GET", "u2") == "resp:GET:u2"          # same token -> no second persist
    jar.append(NS(name=S.SESSION_COOKIE, domain=S.HOST, value="t2"))
    s.request("GET", "u3")                                   # rotated -> persisted
    assert persisted == ["t1", "t2"] and s.calls == 3


def test_hook_never_breaks_a_request(monkeypatch):
    hook = _load_hook()
    fake_requests = NS(Session=_session_cls())
    hook.install(fake_requests)
    monkeypatch.setattr(hook._ms, "persist_rotated", lambda *a, **k: 1 / 0)
    s = fake_requests.Session([NS(name=S.SESSION_COOKIE, domain=S.HOST, value="t")])
    assert s.request("GET", "u") == "resp:GET:u"


def test_install_returns_false_outside_the_mfp_venv(monkeypatch):
    hook = _load_hook()
    import builtins
    real_import = builtins.__import__
    def no_curl(name, *a, **k):
        if name.startswith("curl_cffi"):
            raise ImportError(name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_curl)
    assert hook.install() is False
