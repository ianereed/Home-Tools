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
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    status, body = _request("GET", "/queue-size", token="secret")
    assert status == 200
    assert isinstance(body["size"], int)


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


# ---------------------------------------------------------------------------
# Phase 21 — POST /iphone-intake (multipart)
# ---------------------------------------------------------------------------

def _multipart(boundary: str, fields: list[tuple[str, dict]]) -> bytes:
    """Build a multipart/form-data body.

    fields: list of (name, {"value": bytes, "filename": str|None, "content_type": str|None}).
    """
    out: list[bytes] = []
    b = boundary.encode()
    for name, spec in fields:
        out.append(b"--" + b + b"\r\n")
        disp = f'Content-Disposition: form-data; name="{name}"'
        if spec.get("filename"):
            disp += f'; filename="{spec["filename"]}"'
        out.append(disp.encode() + b"\r\n")
        if spec.get("content_type"):
            out.append(f'Content-Type: {spec["content_type"]}'.encode() + b"\r\n")
        out.append(b"\r\n")
        out.append(spec["value"])
        out.append(b"\r\n")
    out.append(b"--" + b + b"--\r\n")
    return b"".join(out)


def _iphone_request(boundary: str, body: bytes, token: str | None = "secret"):
    headers = {
        "Host": "localhost",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = FakeRequest("POST", "/iphone-intake", headers, body)
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


@pytest.fixture
def _iphone_wired(monkeypatch, tmp_path):
    """Wire env + DB + mock the enqueue so the test stays in-process."""
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    intake_dir = tmp_path / "iphone-intake"
    monkeypatch.setenv("MEAL_PLANNER_IPHONE_INTAKE_DIR", str(intake_dir))

    db_p = tmp_path / "recipes.db"
    from meal_planner.db import _SCHEMA, _add_column_if_missing, _get_conn
    with _get_conn(db_p) as c:
        c.executescript(_SCHEMA)
        _add_column_if_missing(c, "photos_intake", "source", "TEXT")

    import meal_planner.db as _db
    import meal_planner.vision.intake_db as idb
    monkeypatch.setattr(_db, "DB_PATH", db_p)
    monkeypatch.setattr(idb, "DB_PATH", db_p)

    import jobs.kinds.meal_planner_iphone_intake as iphone_mod
    calls: list = []

    class _FakeTaskResult:
        id = "task-abc-123"

    def _fake_enqueue(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeTaskResult()

    monkeypatch.setattr(iphone_mod, "meal_planner_iphone_intake", _fake_enqueue)
    return {"intake_dir": intake_dir, "db_p": db_p, "calls": calls}


def test_iphone_intake_happy_path(_iphone_wired):
    boundary = "----WebKitFormBoundaryABC123"
    body = _multipart(boundary, [
        ("photo", {"value": b"\xff\xd8\xff\xe0fake-jpeg-bytes",
                   "filename": "IMG_0001.jpg",
                   "content_type": "image/jpeg"}),
        ("intent", {"value": b"save_and_shop"}),
        ("servings", {"value": b"6"}),
    ])
    status, payload = _iphone_request(boundary, body)
    assert status == 202
    assert payload["status"] == "enqueued"
    assert payload["id"] == "task-abc-123"
    assert len(payload["sha"]) == 16

    # Enqueue was called with the expected args
    assert len(_iphone_wired["calls"]) == 1
    args, _ = _iphone_wired["calls"][0]
    sha_arg, intent_arg, servings_arg = args
    assert sha_arg == payload["sha"]
    assert intent_arg == "save_and_shop"
    assert servings_arg == 6

    # Photo was written
    photo_path = _iphone_wired["intake_dir"] / "_processing" / f"{payload['sha']}.jpg"
    assert photo_path.exists()
    assert photo_path.read_bytes().startswith(b"\xff\xd8\xff")


def test_iphone_intake_missing_token(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    boundary = "----X"
    body = _multipart(boundary, [
        ("photo", {"value": b"\xff\xd8", "filename": "x.jpg", "content_type": "image/jpeg"}),
        ("intent", {"value": b"save"}),
    ])
    status, payload = _iphone_request(boundary, body, token=None)
    assert status == 401


def test_iphone_intake_bad_intent(_iphone_wired):
    boundary = "----Y"
    body = _multipart(boundary, [
        ("photo", {"value": b"\xff\xd8", "filename": "x.jpg", "content_type": "image/jpeg"}),
        ("intent", {"value": b"delete-everything"}),
    ])
    status, payload = _iphone_request(boundary, body)
    assert status == 400
    assert "bad intent" in payload["error"]
    assert _iphone_wired["calls"] == []  # no enqueue


def test_iphone_intake_missing_photo(_iphone_wired):
    boundary = "----Z"
    body = _multipart(boundary, [
        ("intent", {"value": b"save"}),
    ])
    status, payload = _iphone_request(boundary, body)
    assert status == 400
    assert "photo" in payload["error"]


def test_iphone_intake_missing_intent(_iphone_wired):
    boundary = "----W"
    body = _multipart(boundary, [
        ("photo", {"value": b"\xff\xd8", "filename": "x.jpg", "content_type": "image/jpeg"}),
    ])
    status, payload = _iphone_request(boundary, body)
    assert status == 400
    assert "intent" in payload["error"]


def test_iphone_intake_duplicate_sha(_iphone_wired):
    boundary = "----D"
    photo_bytes = b"\xff\xd8\xff\xe0duplicate"
    body = _multipart(boundary, [
        ("photo", {"value": photo_bytes, "filename": "x.jpg", "content_type": "image/jpeg"}),
        ("intent", {"value": b"save"}),
    ])
    status1, payload1 = _iphone_request(boundary, body)
    assert status1 == 202

    # Second POST of the same bytes — same sha, dedup short-circuits enqueue.
    body2 = _multipart(boundary, [
        ("photo", {"value": photo_bytes, "filename": "x.jpg", "content_type": "image/jpeg"}),
        ("intent", {"value": b"save"}),
    ])
    status2, payload2 = _iphone_request(boundary, body2)
    assert status2 == 200
    assert payload2["status"] == "duplicate"
    assert payload2["sha"] == payload1["sha"]
    assert len(_iphone_wired["calls"]) == 1  # only the first enqueued


def test_iphone_intake_bad_content_type(monkeypatch):
    monkeypatch.setenv("HOME_TOOLS_HTTP_TOKEN", "secret")
    # POST with application/json instead of multipart
    status, payload = _request("POST", "/iphone-intake", {"intent": "save"}, token="secret")
    assert status == 400
    assert "multipart" in payload["error"]


def test_iphone_intake_bad_servings(_iphone_wired):
    boundary = "----S"
    body = _multipart(boundary, [
        ("photo", {"value": b"\xff\xd8\xff\xe0bytes", "filename": "x.jpg", "content_type": "image/jpeg"}),
        ("intent", {"value": b"save"}),
        ("servings", {"value": b"-1"}),
    ])
    status, payload = _iphone_request(boundary, body)
    assert status == 400
    assert "servings" in payload["error"]


def test_iphone_intake_defaults_servings_to_4(_iphone_wired):
    boundary = "----S2"
    body = _multipart(boundary, [
        ("photo", {"value": b"\xff\xd8\xff\xe0bytes-2", "filename": "x.jpg", "content_type": "image/jpeg"}),
        ("intent", {"value": b"save"}),
    ])
    status, payload = _iphone_request(boundary, body)
    assert status == 202
    args, _ = _iphone_wired["calls"][0]
    assert args[2] == 4  # servings default


# ---------------------------------------------------------------------------
# multipart parser unit tests
# ---------------------------------------------------------------------------

def test_parse_multipart_text_and_file():
    boundary = "ABC"
    body = (
        b"--ABC\r\n"
        b'Content-Disposition: form-data; name="intent"\r\n'
        b"\r\n"
        b"save\r\n"
        b"--ABC\r\n"
        b'Content-Disposition: form-data; name="photo"; filename="img.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n"
        b"\r\n"
        b"\xff\xd8\xff\xe0\r\n"
        b"--ABC--\r\n"
    )
    parts = enqueue_http._parse_multipart(body, b"ABC")
    assert parts["intent"]["value"] == b"save"
    assert parts["photo"]["value"] == b"\xff\xd8\xff\xe0"
    assert parts["photo"]["filename"] == "img.jpg"


def test_parse_multipart_no_boundary_returns_empty():
    parts = enqueue_http._parse_multipart(b"no boundaries here", b"X")
    assert parts == {}
