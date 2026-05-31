"""Convert non-JPEG inputs into a single image the vision extractor can read.

The photo-intake pipeline's extractor (`extract_recipe_from_photo`) takes ONE
image. Two input kinds need help before they reach `_process_one`:

- **HEIC/HEIF** (iPhone photos): just register pillow-heif so `PIL.Image.open`
  understands the format — `_process_one` then handles it unchanged.
- **PDF** (recipe prints): rasterize every page via pypdfium2 and stack them
  vertically into one tall image, so a multi-page recipe reaches the model in a
  single call. (Resolution-vs-completeness trade-off chosen 2026-05-31: stack.)

pypdfium2 is the same rasterizer event-aggregator uses
(`analyzers/image_analyzer.py:rasterize_to_pages`) — a pure wheel, no poppler.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PDF_SUFFIXES = frozenset({".pdf"})
HEIF_SUFFIXES = frozenset({".heic", ".heif"})

_heif_registered = False


def register_heif() -> bool:
    """Register the pillow-heif opener with PIL (idempotent).

    Returns True if HEIC/HEIF support is available after the call. Safe to call
    on every ingest; the import + registration is cheap and guarded.
    """
    global _heif_registered
    if _heif_registered:
        return True
    try:
        import pillow_heif
    except ImportError:
        logger.warning("rasterize: pillow-heif not installed — HEIC/HEIF uploads will fail to open")
        return False
    pillow_heif.register_heif_opener()
    _heif_registered = True
    return True


def _stack_vertical(images: list, bg=(255, 255, 255)):
    """Stack PIL images top-to-bottom into one RGB image.

    Width = widest page; narrower pages are centered on a white background.
    Single image in → returned as RGB unchanged. Pure PIL (no pypdfium2), so it
    is unit-testable without the PDF toolchain.
    """
    from PIL import Image

    rgb = [im.convert("RGB") if im.mode != "RGB" else im for im in images]
    if len(rgb) == 1:
        return rgb[0]
    width = max(im.width for im in rgb)
    height = sum(im.height for im in rgb)
    canvas = Image.new("RGB", (width, height), bg)
    y = 0
    for im in rgb:
        x = (width - im.width) // 2  # center narrower pages
        canvas.paste(im, (x, y))
        y += im.height
    return canvas


def pdf_to_stacked_image(src: Path, dst: Path, *, dpi: int = 200) -> int:
    """Rasterize every page of `src` (a PDF) and write a single stacked PNG to
    `dst`. Returns the page count. Raises RuntimeError if pypdfium2 is missing or
    the PDF is unreadable/empty.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("pypdfium2 is required to process PDFs (pip install pypdfium2)") from exc

    pdf = pdfium.PdfDocument(src.read_bytes())
    n = len(pdf)
    if n == 0:
        raise RuntimeError(f"PDF has no pages: {src}")
    scale = dpi / 72.0
    images = [pdf[i].render(scale=scale).to_pil() for i in range(n)]
    stacked = _stack_vertical(images)
    dst.parent.mkdir(parents=True, exist_ok=True)
    stacked.save(dst, "PNG")
    logger.info("rasterize: %s → %s (%d page(s) stacked)", src.name, dst.name, n)
    return n
