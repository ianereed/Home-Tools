"""
Local-only image/PDF analyzer for the intake pipeline.

Pipeline: qwen2.5vl (vision/OCR/classify per page) + qwen3 (calendar detection
from merged text). All processing happens on the mini's Ollama instance; no
cloud fallback exists — the previous Gemini path was removed 2026-04-24 as
part of the privacy-first Slack overhaul.

Privacy: all structured_text is treated as PRIVATE (same as RawMessage.body_text).
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone

import requests

import config
from models import CandidateEvent, FileAnalysisResult

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_MB = 20

# NAS folder mapping — used in the vision prompt below.
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
    "Books": "Book files, ebooks, audiobook notes",
    "Events": "Save-the-dates, wedding invitations, event flyers, tickets with specific date/time",
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
                "think": False,
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
                "think": False,
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

    best = max(page_dicts, key=lambda d: d.get("classification", {}).get("confidence", 0.0))
    cls = best.get("classification", {})
    ext = best.get("extraction", {})

    primary_category_raw = cls.get("primary_category", "Documents")
    parts = primary_category_raw.split("/", 1)
    category = parts[0]
    subcategory = parts[1] if len(parts) > 1 else None

    text_parts = []
    for i, (d, fname) in enumerate(zip(page_dicts, filenames), 1):
        page_text = d.get("extraction", {}).get("structured_text", "")
        if page_text:
            text_parts.append(f"--- PAGE {i} ({fname}) ---\n{page_text}")
    merged_text = "\n\n".join(text_parts)

    date = None
    for d in page_dicts:
        date = d.get("extraction", {}).get("date")
        if date:
            break

    return FileAnalysisResult(
        file_id="",
        primary_category=category,
        subcategory=subcategory,
        confidence=float(cls.get("confidence", 0.5)),
        title=ext.get("title", filenames[0] if filenames else "document")[:200],
        date=date,
        structured_text=merged_text,
        summary=ext.get("summary", "Document analyzed")[:120],
        calendar_items=[],
        document_type=ext.get("document_type", ""),
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

    if len(page_dicts) < len(pages):
        logger.info(
            "Local vision: %d/%d pages analyzed successfully",
            len(page_dicts), len(pages),
        )

    merged = _merge_page_results(page_dicts, filenames)
    if merged is None:
        return None

    calendar_items = _detect_calendar_items_local(merged.structured_text)
    merged.calendar_items = calendar_items
    if calendar_items:
        logger.info("Local vision: detected %d calendar item(s)", len(calendar_items))

    return merged


# ── PDF rasterization (required because Ollama vision can't read PDF bytes) ────

def rasterize_to_pages(
    file_bytes: bytes, filename: str, mimetype: str, dpi: int = 200,
) -> list[tuple[bytes, str, str]]:
    """Return a list of (bytes, page_filename, image_mimetype) ready for local vision.

    For images: returns [(bytes, filename, mimetype)] unchanged.
    For PDFs: rasterizes each page to PNG via pypdfium2.
    Raises RuntimeError on unreadable PDFs so the caller can surface the error.
    """
    if mimetype != "application/pdf":
        return [(file_bytes, filename, mimetype)]

    try:
        import io
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError(
            "pypdfium2 is required to process PDFs with local vision "
            "(pip install pypdfium2)"
        ) from exc

    pdf = pdfium.PdfDocument(file_bytes)
    scale = dpi / 72.0
    stem = filename.rsplit(".", 1)[0]
    pages: list[tuple[bytes, str, str]] = []
    for i in range(len(pdf)):
        page = pdf[i]
        pil = page.render(scale=scale).to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        pages.append((buf.getvalue(), f"{stem}-p{i+1}.png", "image/png"))
    return pages


def _analyze_local_no_calendar(
    pages: list[tuple[bytes, str, str]],
) -> FileAnalysisResult | None:
    """Same as _analyze_local but skips the calendar-detection step.

    Used by the classify CLI which only needs category/confidence, not events.
    """
    page_dicts: list[dict] = []
    filenames: list[str] = []
    for i, (file_bytes, fname, mt) in enumerate(pages):
        logger.debug("Local vision (classify): page %d/%d (%s)", i + 1, len(pages), fname)
        result = _analyze_page_local(file_bytes, fname, mt)
        if result is not None:
            page_dicts.append(result)
            filenames.append(fname)
    if not page_dicts:
        return None
    return _merge_page_results(page_dicts, filenames)


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_document(
    pages: list[tuple[bytes, str, str]],
    accompanying_text: str = "",
    mock: bool = False,
) -> FileAnalysisResult | None:
    """Analyze a multi-page document. Local-only.

    Args:
        pages: list of (file_bytes, filename, mimetype) in page order.
               Callers should rasterize PDFs via rasterize_to_pages() first.
        accompanying_text: any text the user included with the upload (unused by
            local pipeline, kept for signature compatibility).
        mock: if True, return synthetic result without calling any model.

    Returns FileAnalysisResult or None on failure. Never raises.
    """
    if not pages:
        return None

    if mock:
        filenames = ", ".join(name for _, name, _ in pages)
        return _mock_result(f"{len(pages)}-page document ({filenames})")

    total_mb = sum(len(b) for b, _, _ in pages) / (1024 * 1024)
    logger.info("Analyzing %d-page document (%.1f MB total) locally", len(pages), total_mb)

    return _analyze_local(pages, accompanying_text)


def analyze_file(
    file_bytes: bytes,
    filename: str,
    mimetype: str,
    accompanying_text: str = "",
    mock: bool = False,
) -> FileAnalysisResult | None:
    """Analyze a single image or PDF. Local-only.

    Rasterizes PDFs before sending to vision. Returns None on failure; never raises.
    """
    if mock:
        return _mock_result(filename)

    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        logger.warning("File %s too large (%.1f MB > %d MB limit)", filename, size_mb, MAX_FILE_SIZE_MB)
        return None

    logger.info("Analyzing file %s (%.1f MB) locally", filename, size_mb)

    try:
        pages = rasterize_to_pages(file_bytes, filename, mimetype)
    except RuntimeError as exc:
        logger.warning("Rasterize failed for %s: %s", filename, exc)
        return None

    return _analyze_local(pages, accompanying_text)


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
        document_type="medical_form",
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
