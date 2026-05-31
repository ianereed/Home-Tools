"""
HTTP enqueue server smoke tests. We exercise JobsHandler directly via
BaseHTTPRequestHandler's `handle_one_request` machinery without binding
a socket — fast + no port conflicts.
"""
from __future__ import annotations

import io
import json
import os
from unittest.mock import MagicMock

import pytest

from jobs import enqueue_http


class FakeRequest:
    """Stand-in for the socket pair BaseHTTPRequestHandler expects."""

    def __init__(self, method: str, path: str, headers: dict, body: bytes = b""):
        request_line = f"{method} {path} HTTP/1.1\r\n"
        header_lines = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        raw = (request_line + header_lines + "\r\n").encode() + body
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()

    def makefile(self, mode, bufsize):
        return self.rfile if "r" in mode else self.wfile


def _request(method: str, path: str, body: dict | None = None, token: str | None = "secret"):
    headers = {"Host": "localhost", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    raw_body = json.dumps(body).encode() if body is not None else b""
    if raw_body:
        headers["Content-Length"] = str(len(raw_body))
    req = FakeRequest(method, path, headers, raw_body)
    handler = enqueue_http.JobsHandler.__new__(enqueue_http.JobsHandler)
    handler.rfile = req.rfile
    handler.wfile = req.wfile
    handler.client_address = ("127.0.0.1", 0)
    handler.request = MagicMock()
    handler.server = MagicMock()
    handler.requestline = ""
    handler.command = ""
    handler.path = ""
    handler.handle_one_request()
    raw = handler.wfile.getvalue()
    head, _, body_bytes = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode()
    status = int(status_line.split()[1])
    payload = json.loads(body_bytes) if body_bytes else None
    return status, payload


def test_healthz_no_auth(monkeypatch):
    monkeypatch.delenv("HOME_TOOLS_HTTP_TOKEN", raising=False)
    status, body = _request("GET", "/healthz", token=None)
    assert status == 200
    assert body == {"ok": True}


def test_missing_token_rejected(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("GET", "/kinds", token=None)
    assert status == 401
    assert "missing bearer token" in body["error"]


def test_bad_token_rejected(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("GET", "/kinds", token="wrong")
    assert status == 401
    assert "bad token" in body["error"]


def test_kinds_listing(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("GET", "/kinds", token="secret")
    assert status == 200
    names = [k["name"] for k in body["kinds"]]
    assert "nop" in names
    assert "migration_verifier" in names


def test_kinds_listing_includes_lane_field(monkeypatch):
    """Each kind reports its lane: fast (huey_fast) or default (huey)."""
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("GET", "/kinds", token="secret")
    assert status == 200
    by_name = {k["name"]: k for k in body["kinds"]}
    # Phase 22: 4 kinds on the fast lane.
    fast_kinds = {
        "meal_planner_send_to_todoist",
        "meal_planner_clear_todoist",
        "meal_planner_iphone_intake",
        "event_aggregator_decide",
    }
    for name in fast_kinds:
        assert name in by_name, f"missing kind: {name}"
        assert by_name[name]["lane"] == "fast", f"{name} should be on fast lane"
    # Spot-check a few default-lane kinds.
    assert by_name["nop"]["lane"] == "default"
    assert by_name["migration_verifier"]["lane"] == "default"


def test_post_jobs_unknown_kind(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("POST", "/jobs", {"kind": "nonexistent"}, token="secret")
    assert status == 404
    assert "unknown kind" in body["error"]
    assert "available" in body


def test_post_jobs_missing_kind(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("POST", "/jobs", {"params": {}}, token="secret")
    assert status == 400
    assert "missing 'kind'" in body["error"]


def test_post_jobs_bad_params_type(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("POST", "/jobs", {"kind": "nop", "params": "not_an_object"}, token="secret")
    assert status == 400
    assert "params" in body["error"]


def test_post_jobs_nop_succeeds(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("POST", "/jobs", {"kind": "nop", "params": {"echo": {"hi": 1}}}, token="secret")
    assert status == 202
    assert body["kind"] == "nop"


def test_unknown_path(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("GET", "/notathing", token="secret")
    assert status == 404


def test_queue_size(monkeypatch):
    """/queue-size reports both lanes (size = default huey, size_fast = huey_fast)."""
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("GET", "/queue-size", token="secret")
    assert status == 200
    assert isinstance(body["size"], int)
    assert isinstance(body["size_fast"], int)


def test_jobs_id_pending(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    # A random UUID will never exist in huey → pending (result=None).
    status, body = _request("GET", "/jobs/00000000-0000-0000-0000-000000000000", token="secret")
    assert status == 200
    assert body["status"] == "pending"
    assert body["result"] is None
    assert body["error"] is None


def test_jobs_id_success(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    from unittest.mock import patch

    known_result = {"items_sent": 3, "items_attempted": 3}

    def _return_result(_id, blocking=False, preserve=False):
        return known_result

    with patch("jobs.huey.result", side_effect=_return_result):
        status, body = _request("GET", "/jobs/some-id", token="secret")
    assert status == 200
    assert body["status"] == "success"
    assert body["result"] == known_result
    assert body["error"] is None


def test_jobs_id_missing_id(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("GET", "/jobs/", token="secret")
    assert status == 404
    assert "missing job id" in body["error"]


def test_jobs_id_error(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    from unittest.mock import patch
    from huey.exceptions import TaskException

    def _raise(_id, blocking=False, preserve=False):
        # Huey always raises TaskException with a dict metadata (built by
        # Huey.build_error_result); str(exc) calls metadata.get('error').
        raise TaskException({"error": "IndexError: list index out of range", "retries": 0, "traceback": "tb"})

    with patch("jobs.huey.result", side_effect=_raise):
        status, body = _request("GET", "/jobs/some-id", token="secret")
    assert status == 200
    assert body["status"] == "error"
    assert "IndexError" in body["error"]
    assert body["result"] is None


def test_jobs_id_finds_result_on_fast_lane(monkeypatch):
    """When the default lane returns None, /jobs/<id> falls through to huey_fast."""
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    from unittest.mock import patch

    fast_result = {"items_sent": 7, "items_attempted": 7}

    def _default_none(_id, blocking=False, preserve=False):
        return None  # not found on default lane

    def _fast_hit(_id, blocking=False, preserve=False):
        return fast_result

    with patch("jobs.huey.result", side_effect=_default_none), \
         patch("jobs.huey_fast.result", side_effect=_fast_hit):
        status, body = _request("GET", "/jobs/some-id", token="secret")
    assert status == 200
    assert body["status"] == "success"
    assert body["result"] == fast_result
    assert body["error"] is None


def test_jobs_id_pending_when_neither_lane_has_result(monkeypatch):
    """Both lanes return None → status=pending (caller polls again)."""
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    from unittest.mock import patch

    def _none(_id, blocking=False, preserve=False):
        return None

    with patch("jobs.huey.result", side_effect=_none), \
         patch("jobs.huey_fast.result", side_effect=_none):
        status, body = _request("GET", "/jobs/some-id", token="secret")
    assert status == 200
    assert body["status"] == "pending"
    assert body["result"] is None

