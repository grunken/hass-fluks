"""Authenticated Home Assistant command boundary for the fluks config panel."""

from __future__ import annotations

import json
import logging
import math
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
    CONF_CLEARED_MAPPING_CONCEPTS,
    CONF_DEVICE,
    CONF_DEVICE_CONTEXTS,
    CONF_INTEGRATION_ID,
    CONF_INTEGRATION_INTERNAL_ID,
    CONF_INTEGRATION_KEY,
    CONF_SITE_ID,
    DATA_RUNTIME,
    DOMAIN,
)
from .control_capabilities import (
    async_control_capabilities,
    async_validate_control_configuration,
)
from .device import (
    CONF_HA_DEVICE_ID,
    SITE_DEVICE_TYPE,
    concept_label,
    device_identity,
    device_type_name,
    stable_device_id,
)
from .matcher import (
    match_entities,
    normalize_input_configuration,
    prepare_matches,
    suggestion_candidates,
)
from .observations import async_refresh_observations
from .output_mapping import OutputMappingValidationError, validate_output_configuration

_LOGGER = logging.getLogger(__name__)
_DISPLAY_NUMBER_SUFFIX = re.compile(r"\s+#\d+$")

COMMAND_CONTEXT = f"{DOMAIN}/config/context"
COMMAND_RUNTIME_STATUS = f"{DOMAIN}/config/runtime_status"
COMMAND_DEVICE_DETAIL = f"{DOMAIN}/config/device"
COMMAND_ADD_REVIEW = f"{DOMAIN}/config/add_review"
COMMAND_ADD_SAVE = f"{DOMAIN}/config/add_save"
COMMAND_DEVICE_SAVE = f"{DOMAIN}/config/device_save"
COMMAND_CONTROL_SAVE = f"{DOMAIN}/config/control_save"
COMMAND_CONTROL_SUGGESTIONS = f"{DOMAIN}/config/control_suggestions"
COMMAND_CONTROL_CAPABILITIES = f"{DOMAIN}/config/control_capabilities"
COMMAND_DEVICE_DELETE = f"{DOMAIN}/config/device_delete"
COMMAND_SITE_DELETE = f"{DOMAIN}/config/site_delete"

BASE_SCHEMA = {
    vol.Required("entry_id"): str,
}
TRANSLATIONS_DIRECTORY = Path(__file__).parent / "translations"

_FORECAST_SOLAR_DOMAIN = "forecast_solar"
_FORECAST_SOLAR_PLANE_TYPE = "plane"


class PanelCommandError(Exception):
    """A safe product-level command error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise PanelCommandError("not_found")
    if not entry.data.get(CONF_INTEGRATION_KEY):
        entry.async_start_reauth(hass)
        raise PanelCommandError("auth_required")
    return entry


def _api(hass: HomeAssistant, entry: ConfigEntry) -> FluksApiClient:
    return FluksApiClient(
        async_get_clientsession(hass),
        integration_key=entry.data[CONF_INTEGRATION_KEY],
    )


@websocket_api.websocket_command(
    {vol.Required("type"): COMMAND_RUNTIME_STATUS, **BASE_SCHEMA}
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_runtime_status(hass, connection, msg):
    """Subscribe a panel connection to the entry's runtime status."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        runtime = hass.data.get(DOMAIN, {}).get(DATA_RUNTIME, {}).get(entry.entry_id)
        if runtime is None:
            raise PanelCommandError("not_found")

        status = runtime.status
        connection.subscriptions[msg["id"]] = runtime.add_status_listener(
            lambda status: connection.send_event(msg["id"], status)
        )
        connection.send_result(msg["id"])
        connection.send_event(msg["id"], status)
    except PanelCommandError as err:
        _send_error(hass, entry, connection, msg["id"], err)


async def _catalog(api: FluksApiClient) -> dict[str, dict[str, Any]]:
    result = await api.get_device_type_catalog()
    catalog = {
        str(item["type"]): item
        for item in result
        if isinstance(item, dict)
        and isinstance(item.get("type"), str)
        and isinstance(item.get("concepts"), list)
    }
    if not catalog:
        raise PanelCommandError("backend_unavailable")
    return catalog


async def _mapping_proposals(
    hass: HomeAssistant,
    api: FluksApiClient,
    site_id: str,
    device_type: str,
    concepts: list[dict[str, Any]],
    ha_device_id: str | None,
    existing_configurations: dict[str, dict[str, Any]] | None = None,
    additional_ha_device_ids: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Replace only the matcher's final semantic choice with backend selection."""
    prepared = prepare_matches(
        hass,
        concepts,
        ha_device_id,
        existing_configurations,
        additional_device_ids=additional_ha_device_ids,
    )
    # The batch contract groups sources by owning device.  Keep the candidate
    # collection/filtering above unchanged and submit the union of those
    # already-prepared sources for this selected HA device.  A source may be
    # relevant to more than one unresolved concept, but state and attribute
    # sources remain distinct by their (entityId, attribute) identity.
    concept_names = list(prepared.definitions)
    candidate_by_source: dict[tuple[str, str | None], dict[str, Any]] = {}
    for concept in concept_names:
        candidates = suggestion_candidates(prepared, concept)
        for candidate in candidates:
            entity_id = candidate.get("entityId")
            if not isinstance(entity_id, str) or not entity_id:
                continue
            attribute = candidate.get("attribute")
            key = (entity_id, attribute if isinstance(attribute, str) else None)
            candidate_by_source.setdefault(key, candidate)

    selected_sources: dict[str, dict[str, str]] = {}
    suggestions: dict[str, dict[str, Any]] = {}
    candidate_groups_by_device: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidate_by_source.values():
        device_id = candidate.get("deviceId")
        if isinstance(device_id, str) and device_id:
            candidate_groups_by_device.setdefault(device_id, []).append(candidate)
    candidate_groups = [
        {"deviceId": device_id, "candidates": candidates}
        for device_id, candidates in sorted(candidate_groups_by_device.items())
    ]
    if concept_names and candidate_groups:
        try:
            suggestions = await api.suggest_mappings(
                site_id,
                device_type,
                concept_names,
                candidate_groups,
            )
        except FluksApiError as err:
            _LOGGER.debug("Mapping suggestions unavailable: %s", err.code or type(err).__name__)

    for concept in concept_names:
        result = suggestions.get(concept)
        if not isinstance(result, dict):
            continue
        entity_id = result.get("entityId")
        if not isinstance(entity_id, str) or not entity_id:
            continue
        attribute = result.get("attribute")
        source = {"entityId": entity_id}
        if isinstance(attribute, str) and attribute:
            source["attribute"] = attribute
        selected_sources[concept] = source
    proposals = match_entities(
        hass,
        concepts,
        ha_device_id,
        existing_configurations,
        prepared=prepared,
        selected_sources=selected_sources,
    )
    return proposals


def _mappable_concepts(catalog_item: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        concept
        for concept in catalog_item["concepts"]
        if isinstance(concept, dict)
        and isinstance(concept.get("concept"), str)
        and "fact" in concept.get("usages", [])
        and concept.get("source") == "mapping"
    ]


def _control_concepts(catalog_item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return catalog controls exposed during the Add-device review."""
    return [
        concept
        for concept in catalog_item["concepts"]
        if isinstance(concept, dict)
        and isinstance(concept.get("concept"), str)
        and "control" in concept.get("usages", [])
    ]


def _control_behaviors(concept: dict[str, Any]) -> list[str]:
    """Use the same local behavior labels as the output editor."""
    behaviors: list[str] = []
    for mode in concept.get("mappingModes") or [None]:
        behavior = "default" if mode is None else mode
        if isinstance(behavior, str) and behavior and behavior not in behaviors:
            behaviors.append(behavior)
    if concept.get("concept") == "battery.power" and concept.get("datatype") == "number":
        for behavior in ("charge", "discharge", "hold"):
            if behavior not in behaviors:
                behaviors.append(behavior)
    if concept.get("concept") == "site.power" and concept.get("datatype") == "number":
        behaviors = [behavior for behavior in behaviors if behavior != "balance"]
        behaviors.extend(
            behavior
            for behavior in ("balance:gtZero", "balance:ltZero", "balance:eqZero")
            if behavior not in behaviors
        )
    return behaviors


async def _control_action_candidates(
    hass: HomeAssistant, ha_device_id: str
) -> list[dict[str, Any]]:
    """Serialize executable capabilities limited to one selected HA device."""
    registry = er.async_get(hass)
    candidates: list[dict[str, Any]] = []
    for capability in await async_control_capabilities(hass):
        service = capability.get("service")
        if not isinstance(service, str) or "." not in service:
            continue
        for entity in capability.get("entities", []):
            entity_id = entity.get("entity_id")
            if not isinstance(entity_id, str):
                continue
            registry_entity = registry.async_get(entity_id)
            if registry_entity is None or registry_entity.device_id != ha_device_id:
                continue
            fields = {}
            for field in entity.get("fields", []):
                if not isinstance(field, dict) or not isinstance(field.get("id"), str):
                    continue
                selector = field.get("selector") or {}
                field_type = selector.get("type") if isinstance(selector, dict) else None
                fields[field["id"]] = {
                    "type": (
                        field_type
                        if field_type in {"number", "boolean", "select", "state", "text"}
                        else "string"
                    ),
                    **(
                        {"options": field["constraints"]["options"]}
                        if isinstance(field.get("constraints"), dict)
                        and isinstance(field["constraints"].get("options"), list)
                        else {}
                    ),
                }
                constraints = field.get("constraints")
                if isinstance(constraints, dict):
                    for key in ("min", "max", "step"):
                        if key in constraints and isinstance(constraints[key], (int, float)):
                            fields[field["id"]][key] = constraints[key]
            candidates.append({
                "entityId": entity_id,
                "attribute": None,
                "name": entity.get("name"),
                "metadata": entity.get("metadata"),
                "sourceType": "action",
                "domain": entity_id.split(".", 1)[0],
                "service": service,
                "actionName": capability.get("name"),
                "description": capability.get("description"),
                "target": capability.get("target"),
                "fields": fields,
            })
    return candidates


async def _output_suggestions(
    api: FluksApiClient,
    site_id: str,
    device_type: str,
    concept: str,
    behaviors: list[str],
    actions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Call the shared output-suggestion client used by Add and Edit flows."""
    if not behaviors or not actions:
        return {}
    return await api.suggest_output_mapping(
        site_id, device_type, concept, behaviors, actions
    )


def _forecast_solar_number(value: Any) -> float | None:
    """Return a finite Forecast.Solar number, or no prefill for bad data."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _forecast_solar_plane(entry: Any) -> dict[str, Any] | None:
    """Return the one unambiguous Forecast.Solar plane, if present.

    Forecast.Solar currently stores plane settings in config-entry subentries.
    Older entries stored the same fields in options, so retain that compatible
    fallback without importing or depending on that integration at runtime.
    """
    planes: list[Any] = []
    getter = getattr(entry, "get_subentries_of_type", None)
    if callable(getter):
        try:
            planes = list(getter(_FORECAST_SOLAR_PLANE_TYPE))
        except (TypeError, AttributeError):
            planes = []
    if not planes:
        subentries = getattr(entry, "subentries", None)
        if isinstance(subentries, dict):
            planes = [
                item
                for item in subentries.values()
                if getattr(item, "subentry_type", None) == _FORECAST_SOLAR_PLANE_TYPE
            ]
    if len(planes) > 1:
        return None
    if len(planes) == 1:
        data = getattr(planes[0], "data", None)
        return dict(data) if isinstance(data, dict) else None

    # Forecast.Solar config entries before plane subentries kept these values
    # in options.  Keep data as a fallback for test/older HA representations.
    data = getattr(entry, "data", None)
    options = getattr(entry, "options", None)
    combined = {}
    if isinstance(data, dict):
        combined.update(data)
    if isinstance(options, dict):
        combined.update(options)
    if any(key in combined for key in ("azimuth", "declination", "modules_power")):
        return combined
    return None


def _forecast_solar_prefill(
    hass: HomeAssistant, existing: dict[str, Any] | None = None
) -> dict[str, float]:
    """Suggest missing Solar installation properties from one Solar plane.

    A Forecast.Solar entry with several planes, or several entries, is
    intentionally treated as ambiguous.  There is no supported HA relation
    identifying which plane belongs to a Fluks Solar device, so guessing would
    be worse than leaving the normal editable fields empty.
    """
    entries = list(hass.config_entries.async_entries(_FORECAST_SOLAR_DOMAIN))
    if len(entries) != 1:
        return {}
    source = _forecast_solar_plane(entries[0])
    if source is None:
        return {}
    values: dict[str, float] = {}
    azimuth = _forecast_solar_number(source.get("azimuth"))
    if azimuth is not None and 0 <= azimuth < 360:
        values["azimuthDegrees"] = azimuth
    tilt = _forecast_solar_number(source.get("declination"))
    if tilt is not None and 0 <= tilt <= 90:
        values["tiltDegrees"] = tilt
    modules_power = _forecast_solar_number(source.get("modules_power"))
    if modules_power is not None and modules_power > 0:
        values["installedKWp"] = modules_power / 1000
    current = existing or {}
    return {
        key: value
        for key, value in values.items()
        if current.get(key) is None
    }


@lru_cache(maxsize=2)
def _load_panel_translations(language: str) -> dict[str, str]:
    """Load the custom panel namespace, which HA does not expose via localize()."""
    locale = "da" if language.lower().split("-", 1)[0] == "da" else "en"
    with (TRANSLATIONS_DIRECTORY / f"{locale}.json").open(encoding="utf-8") as file:
        panel = json.load(file).get("panel", {})
    return {str(key): str(value) for key, value in panel.items() if isinstance(value, str)}


async def _panel_translations(hass: HomeAssistant) -> dict[str, str]:
    """Return safe UI strings for the authenticated embedded panel."""
    return await hass.async_add_executor_job(
        _load_panel_translations, hass.config.language
    )


def _configuration_status(
    catalog_item: dict[str, Any],
    mappings: list[dict[str, Any]],
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize the existing input and output work for an overview row."""
    concepts = _mappable_concepts(catalog_item)
    input_concepts = {
        str(item.get("concept"))
        for item in mappings
        if isinstance(item, dict)
        if item.get("direction") == "input"
        and isinstance(item.get("concept"), str)
        and isinstance(item.get("configuration"), dict)
        and isinstance(item["configuration"].get("entityId"), str)
        and item["configuration"].get("entityId")
    }
    if catalog_item.get("type") == "heatPump":
        available = set(input_concepts)
        changed = True
        while changed:
            changed = False
            for derived, requirements in (
                ("heatPump.energy", {"heatPump.bufferEnergy", "heatPump.tankEnergy"}),
                ("heatPump.energy", {"heatPump.power"}),
                ("heatPump.power", {"heatPump.energy"}),
                ("heatPump.bufferEnergy", {"heatPump.energy", "heatPump.tankEnergy"}),
                ("heatPump.tankEnergy", {"heatPump.energy", "heatPump.bufferEnergy"}),
            ):
                if requirements <= available and derived not in available:
                    available.add(derived)
                    changed = True
        missing_mappings = []
        if not {"heatPump.power", "heatPump.energy"} & available:
            missing_mappings.append("heatPump.power")
        if not {
            "heatPump.temperature",
            "heatPump.bufferTemperature",
            "heatPump.tankTemperature",
        } & available:
            missing_mappings.append("heatPump.temperature")
    elif catalog_item.get("type") == "battery":
        available = set(input_concepts)
        changed = True
        while changed:
            changed = False
            for derived, requirements in (
                ("battery.energy", {"battery.power"}),
                ("battery.power", {"battery.energy"}),
                ("battery.energy", {"battery.chargeEnergy", "battery.dischargeEnergy"}),
                ("battery.chargeEnergy", {"battery.energy"}),
                ("battery.dischargeEnergy", {"battery.energy"}),
            ):
                if requirements <= available and derived not in available:
                    available.add(derived)
                    changed = True
        missing_mappings = []
        if "battery.soc" not in available:
            missing_mappings.append("battery.soc")
        if not {"battery.power", "battery.energy"} & available:
            missing_mappings.append("battery.power")
    elif catalog_item.get("type") == "solar":
        available = set(input_concepts)
        if "solar.power" in available:
            available.add("solar.energy")
        if "solar.energy" in available:
            available.add("solar.power")
        missing_mappings = [] if {"solar.power", "solar.energy"} & available else ["solar.power"]
    elif catalog_item.get("type") == "electricVehicle":
        available = set(input_concepts)
        changed = True
        while changed:
            changed = False
            for derived, requirements in (
                ("electricVehicle.energy", {"electricVehicle.power"}),
                ("electricVehicle.power", {"electricVehicle.energy"}),
                ("electricVehicle.energy", {"electricVehicle.chargeEnergy", "electricVehicle.dischargeEnergy"}),
                ("electricVehicle.chargeEnergy", {"electricVehicle.energy"}),
                ("electricVehicle.dischargeEnergy", {"electricVehicle.energy"}),
            ):
                if requirements <= available and derived not in available:
                    available.add(derived)
                    changed = True
        missing_mappings = []
        if "electricVehicle.soc" not in available:
            missing_mappings.append("electricVehicle.soc")
        if "electricVehicle.connected" not in available:
            missing_mappings.append("electricVehicle.connected")
        if not {"electricVehicle.power", "electricVehicle.energy"} & available:
            missing_mappings.append("electricVehicle.power")
    elif catalog_item.get("type") == "spaceHeater":
        has_temperature = "spaceHeater.temperature" in input_concepts
        has_direct_consumption = bool(
            {"spaceHeater.power", "spaceHeater.energy"} & input_concepts
        )
        rated_power = (
            properties.get("ratedPowerW") if isinstance(properties, dict) else None
        )
        try:
            has_rated_power = (
                rated_power is not None
                and Decimal(str(rated_power)).is_finite()
                and Decimal(str(rated_power)) >= 0
            )
        except (InvalidOperation, ValueError):
            has_rated_power = False
        has_state_consumption = "spaceHeater.state" in input_concepts and has_rated_power
        missing_mappings = []
        if not has_temperature:
            missing_mappings.append("spaceHeater.temperature")
        if not (has_direct_consumption or has_state_consumption):
            missing_mappings.append("spaceHeater.power")
    elif catalog_item.get("type") == "waterHeater":
        available = set(input_concepts)
        if "waterHeater.power" in available:
            available.add("waterHeater.energy")
        if "waterHeater.energy" in available:
            available.add("waterHeater.power")
        missing_mappings = []
        if "waterHeater.temperature" not in available:
            missing_mappings.append("waterHeater.temperature")
        if not {"waterHeater.power", "waterHeater.energy"} & available:
            missing_mappings.append("waterHeater.power")
    else:
        missing_mappings = [
            str(item["concept"])
            for item in concepts
            if str(item["concept"]) not in input_concepts
        ]
    controls = _control_concepts(catalog_item)
    configured_controls = {
        str(item.get("concept"))
        for item in mappings
        if isinstance(item, dict)
        if item.get("direction") == "output"
        and isinstance(item.get("concept"), str)
        and isinstance(item.get("configuration"), dict)
        and isinstance(item["configuration"].get("actions"), list)
        and item["configuration"]["actions"]
    }
    missing_controls = [
        str(item["concept"])
        for item in controls
        if str(item["concept"]) not in configured_controls
    ]
    mappings_complete = not missing_mappings
    controls_complete = not missing_controls
    if catalog_item.get("type") == "battery":
        battery_power_configured = "battery.power" in configured_controls
        controls_complete = mappings_complete and battery_power_configured
        missing_controls = [] if controls_complete else ["battery.power"]
    elif catalog_item.get("type") == "electricVehicle":
        ev_power_configured = "electricVehicle.power" in configured_controls
        controls_complete = mappings_complete and ev_power_configured
        missing_controls = [] if controls_complete else ["electricVehicle.power"]
    elif catalog_item.get("type") == "spaceHeater":
        temperature_control_configured = "spaceHeater.temperature" in configured_controls
        controls_complete = mappings_complete and temperature_control_configured
        missing_controls = [] if controls_complete else ["spaceHeater.temperature"]
    elif catalog_item.get("type") == "waterHeater":
        temperature_control_configured = "waterHeater.temperature" in configured_controls
        controls_complete = mappings_complete and temperature_control_configured
        missing_controls = [] if controls_complete else ["waterHeater.temperature"]
    return {
        "complete": mappings_complete and controls_complete,
        "mappings_complete": mappings_complete,
        "controls_complete": controls_complete,
        "missing_mappings": len(missing_mappings),
        "missing_controls": len(missing_controls),
        "control_count": len(controls),
    }


def _present_device(
    device: dict[str, Any],
    translations: dict[str, str],
    *,
    name: str | None = None,
    configuration_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Present one canonical Device without exposing backend-only fields."""
    identity = name if name is not None else device_identity(device) or ""
    type_name = device_type_name(device, translations)
    result: dict[str, Any] = {
        "id": str(device["id"]),
        "type": str(device["type"]),
        "type_name": type_name,
        "name": identity,
        "label": f"{type_name} · {identity}" if identity else type_name,
        "metadata": " · ".join(
            str((device.get("properties") or {}).get(key)).strip()
            for key in ("vendor", "model")
            if (device.get("properties") or {}).get(key)
        ),
    }
    if configuration_status is not None:
        result["configuration_status"] = configuration_status
    return result


def _present_device_names(
    devices: list[dict[str, Any]], translations: dict[str, str]
) -> dict[str, str]:
    """Return display identities, adding translated Solar direction labels."""
    directions = (
        "north",
        "northeast",
        "east",
        "southeast",
        "south",
        "southwest",
        "west",
        "northwest",
    )
    names: dict[str, str] = {}
    duplicate_groups: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        internal_id = device.get("id")
        if not isinstance(internal_id, str):
            continue
        identity = device_identity(device) or ""
        identity = _DISPLAY_NUMBER_SUFFIX.sub("", identity).strip()
        properties = device.get("properties") or {}
        azimuth = properties.get("azimuthDegrees")
        if (
            device.get("type") == "solar"
            and isinstance(azimuth, (int, float))
            and not isinstance(azimuth, bool)
            and math.isfinite(azimuth)
            and 0 <= azimuth < 360
        ):
            direction = directions[int((azimuth + 22.5) // 45) % len(directions)]
            localized_direction = translations.get(
                f"compass_{direction}", direction.title()
            )
            identity = (
                f"{identity} · {localized_direction}"
                if identity
                else localized_direction
            )
        displayed_name = (
            f"{device_type_name(device, translations)} · {identity}"
            if identity
            else device_type_name(device, translations)
        )
        duplicate_groups.setdefault(displayed_name, []).append(device)
        names[internal_id] = identity

    for displayed_name, group in duplicate_groups.items():
        if len(group) < 2:
            continue
        group.sort(
            key=lambda item: (
                str(item.get("createdAt") or ""),
                str(item.get("deviceId") or ""),
                str(item.get("id") or ""),
            )
        )
        for index, device in enumerate(group, start=1):
            identity = names[str(device["id"])]
            names[str(device["id"])] = (
                f"{identity} #{index}" if identity else f"#{index}"
            )
    return names


async def _present_devices(
    hass: HomeAssistant,
    devices: list[dict[str, Any]],
    configuration_statuses: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    translations = await _panel_translations(hass)
    physical = [item for item in devices if item.get("type") != SITE_DEVICE_TYPE]
    names = _present_device_names(physical, translations)
    physical.sort(
        key=lambda item: (
            device_type_name(item, translations).casefold(),
            (device_identity(item) or "").casefold(),
        )
    )
    return [
        _present_device(
            item,
            translations,
            name=names.get(str(item["id"]), ""),
            configuration_status=(configuration_statuses or {}).get(str(item["id"])),
        )
        for item in physical
        if isinstance(item.get("id"), str) and isinstance(item.get("type"), str)
    ]


def _ha_context(entry: ConfigEntry, internal_id: str) -> str | None:
    contexts = entry.options.get(CONF_DEVICE_CONTEXTS, {})
    context = contexts.get(internal_id, {}) if isinstance(contexts, dict) else {}
    value = context.get(CONF_HA_DEVICE_ID)
    if isinstance(value, str):
        return value
    legacy = entry.options.get(CONF_DEVICE, {})
    if isinstance(legacy, dict) and legacy.get("internal_id") == internal_id:
        value = legacy.get(CONF_HA_DEVICE_ID)
        return value if isinstance(value, str) else None
    return None


def _site_mapping_candidate_device_ids(
    entry: ConfigEntry, devices: list[dict[str, Any]]
) -> list[str]:
    """Return HA devices already selected for Solar/Battery Site context."""
    result = []
    for device in devices:
        if device.get("type") not in {"solar", "battery"}:
            continue
        device_id = device.get("id")
        if not isinstance(device_id, str):
            continue
        ha_device_id = _ha_context(entry, device_id)
        if ha_device_id and ha_device_id not in result:
            result.append(ha_device_id)
    return result


def _cleared_mapping_concepts(entry: ConfigEntry, internal_id: str) -> set[str]:
    """Return matcher proposals the user explicitly cleared."""
    contexts = entry.options.get(CONF_DEVICE_CONTEXTS, {})
    context = contexts.get(internal_id, {}) if isinstance(contexts, dict) else {}
    values = context.get(CONF_CLEARED_MAPPING_CONCEPTS, [])
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str)}


def _submitted_clears(submitted: dict[str, Any]) -> set[str]:
    """Find explicit empty selections in the existing mappings payload."""
    return {
        str(concept)
        for concept, configuration in submitted.items()
        if isinstance(configuration, dict) and configuration.get("entityId") == ""
    }


def _updated_clears(
    current: set[str], submitted: dict[str, Any], selected: dict[str, Any]
) -> list[str]:
    """Keep explicit clears until the user maps that concept again."""
    return sorted((current | _submitted_clears(submitted)) - selected.keys())


def _safe_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(mapping["id"]),
        "concept": str(mapping["concept"]),
        "mode": mapping.get("mode"),
        "valueCondition": mapping.get("valueCondition"),
        "configuration": dict(mapping.get("configuration") or {}),
    }


async def _migrate_legacy_mappings(
    api: FluksApiClient,
    site_id: str,
    device_id: str,
    integration_id: str,
    mappings: list[dict[str, Any]],
    legacy_concept: str,
    canonical_concept: str,
) -> list[dict[str, Any]]:
    """Move one legacy canonical Mapping name to its replacement."""
    legacy = [
        item
        for item in mappings
        if item.get("concept") == legacy_concept
        and item.get("direction") in {"input", "output"}
    ]
    if not legacy:
        return mappings
    for item in legacy:
        replacement = next(
            (
                candidate
                for candidate in mappings
                if candidate.get("concept") == canonical_concept
                and candidate.get("direction") == item.get("direction")
                and candidate.get("mode") == item.get("mode")
            ),
            None,
        )
        if replacement is None:
            payload = {
                "integrationId": integration_id,
                "deviceId": device_id,
                "concept": canonical_concept,
                "direction": item["direction"],
                "configuration": dict(item.get("configuration") or {}),
            }
            if item.get("direction") == "output":
                payload["mode"] = item.get("mode")
                if "valueCondition" in item:
                    payload["valueCondition"] = item.get("valueCondition")
            await api.create_mapping(site_id, payload)
        await api.delete_mapping(site_id, str(item["id"]))
    return await api.list_mappings(site_id, device_id=device_id)


def _send_error(
    hass: HomeAssistant,
    entry: ConfigEntry | None,
    connection: websocket_api.ActiveConnection,
    msg_id: int,
    err: Exception,
    *,
    recover_integration_key: bool = True,
) -> None:
    if isinstance(err, PanelCommandError):
        code = err.code
    elif isinstance(err, FluksInvalidCredentials):
        code = "invalid_auth"
    elif isinstance(err, FluksNotFound):
        code = "forbidden_or_missing"
    elif isinstance(err, FluksUnauthorized):
        code = "auth_required"
        if entry is not None and recover_integration_key:
            entry.async_start_reauth(hass)
    elif isinstance(err, FluksValidationError):
        code = "validation_error"
    elif isinstance(err, FluksConflict):
        code = "conflict"
    elif isinstance(err, FluksCannotConnect):
        code = "backend_unavailable"
    else:
        code = "unknown"
    connection.send_error(msg_id, code, "The fluks operation could not be completed")


def _present_concepts(
    concepts: list[dict[str, Any]], translations: dict[str, str]
) -> list[dict[str, Any]]:
    return [dict(concept, label=concept_label(concept, translations)) for concept in concepts]


@websocket_api.websocket_command(
    {vol.Required("type"): COMMAND_CONTEXT, **BASE_SCHEMA}
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_context(hass, connection, msg):
    """Return safe Site, Device, catalog, and HA discovery presentation data."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        api = _api(hass, entry)
        devices, catalog = await api.list_devices(entry.data[CONF_SITE_ID]), await _catalog(api)
        mappings = await api.list_mappings(entry.data[CONF_SITE_ID])
        mappings_by_device: dict[str, list[dict[str, Any]]] = {}
        for mapping in mappings:
            device_id = mapping.get("deviceId")
            if isinstance(device_id, str):
                mappings_by_device.setdefault(device_id, []).append(mapping)
        configuration_statuses = {
            str(device["id"]): _configuration_status(
                catalog[str(device["type"])],
                mappings_by_device.get(str(device["id"]), []),
                device.get("properties"),
            )
            for device in devices
            if isinstance(device.get("id"), str) and str(device.get("type")) in catalog
        }
        translations = await _panel_translations(hass)
        site_device = next(
            item
            for item in devices
            if item.get("type") == SITE_DEVICE_TYPE
            and isinstance(item.get("id"), str)
        )
        types = [
            {
                "type": device_type,
                "name": device_type_name({"type": device_type}, translations),
            }
            for device_type in catalog
            if device_type != SITE_DEVICE_TYPE
        ]
        registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)
        ha_devices = [
            {
                "id": item.id,
                "name": item.name_by_user or item.name or item.id,
                "manufacturer": item.manufacturer,
                "model": item.model,
            }
            for item in registry.devices.values()
        ]
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry.entry_id,
                "translations": await _panel_translations(hass),
                "site": _present_device(
                    site_device,
                    translations,
                    name=entry.title.strip() or "Site",
                    configuration_status=configuration_statuses.get(str(site_device["id"])),
                ),
                "devices": await _present_devices(hass, devices, configuration_statuses),
                "device_types": sorted(types, key=lambda item: item["name"].casefold()),
                "ha_devices": sorted(ha_devices, key=lambda item: item["name"].casefold()),
                "entities": _present_entities(hass, entity_registry),
            },
        )
    except (PanelCommandError, FluksApiError) as err:
        _send_error(hass, entry, connection, msg["id"], err)


def _present_entities(hass: HomeAssistant, registry) -> list[dict[str, Any]]:
    """Return safe local entity-picker metadata; never backend credentials."""
    result = []
    for item in registry.entities.values():
        if item.disabled:
            continue
        state = hass.states.get(item.entity_id)
        result.append(
            {
                "entity_id": item.entity_id,
                "device_id": item.device_id,
                "name": (
                    (state.attributes.get("friendly_name") if state else None)
                    or item.name
                    or item.original_name
                    or item.entity_id
                ),
                "domain": item.entity_id.split(".", 1)[0],
                "device_class": item.original_device_class,
            }
        )
    return result


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND_DEVICE_DETAIL,
        **BASE_SCHEMA,
        vol.Required("device_id"): str,
        vol.Optional("suggestions", default=False): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_device_detail(hass, connection, msg):
    """Load one Device with only this Integration's editable input mappings."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        api = _api(hass, entry)
        site_id = entry.data[CONF_SITE_ID]
        device_id = msg["device_id"]
        device = await api.get_device(site_id, device_id)
        catalog = await _catalog(api)
        device_type = str(device.get("type"))
        if device_type not in catalog:
            raise PanelCommandError("not_found")
        mappings = await api.list_mappings(site_id, device_id=device_id)
        if device_type == "waterHeater":
            mappings = await _migrate_legacy_mappings(
                api,
                site_id,
                device_id,
                entry.data[CONF_INTEGRATION_INTERNAL_ID],
                mappings,
                "waterHeater.targetTemperature",
                "waterHeater.temperature",
            )
        existing = {
            str(item["concept"]): item
            for item in mappings
            if item.get("direction") == "input"
            and isinstance(item.get("concept"), str)
        }
        output_mappings: dict[str, list[dict[str, Any]]] = {}
        for item in mappings:
            if item.get("direction") == "output" and isinstance(item.get("concept"), str):
                output_mappings.setdefault(str(item["concept"]), []).append(item)
        concepts = _mappable_concepts(catalog[device_type])
        ha_device_id = _ha_context(entry, device_id)
        additional_ha_device_ids: list[str] = []
        if msg.get("suggestions") and device_type == SITE_DEVICE_TYPE:
            additional_ha_device_ids = _site_mapping_candidate_device_ids(
                entry, await api.list_devices(site_id)
            )
        cleared = _cleared_mapping_concepts(entry, device_id)
        missing = [
            item
            for item in concepts
            if item["concept"] not in existing and item["concept"] not in cleared
        ]
        proposals = (
            await _mapping_proposals(
                hass,
                api,
                site_id,
                device_type,
                missing,
                ha_device_id,
                {
                    name: dict(mapping.get("configuration") or {})
                    for name, mapping in existing.items()
                },
                additional_ha_device_ids=additional_ha_device_ids,
            )
            if msg.get("suggestions") and (ha_device_id or additional_ha_device_ids)
            else {}
        )
        translations = await _panel_translations(hass)
        properties = dict(device.get("properties") or {})
        display_identity = device_identity(device) or ""
        if device_type != SITE_DEVICE_TYPE:
            all_devices = await api.list_devices(site_id)
            display_identity = _present_device_names(all_devices, translations).get(
                str(device["id"]), display_identity
            )
        display_label = (
            f"{device_type_name(device, translations)} · {display_identity}"
            if display_identity
            else device_type_name(device, translations)
        )
        connection.send_result(
            msg["id"],
            {
                "id": str(device["id"]),
                "type": device_type,
                "type_name": device_type_name(device, translations),
                "name": (
                    entry.title.strip() or "Site"
                    if device_type == SITE_DEVICE_TYPE
                    else display_identity
                ),
                "label": (
                    entry.title.strip() or "Site"
                    if device_type == SITE_DEVICE_TYPE
                    else display_label
                ),
                "properties": properties,
                "property_suggestions": (
                    _forecast_solar_prefill(hass, properties)
                    if device_type == "solar"
                    else {}
                ),
                "ha_device_id": ha_device_id,
                "configuration_status": _configuration_status(
                    catalog[device_type], mappings, properties
                ),
                "concepts": _present_concepts(concepts, translations),
                "controls": _present_concepts(_control_concepts(catalog[device_type]), translations),
                "mappings": {
                    name: _safe_mapping(mapping) for name, mapping in existing.items()
                },
                "output_mappings": {
                    name: [_safe_mapping(mapping) for mapping in mappings]
                    for name, mappings in output_mappings.items()
                },
                "proposals": proposals,
            },
        )
    except (PanelCommandError, FluksApiError) as err:
        _send_error(hass, entry, connection, msg["id"], err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND_ADD_REVIEW,
        **BASE_SCHEMA,
        vol.Required("device_type"): str,
        vol.Required("ha_device_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_add_review(hass, connection, msg):
    """Validate the selection and return fact and control review suggestions."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        registry_device = dr.async_get(hass).async_get(msg["ha_device_id"])
        if registry_device is None:
            raise PanelCommandError("validation_error")
        api = _api(hass, entry)
        catalog = await _catalog(api)
        if msg["device_type"] not in catalog or msg["device_type"] == SITE_DEVICE_TYPE:
            raise PanelCommandError("validation_error")
        concepts = _mappable_concepts(catalog[msg["device_type"]])
        controls = _control_concepts(catalog[msg["device_type"]])
        properties = {
            "displayName": registry_device.name_by_user or registry_device.name,
            "vendor": registry_device.manufacturer,
            "model": registry_device.model,
        }
        if msg["device_type"] == "solar":
            properties.update(_forecast_solar_prefill(hass))
        translations = await _panel_translations(hass)
        connection.send_result(
            msg["id"],
            {
                "concepts": _present_concepts(concepts, translations),
                "controls": _present_concepts(controls, translations),
                "control_suggestions": {},
                "proposals": (
                    await _mapping_proposals(
                        hass,
                        api,
                        entry.data[CONF_SITE_ID],
                        msg["device_type"],
                        concepts,
                        msg["ha_device_id"],
                    )
                    if concepts
                    else {}
                ),
                "properties": properties,
            },
        )
    except (PanelCommandError, FluksApiError) as err:
        _send_error(hass, entry, connection, msg["id"], err)


def _validate_selected(
    concepts: list[dict[str, Any]], selected: dict[str, Any]
) -> dict[str, str]:
    allowed = {str(item["concept"]) for item in concepts}
    if not isinstance(selected, dict) or any(key not in allowed for key in selected):
        raise PanelCommandError("invalid_mapping")
    return {
        str(key): str(value)
        for key, value in selected.items()
        if isinstance(value, str) and value
    }


def _validate_input_mappings(
    hass: HomeAssistant,
    concepts: list[dict[str, Any]],
    submitted: dict[str, Any],
    existing: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    allowed = {str(item["concept"]): item for item in concepts}
    if not isinstance(submitted, dict) or any(key not in allowed for key in submitted):
        raise PanelCommandError("invalid_mapping")
    try:
        normalized = {}
        for key, value in submitted.items():
            if not isinstance(value, dict) or not value.get("entityId"):
                continue
            current = (existing or {}).get(str(key))
            normalized[str(key)] = normalize_input_configuration(
                hass,
                allowed[str(key)],
                value,
                preserve_legacy_unit_behavior=(
                    current is not None
                    and "unit" not in current
                    and value == current
                ),
            )
        return normalized
    except ValueError as err:
        raise PanelCommandError("invalid_mapping") from err


def _editable_property_keys(device_type: str) -> set[str]:
    """Return the documented writable properties exposed by this panel."""
    keys = {"displayName", "vendor", "model"}
    if device_type == "solar":
        keys.update({"installedKWp", "azimuthDegrees", "tiltDegrees"})
    elif device_type == "spaceHeater":
        keys.add("ratedPowerW")
    elif device_type == "battery":
        keys.update({"capacityKwh", "battery.socMinimum", "battery.socMaximum"})
    elif device_type == SITE_DEVICE_TYPE:
        keys.update({"arbitrageEnabled", "arbitrageMinimumReturnPercentage"})
    return keys


def _device_properties(
    hass, ha_device_id: str, device_type: str, submitted: dict[str, Any]
) -> dict[str, Any]:
    device = dr.async_get(hass).async_get(ha_device_id)
    properties = {
        key: value
        for key, value in submitted.items()
        if key in _editable_property_keys(device_type) and value is not None
    }
    if device is not None:
        properties.setdefault("displayName", device.name_by_user or device.name)
        properties.setdefault("vendor", device.manufacturer)
        properties.setdefault("model", device.model)
    return {key: value for key, value in properties.items() if value is not None}


def _validate_solar_properties(device_type: str, properties: dict[str, Any]) -> None:
    if device_type != "solar" and any(
        key in properties for key in ("installedKWp", "azimuthDegrees", "tiltDegrees")
    ):
        raise PanelCommandError("validation_error")
    installed = properties.get("installedKWp")
    azimuth = properties.get("azimuthDegrees")
    tilt = properties.get("tiltDegrees")
    if installed is not None and (
        not isinstance(installed, (int, float))
        or isinstance(installed, bool)
        or installed <= 0
    ):
        raise PanelCommandError("validation_error")
    if azimuth is not None and (
        not isinstance(azimuth, (int, float)) or not 0 <= azimuth < 360
    ):
        raise PanelCommandError("validation_error")
    if tilt is not None and (
        not isinstance(tilt, (int, float)) or not 0 <= tilt <= 90
    ):
        raise PanelCommandError("validation_error")


def _validate_battery_properties(device_type: str, properties: dict[str, Any]) -> None:
    """Validate only the backend-documented Battery physical constraints."""
    battery_keys = {"capacityKwh", "battery.socMinimum", "battery.socMaximum"}
    if device_type != "battery" and any(key in properties for key in battery_keys):
        raise PanelCommandError("validation_error")
    capacity = properties.get("capacityKwh")
    if capacity is not None and (
        not isinstance(capacity, (int, float)) or isinstance(capacity, bool) or capacity <= 0
    ):
        raise PanelCommandError("validation_error")
    for key in ("battery.socMinimum", "battery.socMaximum"):
        value = properties.get(key)
        if value is not None and (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0 <= value <= 100
        ):
            raise PanelCommandError("validation_error")


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND_ADD_SAVE,
        **BASE_SCHEMA,
        vol.Required("device_type"): str,
        vol.Required("ha_device_id"): str,
        vol.Required("instance_id"): str,
        vol.Required("mappings"): dict,
        vol.Optional("properties", default={}): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_add_save(hass, connection, msg):
    """Create one deterministic Device and only confirmed input mappings."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        api = _api(hass, entry)
        catalog = await _catalog(api)
        if msg["device_type"] not in catalog or msg["device_type"] == SITE_DEVICE_TYPE:
            raise PanelCommandError("validation_error")
        concepts = _mappable_concepts(catalog[msg["device_type"]])
        selected = _validate_input_mappings(hass, concepts, msg["mappings"])
        _validate_solar_properties(msg["device_type"], msg["properties"])
        _validate_battery_properties(msg["device_type"], msg["properties"])
        try:
            instance_id = str(UUID(msg["instance_id"]))
        except (TypeError, ValueError, AttributeError) as err:
            raise PanelCommandError("validation_error") from err
        external_id = stable_device_id(
            entry.data[CONF_INTEGRATION_ID],
            msg["device_type"],
            msg["ha_device_id"],
            instance_id,
        )
        site_id = entry.data[CONF_SITE_ID]
        devices = await api.list_devices(site_id)
        existing = next((item for item in devices if item.get("deviceId") == external_id), None)
        if existing is None:
            try:
                device = await api.create_device(
                    site_id,
                    external_id,
                    msg["device_type"],
                    _device_properties(
                        hass,
                        msg["ha_device_id"],
                        msg["device_type"],
                        msg["properties"],
                    ),
                )
            except FluksConflict:
                refreshed = await api.list_devices(site_id)
                device = next(
                    (item for item in refreshed if item.get("deviceId") == external_id),
                    None,
                )
                if device is None or device.get("type") != msg["device_type"]:
                    raise
        elif existing.get("type") == msg["device_type"]:
            device = existing
        else:
            raise PanelCommandError("conflict")
        internal_id = str(device["id"])
        existing_mappings = await api.list_mappings(site_id, device_id=internal_id)
        by_name = {item.get("concept"): item for item in existing_mappings}
        for concept, configuration in selected.items():
            payload = {
                "integrationId": entry.data[CONF_INTEGRATION_INTERNAL_ID],
                "deviceId": internal_id,
                "concept": concept,
                "direction": "input",
                "configuration": configuration,
            }
            if concept not in by_name:
                await api.create_mapping(site_id, payload)
        options = dict(entry.options)
        updated_contexts = dict(options.get(CONF_DEVICE_CONTEXTS, {}))
        device_context = {
            CONF_HA_DEVICE_ID: msg["ha_device_id"],
            "type": msg["device_type"],
            "instance_id": instance_id,
        }
        if cleared := _updated_clears(set(), msg["mappings"], selected):
            device_context[CONF_CLEARED_MAPPING_CONCEPTS] = cleared
        updated_contexts[internal_id] = device_context
        options[CONF_DEVICE_CONTEXTS] = updated_contexts
        options.pop(CONF_DEVICE, None)
        hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(msg["id"], {"device_id": internal_id})
        await async_refresh_observations(hass, entry.entry_id)
    except (PanelCommandError, FluksApiError) as err:
        _send_error(hass, entry, connection, msg["id"], err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND_DEVICE_SAVE,
        **BASE_SCHEMA,
        vol.Required("device_id"): str,
        vol.Required("mappings"): dict,
        vol.Required("properties"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_device_save(hass, connection, msg):
    """Incrementally reconcile one Device and its Integration-owned mappings."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        api = _api(hass, entry)
        site_id = entry.data[CONF_SITE_ID]
        device = await api.get_device(site_id, msg["device_id"])
        catalog = await _catalog(api)
        device_type = str(device.get("type"))
        if device_type not in catalog:
            raise PanelCommandError("not_found")
        concepts = _mappable_concepts(catalog[device_type])
        mappings = await api.list_mappings(site_id, device_id=msg["device_id"])
        current_inputs = {
            str(item["concept"]): dict(item.get("configuration") or {})
            for item in mappings
            if item.get("direction") == "input"
            and isinstance(item.get("concept"), str)
        }
        selected = _validate_input_mappings(
            hass, concepts, msg["mappings"], current_inputs
        )
        original_properties = dict(device.get("properties") or {})
        submitted = {
            key: value
            for key, value in msg["properties"].items()
            if key in _editable_property_keys(device_type)
        }
        _validate_solar_properties(device_type, submitted)
        _validate_battery_properties(device_type, submitted)
        changed = {
            key: value
            for key, value in submitted.items()
            if original_properties.get(key) != value
            and (value is not None or key in original_properties)
        }
        updated_device = device
        if changed:
            updated_device = await api.update_device_properties(
                site_id, msg["device_id"], changed
            )
        if device_type == "waterHeater":
            mappings = await _migrate_legacy_mappings(
                api,
                site_id,
                msg["device_id"],
                entry.data[CONF_INTEGRATION_INTERNAL_ID],
                mappings,
                "waterHeater.targetTemperature",
                "waterHeater.temperature",
            )
        existing = {
            str(item["concept"]): item
            for item in mappings
            if item.get("direction") == "input" and isinstance(item.get("concept"), str)
        }
        definitions = {str(item["concept"]): item for item in concepts}
        for concept, definition in definitions.items():
            configuration = selected.get(concept)
            mapping = existing.get(concept)
            current = (mapping or {}).get("configuration") or {}
            if configuration == current:
                continue
            if mapping is not None and configuration is None:
                await api.delete_mapping(site_id, str(mapping["id"]))
            elif configuration is not None:
                if mapping is not None:
                    await api.update_mapping(site_id, str(mapping["id"]), configuration)
                else:
                    await api.create_mapping(
                        site_id,
                        {
                            "integrationId": entry.data[CONF_INTEGRATION_INTERNAL_ID],
                            "deviceId": msg["device_id"],
                            "concept": concept,
                            "direction": "input",
                            "configuration": configuration,
                        },
                    )
        current_clears = _cleared_mapping_concepts(entry, msg["device_id"])
        cleared = _updated_clears(
            current_clears,
            msg["mappings"],
            selected,
        )
        if set(cleared) != current_clears:
            options = dict(entry.options)
            contexts = dict(options.get(CONF_DEVICE_CONTEXTS, {}))
            context = dict(contexts.get(msg["device_id"], {}))
            if cleared:
                context[CONF_CLEARED_MAPPING_CONCEPTS] = cleared
            else:
                context.pop(CONF_CLEARED_MAPPING_CONCEPTS, None)
            contexts[msg["device_id"]] = context
            options[CONF_DEVICE_CONTEXTS] = contexts
            hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(
            msg["id"],
            {"properties": dict(updated_device.get("properties") or {})},
        )
        await async_refresh_observations(hass, entry.entry_id)
    except (PanelCommandError, FluksApiError) as err:
        _send_error(hass, entry, connection, msg["id"], err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND_CONTROL_SAVE,
        **BASE_SCHEMA,
        vol.Required("device_id"): str,
        vol.Required("concept"): str,
        vol.Required("behaviors"): list,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_control_save(hass, connection, msg):
    """Incrementally persist mode-specific Mappings for one catalog control."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        api = _api(hass, entry)
        site_id = entry.data[CONF_SITE_ID]
        device = await api.get_device(site_id, msg["device_id"])
        catalog = await _catalog(api)
        device_type = str(device.get("type"))
        definition = next((item for item in catalog.get(device_type, {}).get("concepts", [])
            if isinstance(item, dict) and item.get("concept") == msg["concept"]
            and "control" in item.get("usages", [])), None)
        if definition is None:
            raise PanelCommandError("not_found")
        allowed_modes = set(definition.get("mappingModes") or [None])
        allowed_modes.add(None)
        if msg["concept"] == "battery.power" and definition.get("datatype") == "number":
            # Charge/discharge/hold are local HACS execution behaviors. They
            # are selected from the signed value and are not backend modes.
            allowed_modes.update({"charge", "discharge", "hold"})
        mappings = await api.list_mappings(site_id, device_id=msg["device_id"])
        existing = [item for item in mappings
            if item.get("direction") == "output" and item.get("concept") == msg["concept"]]
        submitted: list[tuple[str | None, str | None, dict[str, Any]]] = []
        valid_value_conditions = {None, "gtZero", "ltZero", "eqZero"}
        for behavior in msg["behaviors"]:
            if not isinstance(behavior, dict) or not set(behavior).issubset(
                {"mode", "configuration", "valueCondition"}
            ) or "mode" not in behavior or "configuration" not in behavior:
                raise PanelCommandError("invalid_mapping")
            mode = behavior["mode"]
            value_condition = behavior.get("valueCondition")
            if mode not in allowed_modes or value_condition not in valid_value_conditions:
                raise PanelCommandError("invalid_mapping")
            if value_condition is not None and not (
                definition.get("datatype") == "number"
                and str(msg["concept"]).endswith(".power")
                and mode == "balance"
            ):
                raise PanelCommandError("invalid_mapping")
            identity = (mode, value_condition)
            if any(item[:2] == identity for item in submitted):
                raise PanelCommandError("invalid_mapping")
            configuration = validate_output_configuration(behavior["configuration"])
            if not await async_validate_control_configuration(hass, configuration):
                raise PanelCommandError("invalid_mapping")
            submitted.append((mode, value_condition, configuration))
        changed = False
        unmatched = list(existing)
        for mode, value_condition, configuration in submitted:
            mapping = next(
                (
                    item for item in unmatched
                    if item.get("mode") == mode
                    and item.get("valueCondition") == value_condition
                ),
                None,
            )
            if mapping is None:
                mapping = next((item for item in unmatched if item.get("mode") == mode), None)
            if mapping is not None:
                unmatched.remove(mapping)
                condition_changed = mapping.get("valueCondition") != value_condition
                if condition_changed or configuration != mapping.get("configuration"):
                    if condition_changed:
                        await api.update_mapping(
                            site_id,
                            str(mapping["id"]),
                            configuration,
                            value_condition=value_condition,
                        )
                    else:
                        await api.update_mapping(site_id, str(mapping["id"]), configuration)
                    changed = True
                continue
            await api.create_mapping(site_id, {
                "integrationId": entry.data[CONF_INTEGRATION_INTERNAL_ID],
                "deviceId": msg["device_id"], "concept": msg["concept"],
                "direction": "output", "mode": mode, "configuration": configuration,
                **({"valueCondition": value_condition} if value_condition is not None else {}),
            }); changed = True
        for mapping in unmatched:
            await api.delete_mapping(site_id, str(mapping["id"])); changed = True
        connection.send_result(msg["id"], {"changed": changed})
    except OutputMappingValidationError:
        _send_error(
            hass, entry, connection, msg["id"], PanelCommandError("invalid_mapping")
        )
    except (PanelCommandError, FluksApiError) as err:
        _send_error(hass, entry, connection, msg["id"], err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND_CONTROL_SUGGESTIONS,
        **BASE_SCHEMA,
        vol.Required("device_id"): str,
        vol.Required("concept"): str,
        vol.Required("behaviors"): list,
        vol.Required("actions"): list,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_control_suggestions(hass, connection, msg):
    """Return advisory output configurations for one existing control draft."""
    entry = None
    log_context = {
        "device_id": msg.get("device_id"),
        "concept": msg.get("concept"),
        "entry_id": msg.get("entry_id"),
    }
    try:
        entry = _entry(hass, msg["entry_id"])
        api = _api(hass, entry)
        site_id = entry.data[CONF_SITE_ID]
        device = await api.get_device(site_id, msg["device_id"])
        catalog = await _catalog(api)
        device_type = str(device.get("type"))
        definition = next(
            (
                item
                for item in catalog.get(device_type, {}).get("concepts", [])
                if isinstance(item, dict)
                and item.get("concept") == msg["concept"]
                and "control" in item.get("usages", [])
            ),
            None,
        )
        if definition is None or not all(
            isinstance(item, str) and item for item in msg["behaviors"]
        ) or not all(isinstance(item, dict) for item in msg["actions"]):
            _LOGGER.warning(
                "[fluks mapping suggestions] stage=backend_request_rejected "
                "reason=invalid_control_request context=%s",
                log_context,
            )
            raise PanelCommandError("invalid_mapping")
        suggestions = await _output_suggestions(
            api,
            site_id,
            device_type,
            str(msg["concept"]),
            msg["behaviors"],
            msg["actions"],
        )
        connection.send_result(msg["id"], {"suggestions": suggestions})
    except (PanelCommandError, FluksApiError) as err:
        _LOGGER.warning(
            "[fluks mapping suggestions] stage=backend_flow_error "
            "context=%s error=%s",
            log_context,
            getattr(err, "code", str(err)),
        )
        _send_error(hass, entry, connection, msg["id"], err)


@websocket_api.websocket_command(
    {vol.Required("type"): COMMAND_CONTROL_CAPABILITIES, **BASE_SCHEMA}
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_control_capabilities(hass, connection, msg):
    """Return safe, normalized Home Assistant action capabilities."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        connection.send_result(
            msg["id"], {"actions": await async_control_capabilities(hass)}
        )
    except PanelCommandError as err:
        _send_error(hass, entry, connection, msg["id"], err)


async def _human_api(hass, entry, email: str, password: str) -> FluksApiClient:
    api = _api(hass, entry)
    token, _ = await api.login(email, password)
    api.set_human_access_token(token)
    return api


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND_DEVICE_DELETE,
        **BASE_SCHEMA,
        vol.Required("device_id"): str,
        vol.Required("email"): str,
        vol.Required("password"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_device_delete(hass, connection, msg):
    """Human-authenticate and lifecycle-delete exactly one selected Device."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        api = await _human_api(hass, entry, msg["email"], msg["password"])
        try:
            await api.delete_device(entry.data[CONF_SITE_ID], msg["device_id"])
        finally:
            api.set_human_access_token(None)
        options = dict(entry.options)
        contexts = dict(options.get(CONF_DEVICE_CONTEXTS, {}))
        contexts.pop(msg["device_id"], None)
        options[CONF_DEVICE_CONTEXTS] = contexts
        options.pop(CONF_DEVICE, None)
        hass.config_entries.async_update_entry(entry, options=options)
        connection.send_result(msg["id"], {})
        await async_refresh_observations(hass, entry.entry_id)
    except (PanelCommandError, FluksApiError) as err:
        _send_error(
            hass, entry, connection, msg["id"], err,
            recover_integration_key=False,
        )


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND_SITE_DELETE,
        **BASE_SCHEMA,
        vol.Required("email"): str,
        vol.Required("password"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_site_delete(hass, connection, msg):
    """Human-authenticate, delete one Site, then remove only its ConfigEntry."""
    entry = None
    try:
        entry = _entry(hass, msg["entry_id"])
        api = await _human_api(hass, entry, msg["email"], msg["password"])
        try:
            await api.delete_site(entry.data[CONF_SITE_ID])
        finally:
            api.set_human_access_token(None)
        await hass.config_entries.async_remove(entry.entry_id)
        connection.send_result(msg["id"], {"entry_removed": True})
    except (PanelCommandError, FluksApiError) as err:
        _send_error(
            hass, entry, connection, msg["id"], err,
            recover_integration_key=False,
        )


COMMANDS = (
    websocket_context,
    websocket_runtime_status,
    websocket_device_detail,
    websocket_add_review,
    websocket_add_save,
    websocket_device_save,
    websocket_control_save,
    websocket_control_suggestions,
    websocket_control_capabilities,
    websocket_device_delete,
    websocket_site_delete,
)


def async_register_panel_commands(hass: HomeAssistant) -> None:
    """Register the finite product command surface once for this HA process."""
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
