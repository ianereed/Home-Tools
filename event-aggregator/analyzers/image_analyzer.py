"""
Local-first image/PDF analyzer for the intake pipeline.

Analysis order:
  1. Local: qwen2.5vl (vision/OCR/classify) + qwen2.5 (calendar detection)
  2. Cloud fallback: gemini-2.5-flash-lite → gemini-2.5-flash

Privacy: all structured_text is treated as PRIVATE (same as RawMessage.body_text).
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import datetime, timezone

import requests

import config
from models import CandidateEvent, FileAnalysisResult

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_MB = 20
_GEMINI_BATCH_MAX_PAGES = 4  # max pages per Gemini API call

# NAS folder mapping — used in both local and cloud prompts.
NAS_CATEGORIES = {
    "Healthcare/0-Ian Healthcare": "Ian's medical records, health portal screenshots, prescriptions, insurance docs, lab results, EOBs",
    "Healthcare/0-Anny Healthcare": "Anny's medical records",
    "Financial": "Tax documents, bank statements, investment records, bills, receipts, invoices",
    "DIY Projects": "Home improvement photos, project plans, measurements, materials receipts",
    "Documents": "Personal documents, certificates, contracts, legal papers, general unsorted",
    "Engineering": "Technical documents, CAD files, specifications, engineering notes",
    "Identification": "ID cards, passports, driver licenses, visas, birth certificates",
    "Recipes": "Recipe screenshots, cooking instructions, menu photos",
    "334_Iris": "House-related documents for 334 Iris address — mortgage, HOA, utilities, maintenance",
    "Wedding": "Wedding-related photos and documents",
}


def _build_categories_text() -> str:
    return "\n".join(f"- `{path}`: {desc}" for path, desc in NAS_CATEGORIES.items())


def _extract_json_from_text(text: str) -> dict | None:
    """Extract the first {...} JSON block from text."""
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


# ── Local vision prompt (OCR + classify only — no calendar detection) ─────────

_LOCAL_VISION_PROMPT = """\
You are a document analysis assistant. Analyze this image and return ONLY valid JSON:
{{
  "classification": {{
    "primary_category": "<category path from list below>",
    "confidence": <0.0-1.0>,
    "reasoning": "<one sentence>"
  }},
  "extraction": {{
    "document_type": "<e.g. medical_form, receipt, insurance_eob, tax_form, recipe>",
    "title": "<descriptive title, max 80 chars>",
    "date": "<YYYY-MM-DD if visible on document, otherwise null>",
    "structured_text": "<all text from this page, organized with ALL-CAPS section headers and --- separators>",
    "summary": "<one-line summary, max 120 chars, safe to post publicly>"
  }}
}}

Filing categories (choose the best match):
{categories}

If no category matches, use "Documents".
Today's date is {today}.
"""

# ── Calendar detection prompt for text model (OLLAMA_MODEL) ───────────────────

_CALENDAR_DETECT_PROMPT = """\
You are analyzing text extracted from a document. Find any calendar-relevant items
(appointments, follow-ups, deadlines, due dates) that have a SPECIFIC date or time.

Return ONLY valid JSON with this exact structure:
{{
  "calendar_items": [
    {{
      "title": "<descriptive event title>",
      "start": "<ISO 8601 datetime with timezone, e.g. 2026-04-27T14:00:00-07:00>",
      "end": "<ISO 8601 datetime or null>",
      "location": "<location string or null>",
      "category": "<work|personal|social|health|travel|other>"
    }}
  ]
}}

Rules:
- Only include items with a SPECIFIC date/time visible in the text
- Use America/Los_Angeles timezone unless another is explicitly stated
- Set category based on context (medical documents → "health", etc.)
- If no calendar items found, return {{"calendar_items": []}}
- Today's date is {today}

Document text:
{text}
"""

# ── Gemini prompts (multi-page) ───────────────────────────────────────────────

_GEMINI_SYSTEM_PROMPT = """\
You are a document analysis assistant. You will receive one or more document pages.
Your job is to:
1. CLASSIFY the document into the correct filing category.
2. EXTRACT all meaningful text from ALL pages combined into one structured document.
3. DETECT any calendar-relevant items (appointments, deadlines, due dates) across all pages.

## Filing categories (choose the best match):
{categories}

If no category matches well, use "Documents" as the default.

## Output format
Return ONLY valid JSON:
{{
  "classification": {{
    "primary_category": "<NAS path from the list above>",
    "confidence": <0.0-1.0>,
    "reasoning": "<one sentence>"
  }},
  "extraction": {{
    "document_type": "<e.g. medical_form, insurance_document, tax_form, receipt>",
    "title": "<descriptive title for the complete document, max 80 chars>",
    "date": "<YYYY-MM-DD if visible, otherwise null>",
    "structured_text": "<full extracted text, organized with ALL-CAPS section headers and --- separators>",
    "summary": "<one-line summary of the complete document, max 120 chars, safe to post publicly>"
  }},
  "calendar_items": [
    {{
      "title": "<event title>",
      "start": "<ISO 8601 datetime with timezone>",
      "end": "<ISO 8601 datetime or null>",
      "location": "<location or null>",
      "category": "<work|personal|social|health|travel|other>"
    }}
  ]
}}

Today's date is {today}.
"""


# ── Local analysis ─────────────────────────────────────────────────────────────

def _analyze_page_local(file_bytes: bytes, filename: str, mimetype: str) -> dict | None:
    """Analyze a single image page via local qwen2.5vl vision model.

    Returns the raw parsed dict (classification + extraction) or None on failure.
    Does NOT include calendar_items — those are handled separately by the text model.
    """
    if not config.LOCAL_VISION_MODEL:
        return None

    b64_data = base64.standard_b64encode(file_bytes).decode("ascii")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = _LOCAL_VISION_PROMPT.format(
        categories=_build_categories_text(),
        today=today,
    )

    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.LOCAL_VISION_MODEL,
                "prompt": prompt,
                "images": [b64_data],
                "stream": False,
                "format": "json",
                "keep_alive": "10s",
                "options": {"temperature": 0.1},
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        parsed = _extract_json_from_text(text) or json.loads(text)
        if not parsed or "extraction" not in parsed:
            logger.debug("Local vision: unexpected response structure for %s", filename)
            return None
        return parsed
    except requests.exceptions.ConnectionError:
        logger.debug("Local vision: Ollama not reachable for %s", filename)
        return None
    except Exception as exc:
        logger.debug("Local vision: error analyzing %s: %s", filename, exc)
        return None


def _detect_calendar_items_local(structured_text: str) -> list[CandidateEvent]:
    """Run the text model on extracted text to detect calendar-relevant items."""
    if not structured_text or not structured_text.strip():
        return []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Truncate text to keep within model context limits
    text_snippet = structured_text[:4000]
    prompt = _CALENDAR_DETECT_PROMPT.format(today=today, text=text_snippet)

    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "keep_alive": "10s",
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        parsed = json.loads(text)
        return _parse_calendar_items(parsed.get("calendar_items", []))
    except Exception as exc:
        logger.debug("Calendar detection: text model error: %s", exc)
        return []


def _parse_calendar_items(raw_items: list[dict]) -> list[CandidateEvent]:
    """Convert raw calendar item dicts into CandidateEvent objects."""
    items = []
    for item in raw_items:
        start_str = item.get("start")
        if not start_str:
            continue
        try:
            start_dt = datetime.fromisoformat(start_str)
            if start_dt.tzinfo is None:
                from zoneinfo import ZoneInfo
                start_dt = start_dt.replace(tzinfo=ZoneInfo(config.USER_TIMEZONE))
        except (ValueError, KeyError):
            continue

        end_dt = None
        if item.get("end"):
            try:
                end_dt = datetime.fromisoformat(item["end"])
                if end_dt.tzinfo is None:
                    from zoneinfo import ZoneInfo
                    end_dt = end_dt.replace(tzinfo=ZoneInfo(config.USER_TIMEZONE))
            except (ValueError, KeyError):
                pass

        items.append(CandidateEvent(
            title=item.get("title", "Untitled")[:200],
            start_dt=start_dt,
            end_dt=end_dt,
            location=item.get("location"),
            confidence=0.80,
            source="slack_file",
            source_id=f"local_vision",
            category=item.get("category", "other"),
        ))
    return items


def _merge_page_results(page_dicts: list[dict], filenames: list[str]) -> FileAnalysisResult | None:
    """Combine per-page local vision results into a single FileAnalysisResult.

    Returns None if page_dicts is empty.
    """
    if not page_dicts:
        return None

    # Pick the page with highest classification confidence
    best = max(page_dicts, key=lambda d: d.get("classification", {}).get("confidence", 0.0))
    cls = best.get("classification", {})
    ext = best.get("extraction", {})

    primary_category_raw = cls.get("primary_category", "Documents")
    parts = primary_category_raw.split("/", 1)
    category = parts[0]
    subcategory = parts[1] if len(parts) > 1 else None

    # Merge structured_text from all pages
    text_parts = []
    for i, (d, fname) in enumerate(zip(page_dicts, filenames), 1):
        page_text = d.get("extraction", {}).get("structured_text", "")
        if page_text:
            text_parts.append(f"--- PAGE {i} ({fname}) ---\n{page_text}")
    merged_text = "\n\n".join(text_parts)

    # First non-null date wins
    date = None
    for d in page_dicts:
        date = d.get("extraction", {}).get("date")
        if date:
            break

    return FileAnalysisResult(
        file_id="",  # set by caller
        primary_category=category,
        subcategory=subcategory,
        confidence=float(cls.get("confidence", 0.5)),
        title=ext.get("title", filenames[0] if filenames else "document")[:200],
        date=date,
        structured_text=merged_text,
        summary=ext.get("summary", "Document analyzed")[:120],
        calendar_items=[],  # populated later by _detect_calendar_items_local
        original_filename=filenames[0] if filenames else "",
    )


def _analyze_local(
    pages: list[tuple[bytes, str, str]],
    accompanying_text: str = "",
) -> FileAnalysisResult | None:
    """Full local pipeline: qwen2.5vl per page → merge → text model calendar detection."""
    page_dicts = []
    filenames = []
    for i, (file_bytes, filename, mimetype) in enumerate(pages):
        logger.debug("Local vision: analyzing page %d/%d (%s)", i + 1, len(pages), filename)
        result = _analyze_page_local(file_bytes, filename, mimetype)
        if result is not None:
            page_dicts.append(result)
            filenames.append(filename)
        else:
            logger.debug("Local vision: page %d failed — continuing with remaining pages", i + 1)

    if not page_dicts:
        return None

    # Warn if we only got partial results
    if len(page_dicts) < len(pages):
        logger.info(
            "Local vision: %d/%d pages analyzed successfully",
            len(page_dicts), len(pages),
        )

    merged = _merge_page_results(page_dicts, filenames)
    if merged is None:
        return None

    # Calendar detection via text model on merged text
    calendar_items = _detect_calendar_items_local(merged.structured_text)
    merged.calendar_items = calendar_items
    if calendar_items:
        logger.info("Local vision: detected %d calendar item(s)", len(calendar_items))

    return merged


# ── Gemini (cloud fallback) ────────────────────────────────────────────────────

def _parse_retry_delay(resp: requests.Response) -> int:
    """Extract retry delay from a 429 response, defaulting to 60s."""
    try:
        body = resp.json()
        for detail in body.get("error", {}).get("details", []):
            if detail.get("@type", "").endswith("RetryInfo"):
                delay_str = detail.get("retryDelay", "60s")
                return min(30, int(re.sub(r"[^0-9]", "", delay_str) or "30") + 2)
    except Exception:
        pass
    return 30  # cap at 30s so retries aren't wasted


def _analyze_gemini(
    pages: list[tuple[bytes, str, str]],
    accompanying_text: str,
    model: str,
) -> FileAnalysisResult | None:
    """Try a single Gemini model on up to _GEMINI_BATCH_MAX_PAGES pages.

    For documents with more pages, splits into batches and merges.
    Returns FileAnalysisResult or None on failure.
    """
    if not config.GEMINI_API_KEY:
        return None

    if len(pages) > _GEMINI_BATCH_MAX_PAGES:
        return _analyze_gemini_batched(pages, accompanying_text, model)

    return _analyze_gemini_batch(pages, accompanying_text, model, batch_label=None)


def _split_pages(
    pages: list[tuple[bytes, str, str]], max_per_batch: int
) -> list[list[tuple[bytes, str, str]]]:
    """Chunk pages into sublists of at most max_per_batch."""
    return [pages[i:i + max_per_batch] for i in range(0, len(pages), max_per_batch)]


def _analyze_gemini_batched(
    pages: list[tuple[bytes, str, str]],
    accompanying_text: str,
    model: str,
) -> FileAnalysisResult | None:
    """Split large document into batches, analyze each, merge results."""
    batches = _split_pages(pages, _GEMINI_BATCH_MAX_PAGES)
    n = len(batches)
    logger.info("Gemini %s: splitting %d pages into %d batch(es)", model, len(pages), n)

    batch_results: list[FileAnalysisResult] = []
    for i, batch in enumerate(batches):
        label = f"batch {i+1}/{n}"
        text = accompanying_text if i == 0 else ""
        result = _analyze_gemini_batch(batch, text, model, batch_label=label)
        if result is not None:
            batch_results.append(result)
        else:
            logger.warning("Gemini %s: %s failed — continuing with other batches", model, label)

    if not batch_results:
        return None
    if len(batch_results) == 1:
        return batch_results[0]
    return _merge_gemini_results(batch_results)


def _merge_gemini_results(results: list[FileAnalysisResult]) -> FileAnalysisResult:
    """Merge multiple FileAnalysisResult objects (from Gemini batches) into one."""
    best = max(results, key=lambda r: r.confidence)

    merged_text = "\n\n".join(
        f"--- BATCH {i+1} ---\n{r.structured_text}"
        for i, r in enumerate(results)
        if r.structured_text
    )

    # Deduplicate calendar items by title+start proximity
    all_items: list[CandidateEvent] = []
    for r in results:
        for item in r.calendar_items:
            if not _calendar_item_is_duplicate(item, all_items):
                all_items.append(item)

    return FileAnalysisResult(
        file_id=best.file_id,
        primary_category=best.primary_category,
        subcategory=best.subcategory,
        confidence=best.confidence,
        title=best.title,
        date=next((r.date for r in results if r.date), None),
        structured_text=merged_text,
        summary=best.summary,
        calendar_items=all_items,
        original_filename=best.original_filename,
    )


def _calendar_item_is_duplicate(
    item: CandidateEvent, existing: list[CandidateEvent]
) -> bool:
    """True if a calendar item is already in the list (same title + start ±30 min)."""
    from datetime import timedelta
    from thefuzz import fuzz
    for e in existing:
        if fuzz.ratio(item.title.lower(), e.title.lower()) >= 80:
            delta = abs((item.start_dt - e.start_dt).total_seconds())
            if delta <= 1800:  # 30 minutes
                return True
    return False


def _analyze_gemini_batch(
    pages: list[tuple[bytes, str, str]],
    accompanying_text: str,
    model: str,
    batch_label: str | None,
) -> FileAnalysisResult | None:
    """Send one batch of pages to a specific Gemini model. Returns result or None."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system_prompt = _GEMINI_SYSTEM_PROMPT.format(
        categories=_build_categories_text(),
        today=today,
    )
    if batch_label:
        system_prompt += f"\n\nNote: This is {batch_label} of a larger document. Analyze these pages only."

    user_parts: list[dict] = []
    for i, (file_bytes, filename, mimetype) in enumerate(pages, 1):
        user_parts.append({"text": f"Page {i} of {len(pages)}: {filename}"})
        b64_data = base64.standard_b64encode(file_bytes).decode("ascii")
        user_parts.append({"inlineData": {"mimeType": mimetype, "data": b64_data}})

    if accompanying_text:
        user_parts.append({"text": f"The user included this message: {accompanying_text}"})
    user_parts.append({
        "text": (
            f"Analyze this {len(pages)}-page document and return the JSON "
            "classification, extraction (combining all pages), and calendar items."
        )
    })

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    label = batch_label or f"{len(pages)}-page document"

    for attempt in range(3):
        try:
            resp = requests.post(
                endpoint,
                params={"key": config.GEMINI_API_KEY},
                json={
                    "contents": [
                        {"role": "user", "parts": [{"text": system_prompt}]},
                        {"role": "model", "parts": [{"text": "Understood. Send the document pages and I will analyze them."}]},
                        {"role": "user", "parts": user_parts},
                    ],
                },
                timeout=120,
            )

            if resp.status_code == 429:
                retry_delay = _parse_retry_delay(resp)
                logger.warning("Gemini %s: rate limited (429), waiting %ds...", model, retry_delay)
                time.sleep(retry_delay)
                continue

            if resp.status_code == 503:
                wait = [5, 15, 30][attempt] if attempt < 3 else 0
                logger.warning("Gemini %s: 503 for %s — waiting %ds...", model, label, wait)
                if attempt < 2:
                    time.sleep(wait)
                    continue
                return None

            if resp.status_code != 200:
                logger.warning("Gemini %s: HTTP %d for %s: %s", model, resp.status_code, label, resp.text[:200])
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None

            body = resp.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _extract_json_from_text(text)
            if parsed is None:
                logger.warning("Gemini %s: could not parse JSON for %s", model, label)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None

            primary_filename = pages[0][1] if pages else "document"
            return _parse_gemini_response(parsed, primary_filename)

        except requests.RequestException as exc:
            logger.warning("Gemini %s: request error (attempt %d): %s", model, attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** attempt)
            continue
        except (KeyError, IndexError) as exc:
            logger.warning("Gemini %s: response structure error for %s: %s", model, label, exc)
            return None

    logger.warning("Gemini %s: failed after 3 attempts for %s", model, label)
    return None


def _parse_gemini_response(data: dict, filename: str) -> FileAnalysisResult | None:
    """Convert Gemini JSON response dict into a FileAnalysisResult."""
    try:
        classification = data.get("classification", {})
        extraction = data.get("extraction", {})
        raw_calendar = data.get("calendar_items", [])

        primary_category = classification.get("primary_category", "Documents")
        parts = primary_category.split("/", 1)
        category = parts[0]
        subcategory = parts[1] if len(parts) > 1 else None

        return FileAnalysisResult(
            file_id="",  # set by caller
            primary_category=category,
            subcategory=subcategory,
            confidence=float(classification.get("confidence", 0.5)),
            title=extraction.get("title", filename)[:200],
            date=extraction.get("date"),
            structured_text=extraction.get("structured_text", ""),
            summary=extraction.get("summary", "Document analyzed")[:120],
            calendar_items=_parse_calendar_items(raw_calendar),
            original_filename=filename,
        )
    except Exception as exc:
        logger.warning("Failed to parse Gemini response for %s: %s", filename, exc)
        return None


def _analyze_cloud_fallback(
    pages: list[tuple[bytes, str, str]],
    accompanying_text: str,
) -> FileAnalysisResult | None:
    """Try each configured Gemini fallback model in order."""
    if not config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — no cloud fallback available")
        return None

    models = [m.strip() for m in config.GEMINI_FALLBACK_MODELS.split(",") if m.strip()]
    for model in models:
        logger.info("Cloud fallback: trying %s for %d page(s)", model, len(pages))
        result = _analyze_gemini(pages, accompanying_text, model)
        if result is not None:
            logger.info("Cloud fallback: succeeded with %s", model)
            return result
        logger.warning("Cloud fallback: %s failed — trying next", model)

    logger.warning("Cloud fallback: all models exhausted — document unprocessed")
    return None


# ── Image size compression ────────────────────────────────────────────────────

def _prepare_pages(
    pages: list[tuple[bytes, str, str]],
    max_total_mb: float = 18.0,
) -> list[tuple[bytes, str, str]]:
    """Compress images to fit within Gemini's size limit.

    Only compresses when total size exceeds max_total_mb. PDFs are never
    compressed. Compression floor: quality 50 / 1024px longest side.
    """
    total_bytes = sum(len(b) for b, _, _ in pages)
    max_bytes = max_total_mb * 1024 * 1024

    if total_bytes <= max_bytes:
        return pages

    logger.info(
        "Total page size %.1f MB exceeds %.1f MB limit — compressing images",
        total_bytes / (1024 * 1024), max_total_mb,
    )

    try:
        from PIL import Image
        import io
    except ImportError:
        logger.warning("Pillow not available — cannot compress images, sending as-is")
        return pages

    pdf_bytes = sum(len(b) for b, _, mime in pages if mime == "application/pdf")
    image_budget = max_bytes - pdf_bytes
    image_count = sum(1 for _, _, mime in pages if mime != "application/pdf")

    if image_count == 0:
        logger.warning("All pages are PDFs and total size exceeds limit — sending anyway")
        return pages

    per_image_budget = image_budget / image_count

    result = []
    for file_bytes, filename, mimetype in pages:
        if mimetype == "application/pdf":
            result.append((file_bytes, filename, mimetype))
            continue

        if len(file_bytes) <= per_image_budget:
            result.append((file_bytes, filename, mimetype))
            continue

        try:
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(file_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            max_side = max(img.width, img.height)
            if max_side > 2048:
                scale = 2048 / max_side
                img = img.resize(
                    (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                    Image.LANCZOS,
                )

            for quality in (70, 50):
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                compressed = buf.getvalue()
                if len(compressed) <= per_image_budget or quality == 50:
                    if quality == 50 and len(compressed) > per_image_budget:
                        logger.warning(
                            "Could not compress %s to fit budget — sending anyway", filename
                        )
                    stem = filename.rsplit(".", 1)[0]
                    logger.info(
                        "Compressed %s: %.1f MB → %.1f MB (q%d)",
                        filename, len(file_bytes) / (1024 * 1024),
                        len(compressed) / (1024 * 1024), quality,
                    )
                    result.append((compressed, f"{stem}.jpg", "image/jpeg"))
                    break

        except Exception as exc:
            logger.warning("Failed to compress %s: %s — using original", filename, exc)
            result.append((file_bytes, filename, mimetype))

    return result


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_document(
    pages: list[tuple[bytes, str, str]],
    accompanying_text: str = "",
    mock: bool = False,
) -> FileAnalysisResult | None:
    """Analyze a multi-page document. Local-first, cloud fallback.

    Args:
        pages: list of (file_bytes, filename, mimetype) in page order
        accompanying_text: any text the user included with the upload
        mock: if True, return synthetic result without calling any API

    Returns FileAnalysisResult or None on complete failure. Never raises.
    """
    if not pages:
        return None

    if mock:
        filenames = ", ".join(name for _, name, _ in pages)
        return _mock_result(f"{len(pages)}-page document ({filenames})")

    # Compress for cloud fallback (local doesn't need this, but cheap to do once)
    pages = _prepare_pages(pages)

    total_mb = sum(len(b) for b, _, _ in pages) / (1024 * 1024)
    logger.info("Analyzing %d-page document (%.1f MB total)", len(pages), total_mb)

    # 1. Try local
    result = _analyze_local(pages, accompanying_text)
    if result is not None:
        logger.info("Local analysis succeeded for %d-page document", len(pages))
        return result

    # 2. Cloud fallback
    logger.info("Local analysis failed — trying cloud fallback for %d-page document", len(pages))
    return _analyze_cloud_fallback(pages, accompanying_text)


def analyze_file(
    file_bytes: bytes,
    filename: str,
    mimetype: str,
    accompanying_text: str = "",
    mock: bool = False,
) -> FileAnalysisResult | None:
    """Analyze a single image or PDF. Local-first, cloud fallback.

    Returns None on unrecoverable failure. Never raises.
    """
    if mock:
        return _mock_result(filename)

    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        logger.warning("File %s too large (%.1f MB > %d MB limit)", filename, size_mb, MAX_FILE_SIZE_MB)
        return None

    logger.info("Analyzing file %s (%.1f MB)", filename, size_mb)

    # 1. Try local vision for classification + OCR
    page_dict = _analyze_page_local(file_bytes, filename, mimetype)
    if page_dict is not None:
        merged = _merge_page_results([page_dict], [filename])
        if merged is not None:
            merged.calendar_items = _detect_calendar_items_local(merged.structured_text)
            logger.info("Local analysis succeeded for %s", filename)
            return merged

    # 2. Cloud fallback
    logger.info("Local analysis failed for %s — trying cloud fallback", filename)
    return _analyze_cloud_fallback([(file_bytes, filename, mimetype)], accompanying_text)


def check_local_vision_available() -> bool:
    """Return True if the local vision model is available in Ollama."""
    try:
        resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        if resp.status_code != 200:
            return False
        models = [m["name"] for m in resp.json().get("models", [])]
        target = config.LOCAL_VISION_MODEL
        target_base = target.split(":")[0]
        return any(target == m or m.startswith(target_base) for m in models)
    except Exception:
        return False


def _mock_result(filename: str) -> FileAnalysisResult:
    """Return a synthetic FileAnalysisResult for --mock testing."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(config.USER_TIMEZONE)
    mock_start = datetime.now(tz).replace(hour=14, minute=0, second=0, microsecond=0)

    return FileAnalysisResult(
        file_id="",
        primary_category="Healthcare",
        subcategory="0-Ian Healthcare",
        confidence=0.92,
        title=f"Mock Analysis — {filename}",
        date=today,
        structured_text=(
            f"SOURCE: Mock analysis of {filename}\n"
            f"DATE: {today}\n\n"
            "--- MOCK CONTENT ---\n"
            "This is synthetic extracted text for testing.\n"
            "No real document was analyzed.\n"
        ),
        summary=f"Mock document analysis of {filename}",
        calendar_items=[
            CandidateEvent(
                title="Mock Follow-up Appointment",
                start_dt=mock_start,
                end_dt=None,
                location="123 Mock St, Suite 100",
                confidence=0.85,
                source="slack_file",
                source_id=f"file_{filename}",
                category="health",
            ),
        ],
        original_filename=filename,
    )
