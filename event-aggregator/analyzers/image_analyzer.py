"""
Gemini vision analyzer for the image/PDF intake pipeline.

Accepts an image or PDF file, sends it to Gemini 2.5 Pro for classification
and structured extraction, and returns a FileAnalysisResult.

Privacy: extracted text is treated as PRIVATE (same as RawMessage.body_text).
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

# NAS folder mapping — embedded in the vision prompt so Gemini can classify directly.
# Keys are NAS paths relative to NAS_ROOT; values describe the folder's purpose.
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

_SYSTEM_PROMPT = """\
You are a document analysis assistant. You will receive an image or PDF file.
Your job is to:

1. CLASSIFY the document into the correct filing category.
2. EXTRACT all meaningful text and structured information.
3. DETECT any calendar-relevant items (appointments, deadlines, due dates).

## Filing categories (choose the best match):
{categories}

If no category matches well, use "Documents" as the default.

## Output format
Return ONLY valid JSON with this exact structure:
{{
  "classification": {{
    "primary_category": "<NAS path from the list above, e.g. Healthcare/0-Ian Healthcare>",
    "confidence": <0.0-1.0>,
    "reasoning": "<one sentence explaining why>"
  }},
  "extraction": {{
    "document_type": "<e.g. medical_portal_screenshot, receipt, insurance_eob, tax_form, recipe, id_card>",
    "title": "<descriptive title for this document, max 80 chars>",
    "date": "<YYYY-MM-DD if a date is visible on the document, otherwise null>",
    "structured_text": "<full extracted text, organized with ALL-CAPS section headers and --- separators>",
    "summary": "<one-line summary, max 120 chars, safe to post publicly>"
  }},
  "calendar_items": [
    {{
      "title": "<event title>",
      "start": "<ISO 8601 datetime with timezone, e.g. 2026-04-20T13:30:00-07:00>",
      "end": "<ISO 8601 datetime or null>",
      "location": "<location or null>",
      "category": "<work|personal|social|health|travel|other>"
    }}
  ]
}}

## Rules for structured_text extraction
- For health/medical content: use SOURCE, DATE, ALL-CAPS section headers (--- APPOINTMENTS ---, --- INSTRUCTIONS ---, etc.), and flag action items with a warning marker
- For financial documents: capture amounts, dates, account numbers, line items
- For receipts: itemize purchases with prices
- For all documents: preserve all text content, organized logically

## Rules for calendar_items
- Only include items with a SPECIFIC date/time visible in the document
- Use America/Los_Angeles timezone unless another timezone is explicitly shown
- Set category based on document type (health documents → "health", etc.)
- If no calendar items are found, return an empty array

Today's date is {today}.
"""


def _build_categories_text() -> str:
    lines = []
    for path, desc in NAS_CATEGORIES.items():
        lines.append(f"- `{path}`: {desc}")
    return "\n".join(lines)


def _extract_json_from_text(text: str) -> dict | None:
    """Extract the first {...} JSON block from text."""
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def analyze_file(
    file_bytes: bytes,
    filename: str,
    mimetype: str,
    accompanying_text: str = "",
    mock: bool = False,
) -> FileAnalysisResult | None:
    """Analyze an image or PDF via Gemini 2.5 Pro and return structured result.

    Returns None on unrecoverable failure. Never raises.
    """
    if mock:
        return _mock_result(filename)

    if not config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — cannot analyze file")
        return None

    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        logger.warning("File %s too large (%.1f MB > %d MB limit)", filename, size_mb, MAX_FILE_SIZE_MB)
        return None

    b64_data = base64.standard_b64encode(file_bytes).decode("ascii")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system_prompt = _SYSTEM_PROMPT.format(
        categories=_build_categories_text(),
        today=today,
    )

    user_parts = [
        {"inlineData": {"mimeType": mimetype, "data": b64_data}},
    ]
    if accompanying_text:
        user_parts.append(
            {"text": f"The user included this message with the upload: {accompanying_text}"}
        )
    user_parts.append(
        {"text": "Analyze this document and return the JSON classification, extraction, and calendar items."}
    )

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.GEMINI_MODEL}:generateContent"
    )

    # Retry with exponential backoff
    for attempt in range(3):
        try:
            resp = requests.post(
                endpoint,
                params={"key": config.GEMINI_API_KEY},
                json={
                    "contents": [
                        {"role": "user", "parts": [{"text": system_prompt}]},
                        {"role": "model", "parts": [{"text": "Understood. Send the document and I will analyze it."}]},
                        {"role": "user", "parts": user_parts},
                    ],
                },
                timeout=90,
            )

            if resp.status_code == 429:
                # Rate limited — extract retry delay if available
                retry_delay = _parse_retry_delay(resp)
                logger.warning("Gemini rate limited (429), waiting %ds...", retry_delay)
                time.sleep(retry_delay)
                continue

            if resp.status_code == 503:
                wait = [15, 30][attempt] if attempt < 2 else 0
                logger.warning("Gemini HTTP 503 for %s (service overloaded) — waiting %ds...", filename, wait)
                if attempt < 2:
                    time.sleep(wait)
                    continue
                return None

            if resp.status_code != 200:
                logger.warning("Gemini HTTP %d for %s: %s", resp.status_code, filename, resp.text[:200])
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None

            body = resp.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _extract_json_from_text(text)

            if parsed is None:
                logger.warning("Could not parse JSON from Gemini response for %s", filename)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None

            return _parse_response(parsed, filename)

        except requests.RequestException as exc:
            logger.warning("Gemini request error (attempt %d): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** attempt)
            continue
        except (KeyError, IndexError) as exc:
            logger.warning("Gemini response structure error for %s: %s", filename, exc)
            return None

    logger.warning("Gemini analysis failed after 3 attempts for %s", filename)
    return None


def _parse_retry_delay(resp: requests.Response) -> int:
    """Extract retry delay from a 429 response, defaulting to 60s."""
    try:
        body = resp.json()
        for detail in body.get("error", {}).get("details", []):
            if detail.get("@type", "").endswith("RetryInfo"):
                delay_str = detail.get("retryDelay", "60s")
                return int(re.sub(r"[^0-9]", "", delay_str) or "60") + 2
    except Exception:
        pass
    return 60


def _parse_response(data: dict, filename: str) -> FileAnalysisResult | None:
    """Convert Gemini JSON response into a FileAnalysisResult."""
    try:
        classification = data.get("classification", {})
        extraction = data.get("extraction", {})
        raw_calendar = data.get("calendar_items", [])

        primary_category = classification.get("primary_category", "Documents")
        # Split into category and subcategory
        parts = primary_category.split("/", 1)
        category = parts[0]
        subcategory = parts[1] if len(parts) > 1 else None

        # Parse calendar items into CandidateEvent objects
        calendar_items = []
        for item in raw_calendar:
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

            calendar_items.append(
                CandidateEvent(
                    title=item.get("title", "Untitled")[:200],
                    start_dt=start_dt,
                    end_dt=end_dt,
                    location=item.get("location"),
                    confidence=0.85,
                    source="slack_file",
                    source_id=f"file_{filename}",
                    category=item.get("category", "other"),
                )
            )

        return FileAnalysisResult(
            file_id="",  # set by caller
            primary_category=category,
            subcategory=subcategory,
            confidence=float(classification.get("confidence", 0.5)),
            title=extraction.get("title", filename)[:200],
            date=extraction.get("date"),
            structured_text=extraction.get("structured_text", ""),
            summary=extraction.get("summary", "Document analyzed")[:120],
            calendar_items=calendar_items,
            original_filename=filename,
        )

    except Exception as exc:
        logger.warning("Failed to parse Gemini response for %s: %s", filename, exc)
        return None


def _prepare_pages(
    pages: list[tuple[bytes, str, str]],
    max_total_mb: float = 18.0,
) -> list[tuple[bytes, str, str]]:
    """Compress images to fit within Gemini's size limit.

    Only compresses when total size exceeds max_total_mb. PDFs are never
    compressed. Compression floor: quality 50 / 1024px longest side.

    Returns list of (file_bytes, filename, mimetype) — may have updated
    bytes and mimetype (PNG → JPEG) for compressed images.
    """
    total_bytes = sum(len(b) for b, _, _ in pages)
    max_bytes = max_total_mb * 1024 * 1024

    if total_bytes <= max_bytes:
        return pages  # nothing to do

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

    # Separate images from PDFs; PDFs count against budget but can't be compressed
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

        # Compress this image
        try:
            img = Image.open(io.BytesIO(file_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # Step 1: resize if largest side > 2048
            max_side = max(img.width, img.height)
            if max_side > 2048:
                scale = 2048 / max_side
                new_w = max(1, int(img.width * scale))
                new_h = max(1, int(img.height * scale))
                img = img.resize((new_w, new_h), Image.LANCZOS)

            # Step 2: try quality 70 first
            for quality in (70, 50):
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                compressed = buf.getvalue()

                if len(compressed) <= per_image_budget or quality == 50:
                    if quality == 50 and len(compressed) > per_image_budget:
                        logger.warning(
                            "Could not compress %s to fit budget (%.1f MB → %.1f MB at q50) — sending anyway",
                            filename, len(file_bytes) / (1024 * 1024), len(compressed) / (1024 * 1024),
                        )

                    # Floor: never go below 1024px on longest side
                    if max(img.width, img.height) < 1024 and max_side > 1024:
                        # Image was too aggressively resized — try again at 1024px
                        scale = 1024 / max_side
                        img_fallback = Image.open(io.BytesIO(file_bytes))
                        if img_fallback.mode in ("RGBA", "P"):
                            img_fallback = img_fallback.convert("RGB")
                        img_fallback = img_fallback.resize(
                            (max(1, int(img_fallback.width * scale)),
                             max(1, int(img_fallback.height * scale))),
                            Image.LANCZOS,
                        )
                        buf2 = io.BytesIO()
                        img_fallback.save(buf2, format="JPEG", quality=quality, optimize=True)
                        compressed = buf2.getvalue()

                    stem = filename.rsplit(".", 1)[0]
                    new_name = f"{stem}.jpg"
                    logger.info(
                        "Compressed %s: %.1f MB → %.1f MB (q%d)",
                        filename, len(file_bytes) / (1024 * 1024),
                        len(compressed) / (1024 * 1024), quality,
                    )
                    result.append((compressed, new_name, "image/jpeg"))
                    break

        except Exception as exc:
            logger.warning("Failed to compress %s: %s — using original", filename, exc)
            result.append((file_bytes, filename, mimetype))

    return result


_DOCUMENT_SYSTEM_PROMPT = """\
You are a document analysis assistant. You will receive a multi-page document as a series of images.
Your job is to analyze the ENTIRE document as a single unit:

1. CLASSIFY the document into the correct filing category.
2. EXTRACT all meaningful text from ALL pages combined into one structured document.
3. DETECT any calendar-relevant items (appointments, deadlines, due dates) across all pages.

## Filing categories (choose the best match):
{categories}

If no category matches well, use "Documents" as the default.

## Output format
Return ONLY valid JSON with this exact structure:
{{
  "classification": {{
    "primary_category": "<NAS path from the list above, e.g. Healthcare/0-Ian Healthcare>",
    "confidence": <0.0-1.0>,
    "reasoning": "<one sentence explaining why>"
  }},
  "extraction": {{
    "document_type": "<e.g. medical_form, insurance_document, tax_form, contract, receipt>",
    "title": "<descriptive title for the complete document, max 80 chars>",
    "date": "<YYYY-MM-DD if a date is visible on the document, otherwise null>",
    "structured_text": "<full extracted text from ALL pages, organized with ALL-CAPS section headers and --- separators>",
    "summary": "<one-line summary of the complete document, max 120 chars, safe to post publicly>"
  }},
  "calendar_items": [
    {{
      "title": "<event title>",
      "start": "<ISO 8601 datetime with timezone, e.g. 2026-04-20T13:30:00-07:00>",
      "end": "<ISO 8601 datetime or null>",
      "location": "<location or null>",
      "category": "<work|personal|social|health|travel|other>"
    }}
  ]
}}

## Rules for structured_text extraction
- Combine text from ALL pages into one coherent document
- Use --- PAGE N --- separators between page content
- For health/medical content: use SOURCE, DATE, ALL-CAPS section headers, flag action items with a warning marker
- For financial documents: capture all amounts, dates, account numbers, line items
- For all documents: preserve all text content, organized logically

## Rules for calendar_items
- Only include items with a SPECIFIC date/time visible in the document
- Use America/Los_Angeles timezone unless another timezone is explicitly shown
- If no calendar items are found, return an empty array

Today's date is {today}.
"""


def analyze_document(
    pages: list[tuple[bytes, str, str]],
    accompanying_text: str = "",
    mock: bool = False,
) -> FileAnalysisResult | None:
    """Analyze a multi-page document via Gemini and return one combined result.

    Args:
        pages: list of (file_bytes, filename, mimetype) in page order
        accompanying_text: any text the user included with the upload
        mock: if True, return synthetic result without calling API

    Returns FileAnalysisResult or None on failure. Never raises.
    """
    if not pages:
        return None

    if mock:
        filenames = ", ".join(name for _, name, _ in pages)
        return _mock_result(f"{len(pages)}-page document ({filenames})")

    if not config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — cannot analyze document")
        return None

    # Compress if needed before calculating size
    pages = _prepare_pages(pages)

    total_mb = sum(len(b) for b, _, _ in pages) / (1024 * 1024)
    if total_mb > MAX_FILE_SIZE_MB:
        logger.warning("Document too large after compression (%.1f MB) — skipping", total_mb)
        return None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system_prompt = _DOCUMENT_SYSTEM_PROMPT.format(
        categories=_build_categories_text(),
        today=today,
    )

    # Build multi-image user parts
    user_parts: list[dict] = []
    for i, (file_bytes, filename, mimetype) in enumerate(pages, 1):
        user_parts.append({"text": f"Page {i} of {len(pages)}: {filename}"})
        b64_data = base64.standard_b64encode(file_bytes).decode("ascii")
        user_parts.append({"inlineData": {"mimeType": mimetype, "data": b64_data}})

    if accompanying_text:
        user_parts.append({"text": f"The user included this message: {accompanying_text}"})
    user_parts.append({
        "text": (
            f"Analyze this complete {len(pages)}-page document and return the JSON "
            "classification, extraction (combining all pages), and calendar items."
        )
    })

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.GEMINI_MODEL}:generateContent"
    )

    label = f"{len(pages)}-page document"
    for attempt in range(3):
        try:
            resp = requests.post(
                endpoint,
                params={"key": config.GEMINI_API_KEY},
                json={
                    "contents": [
                        {"role": "user", "parts": [{"text": system_prompt}]},
                        {"role": "model", "parts": [{"text": "Understood. Send the document pages and I will analyze them as a complete document."}]},
                        {"role": "user", "parts": user_parts},
                    ],
                },
                timeout=120,  # longer timeout for multi-page
            )

            if resp.status_code == 429:
                retry_delay = _parse_retry_delay(resp)
                logger.warning("Gemini rate limited (429), waiting %ds...", retry_delay)
                time.sleep(retry_delay)
                continue

            if resp.status_code == 503:
                wait = [15, 30][attempt] if attempt < 2 else 0
                logger.warning("Gemini HTTP 503 for %s (service overloaded) — waiting %ds...", label, wait)
                if attempt < 2:
                    time.sleep(wait)
                    continue
                return None

            if resp.status_code != 200:
                logger.warning("Gemini HTTP %d for %s: %s", resp.status_code, label, resp.text[:200])
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None

            body = resp.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = _extract_json_from_text(text)

            if parsed is None:
                logger.warning("Could not parse JSON from Gemini response for %s", label)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return None

            # Use the first filename as the representative filename
            primary_filename = pages[0][1] if pages else "document"
            return _parse_response(parsed, primary_filename)

        except requests.RequestException as exc:
            logger.warning("Gemini request error (attempt %d): %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(2 ** attempt)
            continue
        except (KeyError, IndexError) as exc:
            logger.warning("Gemini response structure error for %s: %s", label, exc)
            return None

    logger.warning("Gemini analysis failed after 3 attempts for %s", label)
    return None


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
