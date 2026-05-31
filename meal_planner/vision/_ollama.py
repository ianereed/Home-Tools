"""Ollama vision adapter — extracted from meal_planner/eval/bake_off.py for Phase 16.

Public API:
    call_ollama_vision(model, image_path, prompt, *, base_url, num_ctx, keep_alive, timeout_s)
        → (parsed_dict_or_None, metadata)
    unload_ollama(model, base_url) → None
    default_ctx_for(model, role="vision") → int
    validate_schema(d) → (is_valid, errors)
    load_prompt() → str

The eval/bake_off.py CLI re-imports these under the original `_`-prefixed names so
existing benchmarks + tests keep working unchanged.
"""
from __future__ import annotations

import base64
import json
import pathlib
import time

import requests

from meal_planner.vision._normalize import normalize_extraction

_PROMPT_PATH = pathlib.Path(__file__).parent / "recipe_extraction_prompt.txt"
_PROMPT_TEXT: str | None = None

# Default socket timeout for the underlying requests.post call. Callers can pass
# a smaller `timeout_s` to call_ollama_vision (Chunk 3 will use 500s for the
# NAS-intake worker; bench keeps the historical 600s).
_OLLAMA_HTTP_TIMEOUT_S = 600

NUM_CTX_TABLE: dict[tuple[str, str], int] = {
    ("minicpm-v:8b", "vision"): 4096,
    ("qwen2.5vl:3b", "vision"): 6144,
    ("qwen2.5vl:7b", "vision"): 4096,
    ("llama3.2-vision:11b", "vision"): 16384,
    ("qwen2.5:3b", "text"): 6144,
    ("qwen2.5:7b", "text"): 4096,
    ("llama3.1:8b", "text"): 4096,
}


def default_ctx_for(model: str, role: str = "vision") -> int:
    return NUM_CTX_TABLE.get((model, role), 4096)


def load_prompt() -> str:
    global _PROMPT_TEXT
    if _PROMPT_TEXT is None:
        _PROMPT_TEXT = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _PROMPT_TEXT


def validate_schema(d: dict | None) -> tuple[bool, list[str]]:
    """Check structural validity. Returns (is_valid, errors).

    `instructions` is optional (Phase 19): if absent, treated as None. If
    present, must be a string or None. Required-field would invalidate
    legacy fixtures and any model response that forgets the key.
    """
    errors: list[str] = []
    if not isinstance(d, dict):
        errors.append("not_a_dict")
        return False, errors
    if not isinstance(d.get("title"), str) and d.get("title") is not None:
        errors.append("title_not_str")
        return False, errors
    if not isinstance(d.get("ingredients"), list):
        errors.append("ingredients_not_list")
        return False, errors
    if not isinstance(d.get("tags"), list):
        errors.append("tags_not_list")
        return False, errors
    if "instructions" in d:
        instr = d["instructions"]
        if instr is not None and not isinstance(instr, str):
            errors.append("instructions_not_str_or_null")
            return False, errors
    if "recipe_book" in d:
        book = d["recipe_book"]
        if book is not None and not isinstance(book, str):
            errors.append("recipe_book_not_str_or_null")
            return False, errors
    for item in d["ingredients"]:
        if not isinstance(item, dict):
            errors.append("ingredient_item_not_dict")
            return False, errors
        for k in ("qty", "unit", "name"):
            if k not in item:
                errors.append(f"ingredient_missing_key_{k}")
                return False, errors
    return True, errors


def unload_ollama(model: str, base_url: str) -> None:
    """Unload model from GPU. Mirrors Mac-mini/benchmark_models.py:_unload (line 142)."""
    try:
        requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=10,
        )
    except Exception:
        pass
    time.sleep(2)


def _ollama_one_call(
    model: str,
    image_b64: str,
    prompt: str,
    base_url: str,
    num_ctx: int,
    keep_alive: str | int = "10s",
    timeout_s: int = _OLLAMA_HTTP_TIMEOUT_S,
) -> tuple[dict | None, dict, str]:
    """Single Ollama call. Returns (parsed_dict_or_None, per_call_metadata, raw_response_text).

    per_call_metadata keys: latency_s, eval_count, raw_response, http_status.
    """
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "images": [image_b64],
        "keep_alive": keep_alive,
        "options": {"temperature": 0.1, "num_ctx": num_ctx},
    }
    md: dict = {"latency_s": None, "eval_count": None, "raw_response": None, "http_status": None}

    t0 = time.monotonic()
    try:
        r = requests.post(f"{base_url}/api/generate", json=body, timeout=timeout_s)
    except requests.RequestException as exc:
        md["latency_s"] = round(time.monotonic() - t0, 3)
        md["raw_response"] = str(exc)
        return None, md, str(exc)

    md["latency_s"] = round(time.monotonic() - t0, 3)
    md["http_status"] = r.status_code

    if r.status_code != 200:
        md["raw_response"] = f"HTTP {r.status_code}: {r.text[:1000]}"
        return None, md, md["raw_response"]

    raw_text = r.text
    md["raw_response"] = raw_text

    try:
        resp_json = r.json()
    except ValueError:
        return None, md, raw_text

    md["eval_count"] = resp_json.get("eval_count")
    response_text = resp_json.get("response", "") or ""

    try:
        parsed = json.loads(response_text)
    except (json.JSONDecodeError, ValueError):
        return None, md, response_text

    if not isinstance(parsed, dict):
        return None, md, response_text

    return parsed, md, response_text


def call_ollama_vision(
    model: str,
    image_path: pathlib.Path,
    prompt: str,
    base_url: str = "http://localhost:11434",
    num_ctx: int | None = None,
    keep_alive: str | int = "10s",
    timeout_s: int = _OLLAMA_HTTP_TIMEOUT_S,
) -> tuple[dict | None, dict]:
    """Call Ollama vision API with a single image. Returns (parsed_json_or_None, metadata).

    Retries once on schema validation failure (parse-fail or schema-fail), feeding back the
    malformed response to the model with an explicit "your output failed validation" prompt.

    metadata keys: latency_s (first call only), cold_load_s (set by cold_call_ollama),
    eval_count (first call), raw_response (final response body), n_retries (0 or 1),
    retry_latency_s (None if no retry).

    HTTP errors (including 429) are never collapsed into a parsed result — they always
    return (None, metadata). This is the regression gate for the 2026-05-04 incident
    where a 429 with empty body silently produced {}.
    """
    if num_ctx is None:
        num_ctx = default_ctx_for(model, "vision")

    with image_path.open("rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")

    metadata: dict = {
        "latency_s": None,
        "cold_load_s": None,
        "eval_count": None,
        "raw_response": None,
        "n_retries": 0,
        "retry_latency_s": None,
    }

    parsed, md1, raw1 = _ollama_one_call(
        model, image_b64, prompt, base_url, num_ctx, keep_alive, timeout_s,
    )
    metadata["latency_s"] = md1["latency_s"]
    metadata["eval_count"] = md1["eval_count"]
    metadata["raw_response"] = md1["raw_response"]

    # Decide whether to retry: HTTP non-200 is unrecoverable; otherwise, validate the parsed
    # output against the schema. Retry only when first call returned a parseable-but-malformed
    # response (parse fail or schema fail) — not when the model is unreachable / rate-limited.
    if md1.get("http_status") != 200:  # None (RequestException) or non-200 — both unrecoverable
        return None, metadata

    is_valid, schema_errors = validate_schema(parsed)
    if is_valid:
        parsed_normalized, norm_warnings = normalize_extraction(parsed)
        if norm_warnings:
            metadata["normalize_warnings"] = norm_warnings
        return parsed_normalized, metadata

    # Retry: same image, augmented prompt with the malformed response and explicit error list.
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
    parsed2, md2, _raw2 = _ollama_one_call(
        model, image_b64, retry_prompt, base_url, num_ctx, keep_alive, timeout_s,
    )
    metadata["n_retries"] = 1
    metadata["retry_latency_s"] = md2.get("latency_s")
    if parsed2 is None:
        return None, metadata

    # Replace raw_response with the retry's body so the parsed_ok row reflects what we used.
    # If parsed2 is parseable but still schema-invalid, surface it anyway — _score will mark
    # structural_validity=False, which is more useful signal than dropping it as parse_fail.
    metadata["raw_response"] = md2.get("raw_response")
    metadata["eval_count"] = md2.get("eval_count")
    # Normalize on both branches: normalize_extraction is idempotent and only acts
    # on dict-shaped ingredients, so it can't make a schema-invalid result worse,
    # and a partially-bad retry with valid ingredients still benefits from the split.
    parsed2_normalized, norm_warnings2 = normalize_extraction(parsed2)
    if norm_warnings2:
        metadata["normalize_warnings"] = norm_warnings2
    return parsed2_normalized, metadata


def cold_call_ollama(
    model: str,
    image_path: pathlib.Path,
    prompt: str,
    base_url: str = "http://localhost:11434",
    num_ctx: int | None = None,
) -> tuple[dict | None, dict]:
    """Unload model then call — measures cold-start latency.

    Mirrors Mac-mini/benchmark_models.py:_cold_load (line 243).
    cold_load_s includes the 2s sleep from unload_ollama plus inference time.
    """
    if num_ctx is None:
        num_ctx = default_ctx_for(model, "vision")
    t0 = time.monotonic()
    unload_ollama(model, base_url)
    parsed, metadata = call_ollama_vision(model, image_path, prompt, base_url, num_ctx)
    metadata["cold_load_s"] = round(time.monotonic() - t0, 3)
    return parsed, metadata
