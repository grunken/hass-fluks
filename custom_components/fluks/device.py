"""Shared fluks Device identity and presentation helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .const import DOMAIN

CONF_HA_DEVICE_ID = "ha_device_id"
SITE_DEVICE_TYPE = "site"


def icon_filename(device_type: str) -> str:
    """Resolve a canonical type to its approved local icon filename."""
    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", device_type).lower()
    return f"{snake_case}.png"


def icon_path(device_type: str) -> Path:
    """Return the approved local icon path for a physical Device type."""
    return Path(__file__).parent / "icons" / icon_filename(device_type)


def stable_device_id(
    integration_id: str,
    device_type: str,
    ha_device_id: str,
    instance_id: str | None = None,
) -> str:
    """Derive a stable external identity for one logical HA Device instance."""
    identity = f"{DOMAIN}:{integration_id}:{device_type}:{ha_device_id}"
    if instance_id is not None:
        identity = f"{identity}:{instance_id}"
    return str(uuid5(NAMESPACE_URL, identity))


def device_type_name(device: dict[str, Any], translations: dict[str, str]) -> str:
    """Return the localized presentation name for a canonical Device type."""
    return translations.get(
        f"device_type_{device['type']}",
        translations.get("device_type_fallback", "Energy device"),
    )


def device_identity(device: dict[str, Any]) -> str | None:
    """Return the best optional human-readable Device identity."""
    properties = device.get("properties") or {}
    for key in ("displayName", "name"):
        if isinstance(properties.get(key), str) and properties[key].strip():
            return properties[key].strip()
    metadata = " ".join(
        str(properties[key]).strip()
        for key in ("vendor", "model")
        if isinstance(properties.get(key), str) and properties[key].strip()
    )
    return metadata or None


def device_display_name(device: dict[str, Any], translations: dict[str, str]) -> str:
    """Prefix the best available identity with the localized Device type."""
    type_name = device_type_name(device, translations)
    identity = device_identity(device)
    return f"{type_name} · {identity}" if identity else type_name


def concept_label(concept: dict[str, Any], translations: dict[str, str]) -> str:
    """Return a localized label without interpreting canonical semantics."""
    name = str(concept["concept"])
    fallback = re.sub(
        r"(?<!^)(?=[A-Z])", " ", name.rsplit(".", 1)[-1]
    ).capitalize()
    return translations.get(f"concept_{name}", fallback)
