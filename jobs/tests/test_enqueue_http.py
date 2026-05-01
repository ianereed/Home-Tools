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
