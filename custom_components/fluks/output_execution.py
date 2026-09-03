"""Execute persisted Home Assistant output Mappings for runtime Decisions."""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.core import HomeAssistant

from .api import FluksApiClient
from .output_mapping import (
    apply_output_transforms,
    validate_output_configuration,
)

_LOGGER = logging.getLogger(__name__)
_DECISION_FIELDS = {"deviceId", "deviceType", "mode"}


def _canonical_value(value: Any, datatype: str) -> Any:
    """Decode the backend runtime scalar using its canonical datatype."""
    if datatype == "number":
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        return int(number) if number.is_integer() else number
    if datatype == "boolean":
        if isinstance(value, bool):
            return value
        if value in ("true", "false"):
            return value == "true"
        raise ValueError
    if datatype == "string" and isinstance(value, str):
        return value
    raise ValueError


class RuntimeOutputExecutor:
    """Resolve runtime Decisions to this Integration's output Mappings."""

    def __init__(
        self, hass: HomeAssistant, api: FluksApiClient, site_id: str
    ) -> None:
        self._hass = hass
        self._api = api
        self._site_id = site_id

    async def async_handle(self, payload: dict[str, Any]) -> None:
        """Execute a supported Decision snapshot without disrupting transport."""
        if payload.get("type") != "decision.snapshot" or not isinstance(
            payload.get("decisions"), list
        ):
            return
        try:
            devices = await self._api.list_devices(self._site_id)
            mappings = await self._api.list_mappings(self._site_id)
            catalog = await self._api.get_device_type_catalog()
        except Exception:  # noqa: BLE001 - runtime discovery must not end transport
            _LOGGER.warning("Unable to resolve fluks output Mappings")
            return

        device_by_external_id = {
            str(device["deviceId"]): device
            for device in devices
            if isinstance(device.get("deviceId"), str)
            and isinstance(device.get("id"), str)
        }
        definitions = {
            str(item["type"]): {
                str(concept["concept"]): concept
                for concept in item.get("concepts", [])
                if isinstance(concept, dict) and isinstance(concept.get("concept"), str)
            }
            for item in catalog
            if isinstance(item, dict) and isinstance(item.get("type"), str)
        }
        for decision in payload["decisions"]:
            if not isinstance(decision, dict):
                continue
            device = device_by_external_id.get(str(decision.get("deviceId")))
            device_type = decision.get("deviceType")
            if device is None or device.get("type") != device_type:
                continue
            for field, raw_value in decision.items():
                if field in _DECISION_FIELDS:
                    continue
                concept = f"{device_type}.{field}"
                definition = definitions.get(str(device_type), {}).get(concept)
                if definition is None or "control" not in definition.get("usages", []):
                    continue
                mapping = next(
                    (
                        item
                        for item in mappings
                        if item.get("direction") == "output"
                        and item.get("deviceId") == device["id"]
                        and item.get("concept") == concept
                        and item.get("mode") == decision.get("mode")
                    ),
                    None,
                )
                if mapping is None:
                    continue
                try:
                    value = _canonical_value(raw_value, str(definition.get("datatype")))
                    configuration = validate_output_configuration(mapping.get("configuration"))
                    await self._async_execute(configuration, value)
                except Exception:  # noqa: BLE001 - one action must not end runtime transport
                    _LOGGER.warning("Unable to execute fluks output Mapping for %s", concept)

    async def _async_execute(
        self, configuration: dict[str, Any], requested_value: Any
    ) -> None:
        """Execute service calls sequentially so configured order is preserved."""
        for action in configuration["actions"]:
            data: dict[str, Any] = {}
            for field, source in action.get("data", {}).items():
                if source["kind"] == "literal":
                    data[field] = source["value"]
                else:
                    data[field] = apply_output_transforms(
                        requested_value, source.get("transforms", [])
                    )
            domain, service = action["service"].split(".", 1)
            await self._hass.services.async_call(
                domain,
                service,
                data,
                blocking=True,
                target={"entity_id": action["target"]["entityId"]},
            )
