"""Validation for approved fluks Device-type icon assets."""

import struct

import pytest

from custom_components.fluks.device import icon_path

PHYSICAL_TYPES = (
    "solar",
    "generator",
    "battery",
    "electricVehicle",
    "heatPump",
    "waterHeater",
    "appliance",
)


@pytest.mark.parametrize("device_type", PHYSICAL_TYPES)
def test_device_type_icon_is_valid_transparent_square_png(device_type):
    """Every current physical type resolves to an approved usable PNG."""
    path = icon_path(device_type)
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, bit_depth, color_type = struct.unpack(">IIBB", raw[16:26])
    assert width == height
    assert width >= 256
    assert bit_depth == 8
    assert color_type == 6
