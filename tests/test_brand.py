"""Validation for the approved local fluks brand assets."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from custom_components.fluks.const import DOMAIN

BRAND_DIR = Path("custom_components") / DOMAIN / "brand"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EXPECTED_ASSETS = (
    "icon.png",
    "dark_icon.png",
    "logo.png",
    "dark_logo.png",
)


def png_header(path: Path) -> tuple[int, int, int, int, int]:
    """Read the dimensions and core format fields from a PNG IHDR chunk."""
    with path.open("rb") as image:
        assert image.read(8) == PNG_SIGNATURE
        assert struct.unpack(">I", image.read(4))[0] == 13
        assert image.read(4) == b"IHDR"
        width, height, bit_depth, color_type, _compression, _filter, interlace = (
            struct.unpack(">IIBBBBB", image.read(13))
        )
    return width, height, bit_depth, color_type, interlace


@pytest.mark.parametrize("filename", EXPECTED_ASSETS)
def test_brand_asset_is_valid_png(filename):
    """Every supported local-brand filename exists and contains a PNG image."""
    path = BRAND_DIR / filename
    assert path.is_file()
    width, height, bit_depth, color_type, interlace = png_header(path)
    assert width > 0 and height > 0
    assert bit_depth == 8
    assert color_type in (2, 6)
    assert interlace in (0, 1)


@pytest.mark.parametrize("filename", ("icon.png", "dark_icon.png"))
def test_icon_meets_home_assistant_geometry(filename):
    """Icons meet Home Assistant's square normal-resolution specification."""
    width, height, *_ = png_header(BRAND_DIR / filename)
    assert (width, height) == (256, 256)


@pytest.mark.parametrize(
    "filename",
    ("logo.png", "dark_logo.png"),
)
def test_logo_meets_home_assistant_geometry(filename):
    """Logos are landscape with a compliant normal-resolution shortest side."""
    width, height, *_ = png_header(BRAND_DIR / filename)
    assert width > height
    assert 128 <= min(width, height) <= 256
