"""Unit tests for meal_planner.vision.gemini_fallback.call_gemini_vision.

Covers:
- Successful extract (200 + valid schema)
- Schema-validation retry (first response missing fields, retry valid)
- Rate-limit retry (429 → 200)
- Non-retryable error (500)
- Transport error (RequestException)
- inline_data / mime_type wired into the request body
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from meal_planner.vision import gemini_fallback as gf


def _photo(tmp_path: Path, suffix: str = ".jpg") -> Path:
    p = tmp_path / f"recipe{suffix}"
    p.write_bytes(b"\xff\xd8\xff")
    return p


def _good_payload() -> dict:
    return {
        "title": "Brown Butter Cookies",
        "ingredients": [
            {"qty": "2", "unit": "cup", "name": "flour"},
            {"qty": "1", "unit": "cup", "name": "butter"},
        ],
        "tags": ["baking"],
    }


def _mock_gemini_200(payload: dict) -> MagicMock:
    body = {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(payload)}]}}
        ],
        "usageMetadata": {"candidatesTokenCount": 42},
    }
    m = MagicMock()
    m.status_code = 200
    m.text = json.dumps(body)
    m.json.return_value = body
    return m


def test_extract_ok(monkeypatch, tmp_path):
    photo = _photo(tmp_path)
    payload = _good_payload()

    def mock_post(*args, **kwargs):
        return _mock_gemini_200(payload)

    monkeypatch.setattr(gf.requests, "post", mock_post)
    parsed, md = gf.call_gemini_vision(photo, api_key="test-key")

    assert parsed == payload
    assert md["http_status"] == 200
    assert md["n_retries"] == 0
    assert md["eval_count"] == 42
    assert md["latency_s"] is not None


def test_schema_retry_recovers(monkeypatch, tmp_path):
    """First Gemini call returns ingredient missing 'name'; retry returns valid payload."""
    photo = _photo(tmp_path)
    bad = {
        "title": "Bad",
        "ingredients": [{"qty": "1", "unit": "cup"}],
        "tags": [],
    }
    good = _good_payload()
    calls = {"n": 0}

    def mock_post(*args, **kwargs):
        calls["n"] += 1
        return _mock_gemini_200(bad if calls["n"] == 1 else good)

    monkeypatch.setattr(gf.requests, "post", mock_post)
    parsed, md = gf.call_gemini_vision(photo, api_key="test-key")

    assert calls["n"] == 2
    assert md["n_retries"] == 1
    assert parsed == good
    assert md["retry_latency_s"] is not None


def test_schema_retry_still_invalid_returns_normalized(monkeypatch, tmp_path):
    """Both calls schema-invalid; we still surface the parsed dict with normalize applied."""
    photo = _photo(tmp_path)
    bad = {
        "title": "Bad",
        "ingredients": [{"qty": "1", "unit": "cup"}],
        "tags": [],
    }

    def mock_post(*args, **kwargs):
        return _mock_gemini_200(bad)

    monkeypatch.setattr(gf.requests, "post", mock_post)
    parsed, md = gf.call_gemini_vision(photo, api_key="test-key")

    assert md["n_retries"] == 1
    assert parsed is not None
    assert parsed["ingredients"][0].get("qty") == "1"


def test_rate_limit_retry(monkeypatch, tmp_path):
    """429 → 200: rate-limit retry inside _gemini_one_call. Sleep is patched out."""
    photo = _photo(tmp_path)
    payload = _good_payload()
    calls = {"n": 0}

    def mock_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            m = MagicMock()
            m.status_code = 429
            m.text = "rate limited"
            m.json.return_value = {
                "error": {
                    "details": [
                        {"@type": "type.googleapis.com/google.rpc.RetryInfo",
                         "retryDelay": "1s"}
                    ]
                }
            }
            return m
        return _mock_gemini_200(payload)

    sleeps: list[float] = []
    monkeypatch.setattr(gf.requests, "post", mock_post)
    monkeypatch.setattr(gf.time, "sleep", lambda s: sleeps.append(s))

    parsed, md = gf.call_gemini_vision(photo, api_key="test-key")
    assert calls["n"] == 2
    assert sleeps == [3]  # 1s parsed delay + 2s buffer
    assert parsed == payload
    assert md["http_status"] == 200


def test_non_retryable_error(monkeypatch, tmp_path):
    """500 surfaces as (None, metadata) with http_status set."""
    photo = _photo(tmp_path)

    def mock_post(*args, **kwargs):
        m = MagicMock()
        m.status_code = 500
        m.text = "internal error"
        return m

    monkeypatch.setattr(gf.requests, "post", mock_post)
    parsed, md = gf.call_gemini_vision(photo, api_key="test-key")
    assert parsed is None
    assert md["http_status"] == 500
    assert md["raw_response"].startswith("HTTP 500")
    assert md["n_retries"] == 0  # schema retry skipped on non-200


def test_transport_exception(monkeypatch, tmp_path):
    photo = _photo(tmp_path)

    def mock_post(*args, **kwargs):
        raise requests.ConnectionError("dns fail")

    monkeypatch.setattr(gf.requests, "post", mock_post)
    parsed, md = gf.call_gemini_vision(photo, api_key="test-key")
    assert parsed is None
    assert md["http_status"] is None
    assert "dns fail" in md["raw_response"]


def test_request_uses_inline_data_with_mime(monkeypatch, tmp_path):
    """Captures the JSON body to verify inline_data shape + mime_type."""
    photo = _photo(tmp_path, suffix=".png")
    captured: dict = {}

    def mock_post(*args, **kwargs):
        captured["json"] = kwargs.get("json")
        captured["params"] = kwargs.get("params")
        return _mock_gemini_200(_good_payload())

    monkeypatch.setattr(gf.requests, "post", mock_post)
    gf.call_gemini_vision(photo, api_key="abc123")

    parts = captured["json"]["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/png"
    assert "data" in parts[0]["inline_data"]
    assert isinstance(parts[1]["text"], str)
    assert captured["params"] == {"key": "abc123"}


def test_mime_detection():
    assert gf._mime_for(Path("foo.jpg")) == "image/jpeg"
    assert gf._mime_for(Path("foo.JPEG")) == "image/jpeg"
    assert gf._mime_for(Path("foo.png")) == "image/png"
    assert gf._mime_for(Path("foo.heic")) == "image/heic"
    assert gf._mime_for(Path("foo.bin")) == "image/jpeg"  # default
