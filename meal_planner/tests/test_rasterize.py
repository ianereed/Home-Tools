"""Tests for meal_planner/vision/rasterize.py.

The pure-PIL stacking helper is tested directly. The pypdfium2 PDF path and the
pillow-heif registration are environment-dependent, so they're covered with
importorskip / behavioral assertions that don't require the heavy toolchain.
"""
from __future__ import annotations

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from meal_planner.vision import rasterize  # noqa: E402


def test_stack_vertical_single_image_passthrough_as_rgb():
    img = Image.new("RGBA", (40, 30), (1, 2, 3, 255))
    out = rasterize._stack_vertical([img])
    assert out.mode == "RGB"
    assert out.size == (40, 30)


def test_stack_vertical_combines_pages():
    a = Image.new("RGB", (100, 50), (255, 0, 0))
    b = Image.new("RGB", (80, 30), (0, 0, 255))
    out = rasterize._stack_vertical([a, b])
    # width = widest page; height = sum of page heights
    assert out.size == (100, 80)
    # page A occupies the top band, page B the bottom band (centered)
    assert out.getpixel((50, 10)) == (255, 0, 0)
    assert out.getpixel((50, 65)) == (0, 0, 255)
    # narrower page B is centered → left margin is white background
    assert out.getpixel((1, 65)) == (255, 255, 255)


def test_register_heif_returns_bool_and_is_idempotent():
    first = rasterize.register_heif()
    second = rasterize.register_heif()
    assert isinstance(first, bool)
    assert second is True or first is False  # once True, stays True


def test_pdf_to_stacked_image_bogus_pdf_raises(tmp_path):
    """A non-PDF input raises (clear RuntimeError if pypdfium2 is missing, or an
    unreadable-PDF error if it's present) — never silently produces nothing."""
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF-1.4 not really")
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="pypdfium2 is required"):
            rasterize.pdf_to_stacked_image(src, tmp_path / "out.png")
    else:
        with pytest.raises(Exception):
            rasterize.pdf_to_stacked_image(src, tmp_path / "out.png")
