"""Execute persisted Home Assistant output Mappings for runtime Decisions."""

from __future__ import annotations

import logging
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import FluksApiClient
from .output_mapping import (
    apply_output_transforms,
    validate_output_configuration,
)
from .const import DOMAIN, TEMPERATURE_OWNERSHIP_STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)
_DECISION_FIELDS = {"deviceId", "deviceType", "mode"}
_TEMPERATURE_CONTROLS = {
    "heatPump.temperature",
    "heatPump.tankTemperature",
    "waterHeater.temperature",
    "spaceHeater.temperature",
}


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


def _matches_value_condition(mapping: dict[str, Any], value: Any, datatype: str) -> bool:
    """Return whether a Mapping accepts the incoming canonical value."""
    condition = mapping.get("valueCondition")
    if condition is None:
        return True
    if condition not in {"gtZero", "ltZero", "eqZero"} or datatype != "number":
        return False
    try:
        numeric = _canonical_value(value, datatype)
    except ValueError:
        return False
    if condition == "gtZero":
        return numeric > 0
    if condition == "ltZero":
        return numeric < 0
    return numeric == 0


class RuntimeOutputExecutor:
    """Resolve runtime Decisions to this Integration's output Mappings."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: FluksApiClient,
        site_id: str,
        entry_id: str | None = None,
        *,
        ownership_store: Store[dict[str, Any]] | None = None,
    ) -> None:
        self._hass = hass
        self._api = api
        self._site_id = site_id
        self._balance_active = False
        self._temperature_store = ownership_store or Store(
            hass,
            TEMPERATURE_OWNERSHIP_STORAGE_VERSION,
            f"{DOMAIN}.temperature_ownership.{entry_id or site_id}",
            atomic_writes=True,
        )
        self._temperature_ownership: dict[str, dict[str, int | float]] = {}
        self._temperature_loaded = False

    async def _async_load_temperature_ownership(self) -> None:
        if self._temperature_loaded:
            return
        stored = await self._temperature_store.async_load()
        records = stored.get("controls", {}) if isinstance(stored, dict) else {}
        if isinstance(records, dict):
            self._temperature_ownership = {
                str(key): dict(value)
                for key, value in records.items()
                if isinstance(value, dict)
                and "previous" in value
                and "applied" in value
                and self._numeric(value["previous"]) is not None
                and self._numeric(value["applied"]) is not None
            }
        self._temperature_loaded = True

    async def _async_save_temperature_ownership(self) -> None:
        await self._temperature_store.async_save({"controls": self._temperature_ownership})

    @staticmethod
    def _numeric(value: Any) -> int | float | None:
        if isinstance(value, bool):
            return None
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not number.is_finite():
            return None
        return int(number) if number == number.to_integral_value() else float(number)

    @classmethod
    def _same_numeric(cls, left: Any, right: Any) -> bool:
        left_number = cls._numeric(left)
        right_number = cls._numeric(right)
        return (
            left_number is not None
            and right_number is not None
            and Decimal(str(left_number)) == Decimal(str(right_number))
        )

    @staticmethod
    def _temperature_binding(configuration: dict[str, Any]) -> tuple[str, str] | None:
        bindings = [
            (action["target"]["entityId"], field)
            for action in configuration.get("actions", [])
            for field, source in action.get("data", {}).items()
            if source.get("kind") == "requestedValue"
        ]
        return bindings[0] if len(bindings) == 1 else None

    def _temperature_setpoint(
        self, configuration: dict[str, Any]
    ) -> int | float | None:
        binding = self._temperature_binding(configuration)
        if binding is None:
            return None
        entity_id, field = binding
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        value = state.attributes.get(field)
        if value is None:
            domain = entity_id.split(".", 1)[0]
            if domain in {"climate", "water_heater"}:
                return None
            value = state.state
        return self._numeric(value)

    def _output_reference(self, reference: dict[str, Any]) -> int | float | None:
        """Read a numeric state or attribute used by an output transform."""
        state = self._hass.states.get(reference["entityId"])
        if state is None:
            return None
        value = (
            state.attributes.get(reference["attribute"])
            if "attribute" in reference
            else state.state
        )
        return self._numeric(value)

    @staticmethod
    def _temperature_key(device: dict[str, Any], concept: str) -> str:
        return f"{device['deviceId']}|{concept}"

    async def async_handle(self, payload: dict[str, Any]) -> None:
        """Execute a supported Decision snapshot without disrupting transport."""
        if payload.get("type") != "decision.snapshot" or not isinstance(
            payload.get("decisions"), list
        ):
            return
        await self._async_load_temperature_ownership()
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
        decisions = [item for item in payload["decisions"] if isinstance(item, dict)]
        ownership = [item for item in decisions if self._is_balance_ownership(item)]
        remaining = [item for item in decisions if not self._is_balance_ownership(item)]
        for decision in ownership + remaining:
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
                if self._balance_active and concept == "battery.power":
                    continue
                mapping_mode = decision.get("mode")
                if concept in _TEMPERATURE_CONTROLS and mapping_mode == "release":
                    mapping_mode = "target"
                mapping = next(
                    (
                        item
                        for item in mappings
                        if item.get("direction") == "output"
                        and item.get("deviceId") == device["id"]
                        and item.get("concept") == concept
                        and item.get("mode") == mapping_mode
                        and _matches_value_condition(
                            item, raw_value, str(definition.get("datatype"))
                        )
                    ),
                    None,
                )
                if mapping is None:
                    if concept in _TEMPERATURE_CONTROLS and decision.get("mode") == "release":
                        self._temperature_ownership.pop(
                            self._temperature_key(device, concept), None
                        )
                        await self._async_save_temperature_ownership()
                    continue
                try:
                    ownership_key = self._temperature_key(device, concept)
                    configuration = validate_output_configuration(mapping.get("configuration"))
                    if concept in _TEMPERATURE_CONTROLS:
                        if decision.get("mode") == "release":
                            value = None
                        else:
                            value = _canonical_value(raw_value, str(definition.get("datatype")))
                        await self._async_temperature_decision(
                            ownership_key,
                            configuration,
                            value,
                            decision.get("mode"),
                        )
                    else:
                        value = _canonical_value(raw_value, str(definition.get("datatype")))
                        await self._async_execute(configuration, value)
                    if concept == "site.power" and decision.get("mode") == "balance":
                        self._balance_active = True
                    elif concept == "site.power" and decision.get("mode") == "release":
                        self._balance_active = False
                except Exception:  # noqa: BLE001 - one action must not end runtime transport
                    _LOGGER.warning("Unable to execute fluks output Mapping for %s", concept)

    async def _async_temperature_decision(
        self,
        ownership_key: str,
        configuration: dict[str, Any],
        requested_value: Any,
        mode: Any,
    ) -> None:
        """Apply or safely release one temporary temperature override."""
        if mode == "release":
            ownership = self._temperature_ownership.get(ownership_key)
            current = self._temperature_setpoint(configuration)
            if not ownership or current is None:
                self._temperature_ownership.pop(ownership_key, None)
                await self._async_save_temperature_ownership()
                return
            if self._same_numeric(current, ownership["applied"]):
                await self._async_execute(
                    configuration,
                    ownership["previous"],
                    apply_transforms=False,
                )
            self._temperature_ownership.pop(ownership_key, None)
            await self._async_save_temperature_ownership()
            return

        if mode != "target":
            await self._async_execute(configuration, requested_value)
            return
        current = self._temperature_setpoint(configuration)
        if current is None:
            raise ValueError("Temperature writable setpoint is unavailable")
        ownership = self._temperature_ownership.get(ownership_key)
        previous = current
        if ownership and self._same_numeric(current, ownership["applied"]):
            previous = ownership["previous"]
        elif ownership:
            self._temperature_ownership.pop(ownership_key, None)
            await self._async_save_temperature_ownership()
        await self._async_execute(configuration, requested_value)
        await self._hass.async_block_till_done()
        applied = self._temperature_setpoint(configuration)
        if applied is None:
            raise ValueError("Temperature applied setpoint is unavailable")
        self._temperature_ownership[ownership_key] = {
            "previous": previous,
            "applied": applied,
        }
        await self._async_save_temperature_ownership()

    @staticmethod
    def _is_balance_ownership(decision: dict[str, Any]) -> bool:
        """Return whether this Decision can change Site balance ownership."""
        return (
            decision.get("deviceType") == "site"
            and "power" in decision
            and decision.get("mode") in {"balance", "release"}
        )

    async def _async_execute(
        self,
        configuration: dict[str, Any],
        requested_value: Any,
        *,
        apply_transforms: bool = True,
    ) -> None:
        """Execute service calls sequentially so configured order is preserved."""
        for action in configuration["actions"]:
            data: dict[str, Any] = {}
            for field, source in action.get("data", {}).items():
                if source["kind"] == "literal":
                    data[field] = source["value"]
                elif not apply_transforms:
                    data[field] = requested_value
                else:
                    data[field] = apply_output_transforms(
                        requested_value,
                        source.get("transforms", []),
                        reference_resolver=self._output_reference,
                    )
            domain, service = action["service"].split(".", 1)
            await self._hass.services.async_call(
                domain,
                service,
                data,
                blocking=True,
                target={"entity_id": action["target"]["entityId"]},
            )
