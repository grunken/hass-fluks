"""Native Options Flow for adding and managing fluks Devices."""

from __future__ import annotations

import re
from functools import partial
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.translation import async_get_translations

from .api import (
    FluksApiClient,
    FluksApiError,
    FluksCannotConnect,
    FluksConflict,
    FluksInvalidCredentials,
    FluksNotFound,
    FluksUnauthorized,
    FluksValidationError,
)
from .const import (
    CONF_DEVICE,
    CONF_DEVICE_CONTEXTS,
    CONF_INTEGRATION_ID,
    CONF_INTEGRATION_INTERNAL_ID,
    CONF_INTEGRATION_KEY,
    CONF_SITE_ID,
    DOMAIN,
)
from .matcher import input_configuration, suggest_entities

CONF_HA_DEVICE_ID = "ha_device_id"
CONF_AZIMUTH_DEGREES = "azimuth_degrees"
CONF_TILT_DEGREES = "tilt_degrees"
CONF_SELECTED_DEVICE = "selected_device"
CONF_DISPLAY_NAME = "display_name"
CONF_VENDOR = "vendor"
CONF_MODEL = "model"
SITE_DEVICE_TYPE = "site"
SECTION_MEASUREMENTS = "measurements"
SECTION_ENERGY = "energy"
SECTION_INSTALLATION = "installation"
SECTION_DEVICE_INFORMATION = "device_information"


def icon_filename(device_type: str) -> str:
    """Resolve a canonical type to its approved local icon filename."""
    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", device_type).lower()
    return f"{snake_case}.png"


def icon_path(device_type: str) -> Path:
    """Return the approved local icon path for a physical Device type."""
    return Path(__file__).parent / "icons" / icon_filename(device_type)


def stable_device_id(
    integration_id: str, device_type: str, ha_device_id: str
) -> str:
    """Derive one stable external identity for an HA Device/type pairing."""
    identity = f"{DOMAIN}:{integration_id}:{device_type}:{ha_device_id}"
    return str(uuid5(NAMESPACE_URL, identity))


class FluksOptionsFlow(config_entries.OptionsFlow):
    """Add and incrementally edit Site-owned Devices."""

    def __init__(self) -> None:
        """Create the stable identity once for this Add Device attempt."""
        self._api: FluksApiClient | None = None
        self._catalog: dict[str, dict[str, Any]] = {}
        self._device_type: str | None = None
        self._ha_device_id: str | None = None
        self._device_id: str | None = None
        self._backend_device_internal_id: str | None = None
        self._device_creation_attempted = False
        self._suggestions: dict[str, str] = {}
        self._selected_entities: dict[str, str] = {}
        self._solar_properties: dict[str, float] = {}
        self._editing_device: dict[str, Any] | None = None
        self._original_mappings: dict[str, dict[str, Any]] = {}
        self._original_properties: dict[str, Any] = {}
        self._selected_device_label: str | None = None
        self._selected_site_label: str | None = None

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
        """Offer the native Device-management actions."""
        if not self.config_entry.data.get(CONF_INTEGRATION_KEY):
            return await self._async_require_reauth()
        # Bind one credential-scoped client to this whole native flow instance.
        # This also makes every later menu branch use the same auth context.
        _ = self.api

        return self.async_show_menu(
            step_id="init", menu_options=["devices", "add_device", "site"]
        )

    async def async_step_site(self, user_input: dict[str, Any] | None = None):
        """Offer Site administration separately from Device management."""
        self._selected_site_label = await self._async_site_label()
        return self.async_show_menu(
            step_id="site",
            menu_options=["delete_site"],
            description_placeholders={"site": self._selected_site_label},
        )

    async def async_step_delete_site(
        self, user_input: dict[str, Any] | None = None
    ):
        """Require explicit confirmation before Site authentication."""
        if self._selected_site_label is None:
            self._selected_site_label = await self._async_site_label()
        return self.async_show_menu(
            step_id="delete_site",
            menu_options=["confirm_delete_site", "cancel_delete_site"],
            description_placeholders={"site": self._selected_site_label},
        )

    async def async_step_cancel_delete_site(
        self, user_input: dict[str, Any] | None = None
    ):
        """Return to Site administration without any side effects."""
        return await self.async_step_site()

    async def async_step_confirm_delete_site(
        self, user_input: dict[str, Any] | None = None
    ):
        """Move from Site confirmation to temporary human login."""
        return await self.async_step_site_delete_login()

    async def async_step_site_delete_login(
        self, user_input: dict[str, Any] | None = None
    ):
        """Authenticate once, delete the Site, then remove this ConfigEntry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, _expires_in = await self.api.login(
                    user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
                self.api.set_human_access_token(token)
                try:
                    await self.api.delete_site(
                        self.config_entry.data[CONF_SITE_ID]
                    )
                finally:
                    self.api.set_human_access_token(None)
                entry_id = self.config_entry.entry_id
                await self.hass.config_entries.async_remove(entry_id)
                return self.async_abort(reason="site_deleted")
            except FluksInvalidCredentials:
                errors["base"] = "invalid_auth"
            except FluksValidationError:
                errors["base"] = "invalid_input"
            except FluksCannotConnect:
                errors["base"] = "cannot_connect"
            except FluksNotFound:
                # Site 404 intentionally conflates foreign and absent Sites.
                errors["base"] = "site_delete_access_denied"
            except FluksUnauthorized:
                errors["base"] = "unauthorized"
            except FluksApiError:
                errors["base"] = "site_delete_failed"

        if self._selected_site_label is None:
            self._selected_site_label = await self._async_site_label()
        return self.async_show_form(
            step_id="site_delete_login",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL
                        )
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"site": self._selected_site_label},
        )

    async def _async_site_label(self) -> str:
        """Return the configured Site name without exposing its identifier."""
        if self.config_entry.title.strip():
            return self.config_entry.title.strip()
        translations = await async_get_translations(
            self.hass,
            self.hass.config.language,
            "options",
            integrations={DOMAIN},
        )
        return translations.get(
            f"component.{DOMAIN}.options.step.site.site_fallback", "Site"
        )

    async def _async_load_catalog(self, retry_step: str):
        """Load and validate the backend-owned catalog for an action."""

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
                return None
        except FluksCannotConnect:
            errors["base"] = "cannot_connect"
        except FluksApiError:
            errors["base"] = "catalog_unavailable"

        return self.async_show_form(
            step_id=retry_step, data_schema=vol.Schema({}), errors=errors
        )

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ):
        """Enter the approved Milestone 2 Add Device flow."""
        failed = await self._async_load_catalog("add_device")
        if failed is not None:
            return failed
        return await self.async_step_device_type()

    async def async_step_devices(self, user_input: dict[str, Any] | None = None):
        """List Site Devices without exposing backend identities."""
        errors: dict[str, str] = {}
        if user_input and CONF_SELECTED_DEVICE in user_input:
            return await self._async_open_device(user_input[CONF_SELECTED_DEVICE])
        try:
            response = await self.api.list_devices(
                self.config_entry.data[CONF_SITE_ID]
            )
            if any(
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not isinstance(item.get("type"), str)
                for item in response
            ):
                raise FluksApiError("INVALID_RESPONSE")
            devices = [
                item for item in response if item["type"] != SITE_DEVICE_TYPE
            ]
            if not devices:
                return self.async_abort(reason="no_devices")
            translations = await async_get_translations(
                self.hass,
                self.hass.config.language,
                "options",
                integrations={DOMAIN},
            )
            presented_devices = sorted(
                devices,
                key=lambda item: (
                    self._device_type_name(item, translations).casefold(),
                    (self._device_identity(item) or "").casefold(),
                ),
            )
            choices = {
                str(item["id"]): self._device_display_name(item, translations)
                for item in presented_devices
            }
        except FluksUnauthorized:
            return await self._async_require_reauth()
        except FluksCannotConnect:
            errors["base"] = "cannot_connect"
            choices = {}
        except FluksApiError:
            errors["base"] = "load_failed"
            choices = {}

        schema: dict[Any, Any] = {}
        if choices:
            schema[vol.Required(CONF_SELECTED_DEVICE)] = vol.In(choices)
        return self.async_show_form(
            step_id="devices", data_schema=vol.Schema(schema), errors=errors
        )

    @staticmethod
    def _device_display_name(
        device: dict[str, Any], translations: dict[str, str]
    ) -> str:
        """Prefix the best available identity with its translated Device type."""
        type_name = FluksOptionsFlow._device_type_name(device, translations)
        identity = FluksOptionsFlow._device_identity(device)
        return f"{type_name} · {identity}" if identity else type_name

    @staticmethod
    def _device_type_name(
        device: dict[str, Any], translations: dict[str, str]
    ) -> str:
        """Return the translated presentation name for a canonical Device type."""
        device_type = str(device["type"])
        return translations.get(
            f"component.{DOMAIN}.options.step.device_type.menu_options.{device_type}",
            translations[
                f"component.{DOMAIN}.options.step.devices.type_fallback"
            ],
        )

    @staticmethod
    def _device_identity(device: dict[str, Any]) -> str | None:
        """Return the best optional human-readable identity for sorting and display."""
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

    async def _async_open_device(self, device_internal_id: str):
        """Load one Device, its Integration mappings, and canonical context."""
        site_id = self.config_entry.data[CONF_SITE_ID]
        try:
            device = await self.api.get_device(site_id, device_internal_id)
            if device.get("type") == SITE_DEVICE_TYPE:
                return self.async_abort(reason="invalid_flow_state")
            failed = await self._async_load_catalog("devices")
            if failed is not None:
                return failed
            device_type = str(device.get("type"))
            if device_type not in self._catalog:
                return self.async_abort(reason="unsupported_device_type")
            mappings = await self.api.list_mappings(
                site_id, device_id=device_internal_id
            )
        except FluksUnauthorized:
            return await self._async_require_reauth()
        except FluksCannotConnect:
            return self.async_show_form(
                step_id="devices", data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
            )
        except FluksApiError:
            return self.async_show_form(
                step_id="devices", data_schema=vol.Schema({}),
                errors={"base": "load_failed"},
            )

        self._editing_device = device
        self._device_type = device_type
        self._backend_device_internal_id = device_internal_id
        self._device_id = str(device.get("deviceId", self._device_id))
        self._original_properties = dict(device.get("properties") or {})
        self._original_mappings = {
            str(item["concept"]): item
            for item in mappings
            if item.get("direction") == "input"
            and isinstance(item.get("concept"), str)
        }
        self._ha_device_id = self._ha_context_for_device(device_internal_id)
        missing = [
            concept
            for concept in self._mappable_concepts()
            if concept["concept"] not in self._original_mappings
        ]
        self._suggestions = (
            suggest_entities(self.hass, missing, self._ha_device_id)
            if self._ha_device_id
            else {}
        )
        setattr(
            self,
            f"async_step_review_{device_type}",
            partial(self._async_step_review, device_type),
        )
        translations = await async_get_translations(
            self.hass,
            self.hass.config.language,
            "options",
            integrations={DOMAIN},
        )
        self._selected_device_label = self._device_display_name(
            device, translations
        )
        return await self.async_step_device_actions()

    async def async_step_device_actions(
        self, user_input: dict[str, Any] | None = None
    ):
        """Offer native actions only after one Device has been selected."""
        if self._editing_device is None or self._selected_device_label is None:
            return self.async_abort(reason="invalid_flow_state")
        return self.async_show_menu(
            step_id="device_actions",
            menu_options=["edit_device", "delete_device"],
            description_placeholders={"device": self._selected_device_label},
        )

    async def async_step_edit_device(
        self, user_input: dict[str, Any] | None = None
    ):
        """Open the existing Milestone 3 Device editor unchanged."""
        if self._device_type is None:
            return self.async_abort(reason="invalid_flow_state")
        return await self._async_step_review(self._device_type)

    async def async_step_delete_device(
        self, user_input: dict[str, Any] | None = None
    ):
        """Require a separate native confirmation before human login."""
        if self._editing_device is None or self._selected_device_label is None:
            return self.async_abort(reason="invalid_flow_state")
        return self.async_show_menu(
            step_id="delete_device",
            menu_options=["confirm_delete", "cancel_delete"],
            description_placeholders={"device": self._selected_device_label},
        )

    async def async_step_cancel_delete(
        self, user_input: dict[str, Any] | None = None
    ):
        """Return safely without changing backend or local state."""
        return await self.async_step_device_actions()

    async def async_step_confirm_delete(
        self, user_input: dict[str, Any] | None = None
    ):
        """Move from explicit confirmation to temporary human login."""
        return await self.async_step_delete_login()

    async def async_step_delete_login(
        self, user_input: dict[str, Any] | None = None
    ):
        """Authenticate once as a human and lifecycle-delete the Device."""
        if self._backend_device_internal_id is None:
            return self.async_abort(reason="invalid_flow_state")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, _expires_in = await self.api.login(
                    user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
                )
                self.api.set_human_access_token(token)
                await self.api.delete_device(
                    self.config_entry.data[CONF_SITE_ID],
                    self._backend_device_internal_id,
                )
                return self._finish_deleted_device()
            except FluksInvalidCredentials:
                errors["base"] = "invalid_auth"
            except FluksValidationError:
                errors["base"] = "invalid_input"
            except FluksCannotConnect:
                errors["base"] = "cannot_connect"
            except FluksNotFound:
                # The backend deliberately uses the same 404 for foreign and
                # nonexistent resources, so absence cannot be proven here.
                errors["base"] = "delete_access_denied"
            except FluksUnauthorized:
                errors["base"] = "unauthorized"
            except FluksApiError:
                errors["base"] = "delete_failed"

        return self.async_show_form(
            step_id="delete_login",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL
                        )
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "device": self._selected_device_label or ""
            },
        )

    def _finish_deleted_device(self):
        """Forget only local discovery context and finish cleanly."""
        options = dict(self.config_entry.options)
        contexts = dict(options.get(CONF_DEVICE_CONTEXTS, {}))
        if self._backend_device_internal_id:
            contexts.pop(self._backend_device_internal_id, None)
        options[CONF_DEVICE_CONTEXTS] = contexts
        options.pop(CONF_DEVICE, None)
        return self.async_create_entry(title="", data=options)

    def _ha_context_for_device(self, internal_id: str) -> str | None:
        """Read only the local HA discovery context, including old M2 options."""
        contexts = self.config_entry.options.get(CONF_DEVICE_CONTEXTS, {})
        context = contexts.get(internal_id, {}) if isinstance(contexts, dict) else {}
        ha_device_id = context.get(CONF_HA_DEVICE_ID)
        if isinstance(ha_device_id, str):
            return ha_device_id
        legacy = self.config_entry.options.get(CONF_DEVICE, {})
        if legacy.get("internal_id") == internal_id:
            value = legacy.get(CONF_HA_DEVICE_ID)
            return value if isinstance(value, str) else None
        return None

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
                try:
                    duplicate = await self._async_has_device_context(
                        ha_device_id, device_type
                    )
                except FluksUnauthorized:
                    return await self._async_require_reauth()
                except FluksCannotConnect:
                    errors["base"] = "cannot_connect"
                except FluksApiError:
                    errors["base"] = "load_failed"
                else:
                    if duplicate:
                        errors[CONF_HA_DEVICE_ID] = (
                            "device_type_already_configured"
                        )
                    else:
                        self._ha_device_id = ha_device_id
                        self._device_id = stable_device_id(
                            self.config_entry.data[CONF_INTEGRATION_ID],
                            device_type,
                            ha_device_id,
                        )
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

    async def _async_has_device_context(
        self, ha_device_id: str, device_type: str
    ) -> bool:
        """Check the Integration-local HA Device/type uniqueness rule."""
        unresolved_internal_ids: set[str] = set()
        contexts = self.config_entry.options.get(CONF_DEVICE_CONTEXTS, {})
        if isinstance(contexts, dict):
            for internal_id, context in contexts.items():
                if (
                    not isinstance(context, dict)
                    or context.get(CONF_HA_DEVICE_ID) != ha_device_id
                ):
                    continue
                context_type = context.get("type")
                if context_type == device_type:
                    return True
                if not isinstance(context_type, str):
                    unresolved_internal_ids.add(str(internal_id))

        legacy = self.config_entry.options.get(CONF_DEVICE, {})
        if (
            isinstance(legacy, dict)
            and legacy.get(CONF_HA_DEVICE_ID) == ha_device_id
            and legacy.get("type") == device_type
        ):
            return True

        if not unresolved_internal_ids:
            return False
        devices = await self.api.list_devices(self.config_entry.data[CONF_SITE_ID])
        return any(
            isinstance(item, dict)
            and str(item.get("id")) in unresolved_internal_ids
            and item.get("type") == device_type
            for item in devices
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
            existing = self._original_mappings.get(concept_name, {})
            existing_configuration = existing.get("configuration") or {}
            default = existing_configuration.get(
                "entityId"
            ) or self._selected_entities.get(
                concept_name,
                self._suggestions.get(concept_name),
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
            azimuth = self._solar_properties.get(
                "azimuthDegrees", self._original_properties.get("azimuthDegrees")
            )
            tilt = self._solar_properties.get(
                "tiltDegrees", self._original_properties.get("tiltDegrees")
            )
            azimuth_key = (
                vol.Optional(
                    CONF_AZIMUTH_DEGREES,
                    description={"suggested_value": azimuth},
                )
                if azimuth is not None
                else vol.Optional(CONF_AZIMUTH_DEGREES)
            )
            installation[azimuth_key] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=360,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°",
                )
            )
            tilt_key = (
                vol.Optional(
                    CONF_TILT_DEGREES,
                    description={"suggested_value": tilt},
                )
                if tilt is not None
                else vol.Optional(CONF_TILT_DEGREES)
            )
            installation[tilt_key] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=90,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°",
                )
            )
            grouped_fields[SECTION_INSTALLATION] = installation

        if self._editing_device is not None:
            information: dict[Any, Any] = {}
            for field, backend_key in (
                (CONF_DISPLAY_NAME, "displayName"),
                (CONF_VENDOR, "vendor"),
                (CONF_MODEL, "model"),
            ):
                value = self._original_properties.get(backend_key)
                key = (
                    vol.Optional(field, description={"suggested_value": value})
                    if value
                    else vol.Optional(field)
                )
                information[key] = selector.TextSelector()
            grouped_fields[SECTION_DEVICE_INFORMATION] = information

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
        if self._device_type is None or (
            self._editing_device is None and self._ha_device_id is None
        ):
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
                    if self._editing_device is not None:
                        return await self._save_device_changes(user_input)
                    return await self._save_device()
                except FluksValidationError:
                    errors["base"] = "invalid_input"
                except FluksUnauthorized:
                    return await self._async_require_reauth()
                except FluksCannotConnect:
                    errors["base"] = "cannot_connect"
                except FluksConflict as err:
                    errors["base"] = (
                        "device_type_already_configured"
                        if err.code == "DUPLICATE_DEVICE_CONTEXT"
                        else "identity_conflict"
                    )
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
        if self._device_id is None:
            return None
        devices = await self.api.list_devices(self.config_entry.data[CONF_SITE_ID])
        return next(
            (item for item in devices if item.get("deviceId") == self._device_id),
            None,
        )

    async def _ensure_device(self) -> dict[str, Any]:
        """Reuse the stable external identity before or after a lost response."""
        if self._device_id is None:
            raise FluksApiError("INVALID_FLOW_STATE")
        existing = await self._find_device()
        if existing is not None:
            if existing.get("type") != self._device_type:
                raise FluksConflict("DEVICE_ID_CONFLICT")
            if not self._device_creation_attempted:
                raise FluksConflict("DUPLICATE_DEVICE_CONTEXT")
            return existing
        try:
            self._device_creation_attempted = True
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

        existing = await self.api.list_mappings(
            site_id, device_id=self._backend_device_internal_id
        )
        for mapping in desired:
            if any(self._same_mapping(item, mapping) for item in existing):
                continue
            try:
                created = await self.api.create_mapping(site_id, mapping)
                existing.append(created)
            except FluksConflict:
                refreshed = await self.api.list_mappings(
                    site_id, device_id=self._backend_device_internal_id
                )
                if not any(self._same_mapping(item, mapping) for item in refreshed):
                    raise
                existing = refreshed

        return self._finish_with_ha_context()

    def _finish_with_ha_context(self):
        """Persist only local HA discovery context, never backend domain state."""
        options = dict(self.config_entry.options)
        contexts = dict(options.get(CONF_DEVICE_CONTEXTS, {}))
        if self._backend_device_internal_id and self._ha_device_id:
            contexts[self._backend_device_internal_id] = {
                CONF_HA_DEVICE_ID: self._ha_device_id,
                "type": self._device_type,
            }
        options[CONF_DEVICE_CONTEXTS] = contexts
        options.pop(CONF_DEVICE, None)
        return self.async_create_entry(title="", data=options)

    async def _save_device_changes(self, user_input: dict[str, Any]):
        """Reconcile only changed Device properties and input Mappings."""
        if self._editing_device is None or self._backend_device_internal_id is None:
            return self.async_abort(reason="invalid_flow_state")
        site_id = self.config_entry.data[CONF_SITE_ID]
        property_values = user_input.get(SECTION_DEVICE_INFORMATION, {})
        submitted_properties: dict[str, Any] = {
            backend_key: property_values.get(field) or None
            for field, backend_key in (
                (CONF_DISPLAY_NAME, "displayName"),
                (CONF_VENDOR, "vendor"),
                (CONF_MODEL, "model"),
            )
        }
        if self._device_type == "solar":
            installation = user_input.get(SECTION_INSTALLATION, {})
            submitted_properties.update(
                {
                    "azimuthDegrees": installation.get(CONF_AZIMUTH_DEGREES),
                    "tiltDegrees": installation.get(CONF_TILT_DEGREES),
                }
            )
        changed_properties = {
            key: value
            for key, value in submitted_properties.items()
            if self._original_properties.get(key) != value
            and (value is not None or key in self._original_properties)
        }
        if changed_properties:
            updated = await self.api.update_device_properties(
                site_id, self._backend_device_internal_id, changed_properties
            )
            self._editing_device = updated
            for key, value in changed_properties.items():
                if value is None:
                    self._original_properties.pop(key, None)
                else:
                    self._original_properties[key] = value

        concepts = {
            str(item["concept"]): item for item in self._mappable_concepts()
        }
        for concept_name, concept in concepts.items():
            selected = self._selected_entities.get(concept_name)
            existing = self._original_mappings.get(concept_name)
            existing_configuration = (existing or {}).get("configuration") or {}
            existing_entity = existing_configuration.get("entityId")
            if selected == existing_entity:
                continue
            if existing is not None and not selected:
                await self.api.delete_mapping(site_id, str(existing["id"]))
                self._original_mappings.pop(concept_name, None)
                continue
            if not selected:
                continue
            configuration = input_configuration(self.hass, concept, selected)
            if existing is not None:
                updated = await self.api.update_mapping(
                    site_id, str(existing["id"]), configuration
                )
                self._original_mappings[concept_name] = updated
                continue
            payload = {
                "integrationId": self.config_entry.data[
                    CONF_INTEGRATION_INTERNAL_ID
                ],
                "deviceId": self._backend_device_internal_id,
                "concept": concept_name,
                "direction": "input",
                "configuration": configuration,
            }
            created = await self.api.create_mapping(site_id, payload)
            self._original_mappings[concept_name] = created

        return self._finish_with_ha_context()

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
