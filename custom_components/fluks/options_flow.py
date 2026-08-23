"""Native Options Flow for adding one Home Assistant Device to fluks."""

from __future__ import annotations

import re
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    FluksApiClient,
    FluksApiError,
    FluksCannotConnect,
    FluksConflict,
    FluksUnauthorized,
    FluksValidationError,
)
from .const import (
    CONF_DEVICE,
    CONF_INTEGRATION_INTERNAL_ID,
    CONF_INTEGRATION_KEY,
    CONF_SITE_ID,
)
from .matcher import input_configuration, suggest_entities

CONF_HA_DEVICE_ID = "ha_device_id"
CONF_AZIMUTH_DEGREES = "azimuth_degrees"
CONF_TILT_DEGREES = "tilt_degrees"
SITE_DEVICE_TYPE = "site"
SECTION_MEASUREMENTS = "measurements"
SECTION_ENERGY = "energy"
SECTION_INSTALLATION = "installation"


def icon_filename(device_type: str) -> str:
    """Resolve a canonical type to its approved local icon filename."""
    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", device_type).lower()
    return f"{snake_case}.png"


def icon_path(device_type: str) -> Path:
    """Return the approved local icon path for a physical Device type."""
    return Path(__file__).parent / "icons" / icon_filename(device_type)


class FluksOptionsFlow(config_entries.OptionsFlow):
    """Add exactly one fluks Device without runtime behavior."""

    def __init__(self) -> None:
        """Create the stable identity once for this Add Device attempt."""
        self._api: FluksApiClient | None = None
        self._catalog: dict[str, dict[str, Any]] = {}
        self._device_type: str | None = None
        self._ha_device_id: str | None = None
        self._device_id = str(uuid4())
        self._backend_device_internal_id: str | None = None
        self._suggestions: dict[str, str] = {}
        self._selected_entities: dict[str, str] = {}
        self._solar_properties: dict[str, float] = {}

    @property
    def api(self) -> FluksApiClient:
        """Return a flow-scoped authenticated API client."""
        if self._api is None:
            self._api = FluksApiClient(
                async_get_clientsession(self.hass),
                integration_key=self.config_entry.data.get(CONF_INTEGRATION_KEY),
            )
        return self._api

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Load the backend-owned catalog and offer physical Device types."""
        if not self.config_entry.data.get(CONF_INTEGRATION_KEY):
            return await self._async_require_reauth()
        if CONF_DEVICE in self.config_entry.options:
            return self.async_abort(reason="device_already_configured")

        errors: dict[str, str] = {}
        try:
            catalog = await self.api.get_device_type_catalog()
            self._catalog = {
                str(item["type"]): item
                for item in catalog
                if isinstance(item, dict)
                and isinstance(item.get("type"), str)
                and isinstance(item.get("concepts"), list)
                and item["type"] != SITE_DEVICE_TYPE
            }
            if not self._catalog:
                raise FluksApiError("EMPTY_CATALOG")
            if any(
                not icon_path(device_type).is_file()
                for device_type in self._catalog
            ):
                errors["base"] = "missing_device_icon"
            else:
                return await self.async_step_device_type()
        except FluksCannotConnect:
            errors["base"] = "cannot_connect"
        except FluksApiError:
            errors["base"] = "catalog_unavailable"

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema({}), errors=errors
        )

    async def async_step_device_type(
        self, user_input: dict[str, Any] | None = None
    ):
        """Create native menu targets from the live catalog, not a local registry."""
        for device_type in self._catalog:
            setattr(
                self,
                f"async_step_{device_type}",
                partial(self._async_select_device_type, device_type),
            )
        return self.async_show_menu(
            step_id="device_type", menu_options=list(self._catalog), sort=True
        )

    async def _async_select_device_type(
        self, device_type: str, user_input: dict[str, Any] | None = None
    ):
        """Keep the live catalog entry and request HA discovery context."""
        self._device_type = device_type
        setattr(
            self,
            f"async_step_ha_device_{device_type}",
            partial(self._async_step_ha_device, device_type),
        )
        setattr(
            self,
            f"async_step_review_{device_type}",
            partial(self._async_step_review, device_type),
        )
        return await self._async_step_ha_device(device_type)

    async def _async_step_ha_device(
        self, device_type: str, user_input: dict[str, Any] | None = None
    ):
        """Choose a native Home Assistant Device as discovery context."""
        errors: dict[str, str] = {}
        if user_input is not None:
            ha_device_id = user_input[CONF_HA_DEVICE_ID]
            if dr.async_get(self.hass).async_get(ha_device_id) is None:
                errors[CONF_HA_DEVICE_ID] = "unknown_ha_device"
            else:
                self._ha_device_id = ha_device_id
                concepts = self._mappable_concepts()
                self._suggestions = suggest_entities(
                    self.hass, concepts, ha_device_id
                )
                return await self._async_step_review(device_type)

        return self.async_show_form(
            step_id=f"ha_device_{device_type}",
            data_schema=vol.Schema(
                {vol.Required(CONF_HA_DEVICE_ID): selector.DeviceSelector()}
            ),
            errors=errors,
        )

    def _mappable_concepts(self) -> list[dict[str, Any]]:
        """Return only backend-declared directly mappable fact concepts."""
        if self._device_type is None:
            return []
        return [
            item
            for item in self._catalog[self._device_type]["concepts"]
            if isinstance(item, dict)
            and isinstance(item.get("concept"), str)
            and "fact" in item.get("usages", [])
            and item.get("source") == "mapping"
        ]

    def _review_schema(self) -> vol.Schema:
        """Build optional entity fields directly from the live catalog."""
        grouped_fields: dict[str, dict[Any, Any]] = {
            SECTION_MEASUREMENTS: {},
            SECTION_ENERGY: {},
        }
        for concept in self._mappable_concepts():
            concept_name = str(concept["concept"])
            default = self._selected_entities.get(
                concept_name, self._suggestions.get(concept_name)
            )
            key = (
                vol.Optional(
                    concept_name, description={"suggested_value": default}
                )
                if default
                else vol.Optional(concept_name)
            )
            section_name = (
                SECTION_ENERGY
                if concept.get("cadence") == "interval"
                else SECTION_MEASUREMENTS
            )
            grouped_fields[section_name][key] = selector.EntitySelector()

        if self._device_type == "solar":
            installation: dict[Any, Any] = {}
            installation[vol.Optional(CONF_AZIMUTH_DEGREES)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=360,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°",
                )
            )
            installation[vol.Optional(CONF_TILT_DEGREES)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=90,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°",
                )
            )
            grouped_fields[SECTION_INSTALLATION] = installation

        fields: dict[Any, Any] = {}
        for section_name, section_fields in grouped_fields.items():
            if section_fields:
                fields[vol.Optional(section_name)] = section(
                    vol.Schema(section_fields), {"collapsed": False}
                )
        return vol.Schema(fields)

    async def _async_step_review(
        self, device_type: str, user_input: dict[str, Any] | None = None
    ):
        """Review suggested optional measurements and save the Device."""
        if self._device_type is None or self._ha_device_id is None:
            return self.async_abort(reason="invalid_flow_state")

        errors: dict[str, str] = {}
        if user_input is not None:
            concept_names = {
                str(item["concept"]) for item in self._mappable_concepts()
            }
            self._selected_entities = {
                concept: str(entity_id)
                for section_name in (SECTION_MEASUREMENTS, SECTION_ENERGY)
                for concept, entity_id in user_input.get(section_name, {}).items()
                if concept in concept_names and entity_id
            }
            self._solar_properties = {}
            installation = user_input.get(SECTION_INSTALLATION, {})
            if installation.get(CONF_AZIMUTH_DEGREES) is not None:
                azimuth = float(installation[CONF_AZIMUTH_DEGREES])
                if not 0 <= azimuth < 360:
                    errors[CONF_AZIMUTH_DEGREES] = "invalid_solar_property"
                else:
                    self._solar_properties["azimuthDegrees"] = azimuth
            if installation.get(CONF_TILT_DEGREES) is not None:
                tilt = float(installation[CONF_TILT_DEGREES])
                if not 0 <= tilt <= 90:
                    errors[CONF_TILT_DEGREES] = "invalid_solar_property"
                else:
                    self._solar_properties["tiltDegrees"] = tilt
            if not errors:
                try:
                    return await self._save_device()
                except FluksValidationError:
                    errors["base"] = "invalid_input"
                except FluksUnauthorized:
                    return await self._async_require_reauth()
                except FluksCannotConnect:
                    errors["base"] = "cannot_connect"
                except FluksConflict:
                    errors["base"] = "identity_conflict"
                except FluksApiError:
                    errors["base"] = "save_failed"

        return self.async_show_form(
            step_id=f"review_{device_type}",
            data_schema=self._review_schema(),
            errors=errors,
        )

    async def _async_require_reauth(self):
        """Start one native recovery flow without credential fallback."""
        self.config_entry.async_start_reauth(self.hass)
        return self.async_abort(reason="reauth_required")

    def _device_properties(self) -> dict[str, Any]:
        """Use available HA metadata without asking the user to repeat it."""
        if self._ha_device_id is None:
            return dict(self._solar_properties)
        device = dr.async_get(self.hass).async_get(self._ha_device_id)
        properties: dict[str, Any] = dict(self._solar_properties)
        if device is None:
            return properties
        if device.name_by_user or device.name:
            properties["displayName"] = device.name_by_user or device.name
        if device.manufacturer:
            properties["vendor"] = device.manufacturer
        if device.model:
            properties["model"] = device.model
        return properties

    async def _find_device(self) -> dict[str, Any] | None:
        devices = await self.api.list_devices(self.config_entry.data[CONF_SITE_ID])
        return next(
            (item for item in devices if item.get("deviceId") == self._device_id),
            None,
        )

    async def _ensure_device(self) -> dict[str, Any]:
        """Reuse the stable external identity before or after a lost response."""
        existing = await self._find_device()
        if existing is not None:
            if existing.get("type") != self._device_type:
                raise FluksConflict("DEVICE_ID_CONFLICT")
            return existing
        try:
            return await self.api.create_device(
                self.config_entry.data[CONF_SITE_ID],
                self._device_id,
                self._device_type or "",
                self._device_properties(),
            )
        except FluksConflict:
            existing = await self._find_device()
            if existing is None or existing.get("type") != self._device_type:
                raise
            return existing

    async def _save_device(self):
        """Create/reuse the Device, then create only confirmed input mappings."""
        device = await self._ensure_device()
        self._backend_device_internal_id = str(device["id"])
        site_id = self.config_entry.data[CONF_SITE_ID]
        integration_internal_id = self.config_entry.data[CONF_INTEGRATION_INTERNAL_ID]
        concepts = {
            str(item["concept"]): item for item in self._mappable_concepts()
        }
        desired: list[dict[str, Any]] = []
        for concept_name, entity_id in self._selected_entities.items():
            desired.append(
                {
                    "integrationId": integration_internal_id,
                    "deviceId": self._backend_device_internal_id,
                    "concept": concept_name,
                    "direction": "input",
                    "configuration": input_configuration(
                        self.hass, concepts[concept_name], entity_id
                    ),
                }
            )

        existing = await self.api.list_mappings(site_id)
        for mapping in desired:
            if any(self._same_mapping(item, mapping) for item in existing):
                continue
            try:
                created = await self.api.create_mapping(site_id, mapping)
                existing.append(created)
            except FluksConflict:
                refreshed = await self.api.list_mappings(site_id)
                if not any(self._same_mapping(item, mapping) for item in refreshed):
                    raise
                existing = refreshed

        return self.async_create_entry(
            title="",
            data={
                CONF_DEVICE: {
                    "device_id": self._device_id,
                    "internal_id": self._backend_device_internal_id,
                    "type": self._device_type,
                    "ha_device_id": self._ha_device_id,
                    "mappings": [
                        {
                            "concept": item["concept"],
                            "direction": item["direction"],
                            "configuration": item["configuration"],
                        }
                        for item in desired
                    ],
                }
            },
        )

    @staticmethod
    def _same_mapping(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
        """Recognize only an exact documented Mapping after a lost response."""
        return all(
            existing.get(key) == desired[key]
            for key in (
                "integrationId",
                "deviceId",
                "concept",
                "direction",
                "configuration",
            )
        )
