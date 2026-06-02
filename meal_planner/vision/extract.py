"""Production single-photo extraction entrypoint.

Wraps the Ollama vision call with explicit success/failure-mode classification.
The worker (Chunk 2) is responsible for preprocessing the photo with
meal_planner/eval/preprocess_images.py:_process_one before calling here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from meal_planner.vision._ollama import (
    call_ollama_text,
    call_ollama_vision,
    load_prompt,
    validate_schema,
)


@dataclass
class ExtractResult:
    status: str
        # ok | timeout | parse_fail | validation_fail | ollama_error
    parsed: dict | None
    latency_s: float | None
    error: str | None
    n_retries: int
    normalize_warnings: list[str] | None = None
        # Phase 16 Chunk F: post-extraction normalizer flagged splits/discards.
        # None on non-ok branches; list (possibly empty) when normalization ran.


def extract_recipe_from_photo(
    photo_path: Path,
    *,
    timeout_s: int = 500,
    num_ctx: int | None = None,
    base_url: str = "http://localhost:11434",
    keep_alive: str | int = "300s",
    model: str = "llama3.2-vision:11b",
) -> ExtractResult:
    """Run a single Ollama vision extraction; classify the outcome.

    The worker is expected to have already run resize+autocontrast preprocessing on
    the photo before calling this function. status branches:

      - ok: parsed dict validates against the schema
      - validation_fail: model returned a parseable dict but it failed schema check
        (and the in-call retry also failed to recover)
      - parse_fail: model returned text that wouldn't parse as JSON
      - timeout: the underlying HTTP call exceeded timeout_s
      - ollama_error: any other transport / HTTP error (non-200, RequestException)
    """
    prompt = load_prompt()

    # _ollama_one_call swallows RequestException (incl. Timeout) and surfaces
    # the message in metadata["raw_response"]. Classify by string content below.
    parsed, metadata = call_ollama_vision(
        model,
        photo_path,
        prompt,
        base_url=base_url,
        num_ctx=num_ctx,
        keep_alive=keep_alive,
        timeout_s=timeout_s,
    )
    return _classify(parsed, metadata)


def extract_recipe_from_text(
    text: str,
    *,
    timeout_s: int = 500,
    num_ctx: int | None = None,
    base_url: str = "http://localhost:11434",
    keep_alive: str | int = "300s",
    model: str = "llama3.2-vision:11b",
) -> ExtractResult:
    """Extract a recipe from a PDF's embedded text layer (no vision/OCR).

    Same status classification as extract_recipe_from_photo, but the model reads
    the text directly — used by the PDF text-layer fast-path for digital recipe
    printouts, which the flaky vision OCR path handles unreliably. The default
    model is the vision model run in text-only mode, since that is the one the
    worker already keeps warm; any text-capable Ollama model works.
    """
    prompt = load_prompt()
    parsed, metadata = call_ollama_text(
        model,
        text,
        prompt,
        base_url=base_url,
        num_ctx=num_ctx,
        keep_alive=keep_alive,
        timeout_s=timeout_s,
    )
    return _classify(parsed, metadata)


def extract_recipe_from_gemini(
    photo_path: Path,
    *,
    api_key: str,
    timeout_s: int = 60,
) -> ExtractResult:
    """Extract a recipe via Gemini 2.5 Flash (the escalation path).

    Same status classification as the local entrypoints so the worker can persist
    a Gemini result identically. `call_gemini_vision` already validates + retries +
    normalizes; this wraps it into the shared ExtractResult contract.
    """
    # Imported here (not at module top) so the local-only extract paths don't pull
    # the Gemini HTTP module when Gemini isn't configured.
    from meal_planner.vision.gemini_fallback import call_gemini_vision

    parsed, metadata = call_gemini_vision(photo_path, api_key=api_key, timeout_s=timeout_s)
    return _classify(parsed, metadata)


def _classify(parsed: dict | None, metadata: dict) -> ExtractResult:
    """Map an (Ollama parsed result, metadata) pair to an ExtractResult.

    Shared by the photo and text entrypoints so both classify timeout / parse_fail
    / ollama_error / validation_fail / ok identically.
    """
    latency_s = metadata.get("latency_s")
    n_retries = metadata.get("n_retries", 0) or 0
    raw = metadata.get("raw_response") or ""
    norm_warnings = metadata.get("normalize_warnings")

    # If (None, metadata) was returned, classify by the raw_response string.
    if parsed is None:
        if "timed out" in raw.lower() or "timeout" in raw.lower():
            return ExtractResult(
                status="timeout",
                parsed=None,
                latency_s=latency_s,
                error=raw[:500],
                n_retries=n_retries,
            )
        if raw.startswith("HTTP "):
            return ExtractResult(
                status="ollama_error",
                parsed=None,
                latency_s=latency_s,
                error=raw[:500],
                n_retries=n_retries,
            )
        return ExtractResult(
            status="parse_fail",
            parsed=None,
            latency_s=latency_s,
            error=(raw[:500] if raw else "model returned unparseable output"),
            n_retries=n_retries,
        )

    is_valid, errors = validate_schema(parsed)
    if not is_valid:
        return ExtractResult(
            status="validation_fail",
            parsed=parsed,
            latency_s=latency_s,
            error=", ".join(errors) if errors else "schema validation failed",
            n_retries=n_retries,
            normalize_warnings=norm_warnings,
        )

    return ExtractResult(
        status="ok",
        parsed=parsed,
        latency_s=latency_s,
        error=None,
        n_retries=n_retries,
        normalize_warnings=norm_warnings,
    )
