"""Gemini Vision recipe extraction.

Two consumers:

1. Phase 16 Chunk 3 "Use Gemini" decision card — fallback when the local
   llama3.2-vision:11b call times out on a NAS-intake photo.
2. Phase 21 iPhone intake — primary extractor for the iPhone-Shortcut path
   (Gemini is faster + more accurate on single photos than the local model).

Return shape mirrors meal_planner.vision._ollama.call_ollama_vision so the
two extractors are drop-in interchangeable.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import requests

from meal_planner.vision._normalize import normalize_extraction
from meal_planner.vision._ollama import load_prompt, validate_schema

_GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{_GEMINI_MODEL}:generateContent"
)

_GEMINI_HTTP_TIMEOUT_S = 60
_MAX_RATE_LIMIT_RETRIES = 4


def _mime_for(photo_path: Path) -> str:
    suffix = photo_path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".heic":
        return "image/heic"
    return "image/jpeg"


def _gemini_one_call(
    image_b64: str,
    mime_type: str,
    prompt: str,
    api_key: str,
    timeout_s: int = _GEMINI_HTTP_TIMEOUT_S,
) -> tuple[dict | None, dict, str]:
    """Single Gemini call with 429/503 retry. Returns (parsed_dict_or_None, metadata, raw_text)."""
    md: dict = {
        "latency_s": None,
        "http_status": None,
        "raw_response": None,
        "eval_count": None,
    }
    body = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": image_b64}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    t0 = time.monotonic()
    resp: requests.Response | None = None

    for attempt in range(_MAX_RATE_LIMIT_RETRIES):
        try:
            resp = requests.post(
                _GEMINI_ENDPOINT,
                params={"key": api_key},
                json=body,
                timeout=timeout_s,
            )
        except requests.RequestException as exc:
            md["latency_s"] = round(time.monotonic() - t0, 3)
            md["raw_response"] = str(exc)
            return None, md, str(exc)

        if resp.status_code not in (429, 503):
            break

        retry_delay = 60
        try:
            for detail in resp.json().get("error", {}).get("details", []):
                if detail.get("@type", "").endswith("RetryInfo"):
                    delay_str = detail.get("retryDelay", "60s")
                    retry_delay = int(re.sub(r"[^0-9]", "", delay_str) or "60") + 2
                    break
        except ValueError:
            pass
        time.sleep(retry_delay)

    md["latency_s"] = round(time.monotonic() - t0, 3)
    if resp is None:
        return None, md, ""

    md["http_status"] = resp.status_code

    if resp.status_code != 200:
        md["raw_response"] = f"HTTP {resp.status_code}: {resp.text[:1000]}"
        return None, md, md["raw_response"]

    raw_text = resp.text
    md["raw_response"] = raw_text

    try:
        resp_json = resp.json()
    except ValueError:
        return None, md, raw_text

    try:
        candidate_text = resp_json["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return None, md, raw_text

    md["eval_count"] = (
        resp_json.get("usageMetadata", {}).get("candidatesTokenCount")
    )

    try:
        parsed = json.loads(candidate_text)
    except (json.JSONDecodeError, ValueError):
        return None, md, candidate_text

    if not isinstance(parsed, dict):
        return None, md, candidate_text

    return parsed, md, candidate_text


def call_gemini_vision(
    photo_path: Path, *, api_key: str, timeout_s: int = _GEMINI_HTTP_TIMEOUT_S
) -> tuple[dict | None, dict]:
    """Call Gemini 2.5 Flash with a single photo. Returns (parsed_or_None, metadata).

    Same return shape as meal_planner.vision._ollama.call_ollama_vision so callers
    (card resolver, iPhone-intake worker) can branch uniformly on result.

    Retries once on schema-validation failure, feeding the malformed response back
    to the model with an explicit error list — same recovery pattern _ollama uses.
    Rate-limit retries (429/503) happen inside _gemini_one_call.
    """
    with photo_path.open("rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")
    mime_type = _mime_for(photo_path)
    prompt = load_prompt()

    metadata: dict = {
        "latency_s": None,
        "http_status": None,
        "eval_count": None,
        "raw_response": None,
        "n_retries": 0,
        "retry_latency_s": None,
    }

    parsed, md1, raw1 = _gemini_one_call(image_b64, mime_type, prompt, api_key, timeout_s)
    metadata["latency_s"] = md1["latency_s"]
    metadata["http_status"] = md1["http_status"]
    metadata["raw_response"] = md1["raw_response"]
    metadata["eval_count"] = md1["eval_count"]

    if md1.get("http_status") != 200:
        return None, metadata

    is_valid, schema_errors = validate_schema(parsed)
    if is_valid:
        parsed_normalized, norm_warnings = normalize_extraction(parsed)
        if norm_warnings:
            metadata["normalize_warnings"] = norm_warnings
        return parsed_normalized, metadata

    err_summary = ", ".join(schema_errors) if schema_errors else "could not parse as JSON"
    truncated_raw = (raw1 or "")[:1500]
    retry_prompt = (
        f"{prompt}\n\n"
        f"---\n"
        f"Your previous response failed schema validation: {err_summary}.\n"
        f"Previous response was:\n{truncated_raw}\n\n"
        f"Return ONLY valid JSON matching the schema above. "
        f"Every ingredient must have qty, unit, AND name keys."
    )
    parsed2, md2, _raw2 = _gemini_one_call(image_b64, mime_type, retry_prompt, api_key, timeout_s)
    metadata["n_retries"] = 1
    metadata["retry_latency_s"] = md2.get("latency_s")
    if parsed2 is None:
        return None, metadata

    metadata["raw_response"] = md2.get("raw_response")
    metadata["eval_count"] = md2.get("eval_count")
    parsed2_normalized, norm_warnings2 = normalize_extraction(parsed2)
    if norm_warnings2:
        metadata["normalize_warnings"] = norm_warnings2
    return parsed2_normalized, metadata
