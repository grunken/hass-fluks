"""Tests for the native Milestone 2 Add Device Options Flow."""

import json
from pathlib import Path
import re
from unittest.mock import AsyncMock, MagicMock, call, patch

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluks.api import (
    FluksApiClient,
    FluksCannotConnect,
    FluksConflict,
    FluksInvalidCredentials,
    FluksNotFound,
    FluksUnauthorized,
)
from custom_components.fluks.const import (
    CONF_DEVICE,
    CONF_DEVICE_CONTEXTS,
    CONF_INTEGRATION_KEY,
    DOMAIN,
)
from custom_components.fluks.options_flow import (
    CONF_AZIMUTH_DEGREES,
    CONF_HA_DEVICE_ID,
    CONF_TILT_DEGREES,
    FluksOptionsFlow,
    SECTION_ENERGY,
    SECTION_INSTALLATION,
    SECTION_MEASUREMENTS,
    stable_device_id,
)

SITE_ID = "00000000-0000-0000-0000-000000000001"
INTEGRATION_INTERNAL_ID = "00000000-0000-0000-0000-000000000002"
DEVICE_INTERNAL_ID = "00000000-0000-0000-0000-000000000003"
EXTERNAL_DEVICE_ID = "00000000-0000-0000-0000-000000000004"
INTEGRATION_KEY = "fluks_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ"

BATTERY_CATALOG = [
    {
        "type": "site",
        "concepts": [{"concept": "site.power", "datatype": "number"}],
    },
    {
        "type": "battery",
        "concepts": [
            {
                "concept": "battery.power",
                "datatype": "number",
                "unit": "W",
                "cadence": "realtime",
                "usages": ["fact", "control"],
                "source": "mapping",
            },
            {
                "concept": "battery.soc",
                "datatype": "number",
                "unit": "%",
                "cadence": "realtime",
                "min": 0,
                "max": 100,
                "usages": ["fact"],
                "source": "mapping",
            },
            {
                "concept": "battery.chargeEnergy",
                "datatype": "number",
                "unit": "kWh",
                "cadence": "interval",
                "intervalMinutes": 15,
                "usages": ["fact"],
                "source": "mapping",
            },
        ],
    },
]


def make_entry(hass):
    """Add a configured Milestone 1 entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SITE_ID,
        title="Home",
        data={
            "site_id": SITE_ID,
            "integration_id": "external-integration-id",
            "integration_internal_id": INTEGRATION_INTERNAL_ID,
            CONF_INTEGRATION_KEY: INTEGRATION_KEY,
            "access_token": "human-jwt",
            "access_token_expires_at": 1,
        },
    )
    entry.add_to_hass(hass)
    return entry


def make_api(catalog=BATTERY_CATALOG):
    """Return a successful Milestone 2 API mock."""
    api = MagicMock(spec=FluksApiClient)
    api.get_device_type_catalog = AsyncMock(return_value=catalog)
    api.list_devices = AsyncMock(return_value=[])
    api.get_device = AsyncMock()
    api.update_device_properties = AsyncMock()
    api.delete_device = AsyncMock()
    api.delete_site = AsyncMock()
    api.login = AsyncMock(return_value=("temporary-human-jwt", 3600))
    api.create_device = AsyncMock(
        return_value={
            "id": DEVICE_INTERNAL_ID,
            "deviceId": EXTERNAL_DEVICE_ID,
            "type": "battery",
            "properties": {},
        }
    )
    api.list_mappings = AsyncMock(return_value=[])
    api.update_mapping = AsyncMock()
    api.delete_mapping = AsyncMock()

    async def create_mapping(_site_id, mapping):
        return {"id": f"mapping-{mapping['concept']}", **mapping}

    api.create_mapping = AsyncMock(side_effect=create_mapping)
    return api


def make_ha_device(hass, config_entry_id):
    """Create a GoodWe Device with strong battery candidates and one weak entity."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry_id,
        identifiers={("goodwe", "inverter")},
        name="GoodWe Inverter",
        manufacturer="GoodWe",
        model="GW10K-ET",
    )
    registry = er.async_get(hass)
    power = registry.async_get_or_create(
        "sensor",
        "test",
        "battery_power",
        device_id=device.id,
        original_name="Battery Power",
        original_device_class="power",
    )
    soc = registry.async_get_or_create(
        "sensor",
        "test",
        "battery_soc",
        device_id=device.id,
        original_name="Battery State of Charge",
        original_device_class="battery",
    )
    energy = registry.async_get_or_create(
        "sensor",
        "test",
        "battery_charge_total",
        device_id=device.id,
        original_name="Battery Charge Total",
        original_device_class="energy",
    )
    weak = registry.async_get_or_create(
        "sensor", "test", "outdoor_temperature", original_name="Outdoor Temperature"
    )
    hass.states.async_set(
        power.entity_id,
        "-842",
        {"unit_of_measurement": "W", "device_class": "power"},
    )
    hass.states.async_set(
        soc.entity_id,
        "73",
        {"unit_of_measurement": "%", "device_class": "battery"},
    )
    hass.states.async_set(
        energy.entity_id,
        "1234.5",
        {
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
        },
    )
    hass.states.async_set(
        weak.entity_id,
        "12",
        {"unit_of_measurement": "°C", "device_class": "temperature"},
    )
    return device, power, soc, energy, weak


async def start_options(hass, entry, api):
    """Start Options Flow with deterministic external Device identity."""
    with patch(
        "custom_components.fluks.options_flow.FluksApiClient", return_value=api
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        if result["type"] is FlowResultType.MENU and result["step_id"] == "init":
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"next_step_id": "add_device"}
            )
        return result


async def start_options_root(hass, entry, api):
    """Start at the Milestone 3 management menu."""
    with patch(
        "custom_components.fluks.options_flow.FluksApiClient", return_value=api
    ):
        return await hass.config_entries.options.async_init(entry.entry_id)


async def reach_site_delete_login(hass, entry, api):
    """Reach Site deletion login through both native confirmation menus."""
    result = await start_options_root(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "site"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_site"}
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "confirm_delete_site"}
    )


def serialize_form(hass, result):
    """Serialize through the actual HA frontend flow response path."""
    return _BaseFlowManagerView(hass.config_entries.options)._prepare_result_json(
        result
    )


def assert_menu_translations_render(hass, result, device_label):
    """Exercise HA's serialized MENU placeholder contract for both locales."""
    serialized = serialize_form(hass, result)
    assert serialized["description_placeholders"] == {"device": device_label}

    rendered: dict[str, tuple[str, str]] = {}
    for language in ("en", "da"):
        translations = json.loads(
            (
                Path("custom_components/fluks/translations")
                / f"{language}.json"
            ).read_text()
        )
        step = translations["options"]["step"][serialized["step_id"]]
        # Native MENU results only serialize description_placeholders. A
        # translated MENU title therefore cannot safely require a value.
        assert not re.findall(r"\{([^{}]+)\}", step["title"])
        required = set(re.findall(r"\{([^{}]+)\}", step["description"]))
        assert required <= serialized["description_placeholders"].keys()
        description = step["description"].format(
            **serialized["description_placeholders"]
        )
        assert "{device}" not in step["title"] + description
        rendered[language] = (step["title"], description)
    return rendered


async def reach_review(hass, api, entry=None):
    """Reach battery review with a native HA Device selection."""
    entry = entry or make_entry(hass)
    device, power, soc, energy, weak = make_ha_device(hass, entry.entry_id)
    result = await start_options(hass, entry, api)
    flow_id = result["flow_id"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "battery"}
    )
    assert result["step_id"] == "ha_device_battery"
    serialized = serialize_form(hass, result)
    assert "device" in serialized["data_schema"][0]["selector"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {CONF_HA_DEVICE_ID: device.id}
    )
    return result, entry, device, power, soc, energy, weak


async def test_catalog_menu_is_dynamic_excludes_site_and_is_retryable(hass):
    """Only physical types returned by the live catalog appear in the menu."""
    entry = make_entry(hass)
    api = make_api()
    api.get_device_type_catalog.side_effect = [FluksCannotConnect(), BATTERY_CATALOG]

    result = await start_options(hass, entry, api)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    serialize_form(hass, result)

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["battery"]
    assert "site" not in result["menu_options"]
    assert api.get_device_type_catalog.await_count == 2


async def test_expired_human_jwt_does_not_affect_add_device(hass):
    """The machine key keeps Options Flow independent of human JWT expiry."""
    entry = make_entry(hass)
    assert entry.data["access_token_expires_at"] == 1
    api = make_api()

    result = await start_options(hass, entry, api)

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "device_type"
    api.get_device_type_catalog.assert_awaited_once()


async def test_legacy_entry_starts_native_reauth_before_add_device(hass):
    """An entry without a machine key cannot fall back to its old human JWT."""
    entry = make_entry(hass)
    legacy_data = dict(entry.data)
    legacy_data.pop(CONF_INTEGRATION_KEY)
    hass.config_entries.async_update_entry(entry, data=legacy_data)

    result = await start_options(hass, entry, make_api())

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_required"
    reauth_flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == config_entries.SOURCE_REAUTH
        and flow["context"].get("entry_id") == entry.entry_id
    ]
    assert len(reauth_flows) == 1


async def test_selected_type_stays_in_context_and_same_ha_device_is_reusable(hass):
    """Type-specific native steps retain context without constraining HA Devices."""
    catalog = [
        BATTERY_CATALOG[1],
        {
            "type": "solar",
            "concepts": [
                {
                    "concept": "solar.power",
                    "datatype": "number",
                    "unit": "W",
                    "cadence": "realtime",
                    "usages": ["fact"],
                    "source": "mapping",
                }
            ],
        },
    ]
    entry = make_entry(hass)
    device, *_ = make_ha_device(hass, entry.entry_id)

    battery = await start_options(hass, entry, make_api(catalog))
    battery = await hass.config_entries.options.async_configure(
        battery["flow_id"], {"next_step_id": "battery"}
    )
    assert battery["step_id"] == "ha_device_battery"
    battery = await hass.config_entries.options.async_configure(
        battery["flow_id"], {CONF_HA_DEVICE_ID: device.id}
    )
    assert battery["step_id"] == "review_battery"
    serialize_form(hass, battery)

    solar = await start_options(hass, entry, make_api(catalog))
    solar = await hass.config_entries.options.async_configure(
        solar["flow_id"], {"next_step_id": "solar"}
    )
    assert solar["step_id"] == "ha_device_solar"
    solar = await hass.config_entries.options.async_configure(
        solar["flow_id"], {CONF_HA_DEVICE_ID: device.id}
    )
    assert solar["step_id"] == "review_solar"
    serialize_form(hass, solar)


async def test_catalog_source_semantics_filter_matcher_review_and_save(hass):
    """Only source=mapping facts can reach matching, UI, or Mapping creation."""
    catalog = [
        {
            "type": "electricVehicle",
            "concepts": [
                {
                    "concept": "electricVehicle.power",
                    "datatype": "number",
                    "unit": "W",
                    "cadence": "realtime",
                    "usages": ["fact"],
                    "source": "mapping",
                },
                {
                    "concept": "electricVehicle.distanceToSite",
                    "datatype": "number",
                    "unit": "m",
                    "cadence": "realtime",
                    "usages": ["fact"],
                    "source": "derived",
                },
                {
                    "concept": "future.dynamicMetric",
                    "datatype": "number",
                    "cadence": "realtime",
                    "usages": ["fact"],
                    "source": "future_semantic",
                },
            ],
        }
    ]
    api = make_api(catalog)
    api.create_device.return_value = {
        "id": DEVICE_INTERNAL_ID,
        "deviceId": EXTERNAL_DEVICE_ID,
        "type": "electricVehicle",
    }
    entry = make_entry(hass)
    device, power, *_ = make_ha_device(hass, entry.entry_id)
    result = await start_options(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "electricVehicle"}
    )
    matcher = MagicMock(return_value={})
    with patch("custom_components.fluks.options_flow.suggest_entities", matcher):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_HA_DEVICE_ID: device.id}
        )

    matched_concepts = matcher.call_args.args[1]
    assert [item["concept"] for item in matched_concepts] == [
        "electricVehicle.power"
    ]
    serialized = serialize_form(hass, result)
    assert [item["name"] for item in serialized["data_schema"]] == [
        SECTION_MEASUREMENTS
    ]
    fields = serialized["data_schema"][0]["schema"]
    assert [item["name"] for item in fields] == ["electricVehicle.power"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {SECTION_MEASUREMENTS: {"electricVehicle.power": power.entity_id}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    api.create_mapping.assert_awaited_once()
    assert api.create_mapping.await_args.args[1]["concept"] == (
        "electricVehicle.power"
    )


async def test_missing_approved_icon_is_a_clear_recoverable_error(hass):
    """A backend type is not silently presented without its approved asset."""
    entry = make_entry(hass)
    api = make_api([{"type": "battery", "concepts": []}])
    with patch(
        "custom_components.fluks.options_flow.icon_path",
        return_value=Path("/definitely/missing.png"),
    ):
        result = await start_options(hass, entry, api)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "missing_device_icon"}


async def test_suggestions_are_optional_serializable_and_user_replaceable(hass):
    """Strong suggestions are defaults while weak or unwanted mappings stay empty."""
    api = make_api()
    result, _entry, device, power, soc, energy, weak = await reach_review(hass, api)

    assert result["type"] is FlowResultType.FORM
    serialized = serialize_form(hass, result)
    sections = {item["name"]: item for item in serialized["data_schema"]}
    assert sections.keys() == {SECTION_MEASUREMENTS, SECTION_ENERGY}
    fields = [
        item
        for current_section in sections.values()
        for item in current_section["schema"]
    ]
    assert all("entity" in item["selector"] for item in fields)
    suggestions = {
        item["name"]: item.get("description", {}).get("suggested_value")
        for item in fields
    }
    assert suggestions["battery.power"] == power.entity_id
    assert suggestions["battery.soc"] == soc.entity_id
    assert suggestions["battery.chargeEnergy"] == energy.entity_id
    assert weak.entity_id not in suggestions.values()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {SECTION_MEASUREMENTS: {"battery.soc": power.entity_id}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    contexts = result["data"][CONF_DEVICE_CONTEXTS]
    assert CONF_INTEGRATION_KEY not in result["data"]
    assert contexts == {
        DEVICE_INTERNAL_ID: {"ha_device_id": device.id, "type": "battery"}
    }
    assert device.id != EXTERNAL_DEVICE_ID
    api.create_device.assert_awaited_once_with(
        SITE_ID,
        stable_device_id("external-integration-id", "battery", device.id),
        "battery",
        {
            "displayName": "GoodWe Inverter",
            "vendor": "GoodWe",
            "model": "GW10K-ET",
        },
    )
    api.create_mapping.assert_awaited_once()
    payload = api.create_mapping.await_args.args[1]
    assert payload["integrationId"] == INTEGRATION_INTERNAL_ID
    assert payload["deviceId"] == DEVICE_INTERNAL_ID
    assert payload["concept"] == "battery.soc"
    assert payload["concept"] == "battery.soc"


async def test_battery_with_only_soc_and_power_saves_without_site_or_energy(hass):
    """Missing energy and Site measurements never block Device creation."""
    api = make_api()
    result, _entry, _device, power, soc, _energy, _weak = await reach_review(hass, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            SECTION_MEASUREMENTS: {
                "battery.power": power.entity_id,
                "battery.soc": soc.entity_id,
            },
            SECTION_ENERGY: {},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert {call.args[1]["concept"] for call in api.create_mapping.await_args_list} == {
        "battery.power", "battery.soc"
    }
    assert all(
        not call.args[1]["concept"].startswith("site.")
        for call in api.create_mapping.await_args_list
    )


async def test_energy_mapping_stores_source_semantics_without_normalization(hass):
    """Energy configuration retains source kind and no runtime interval logic."""
    api = make_api()
    result, _entry, _device, _power, _soc, energy, _weak = await reach_review(
        hass, api
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {SECTION_MEASUREMENTS: {}, SECTION_ENERGY: {"battery.chargeEnergy": energy.entity_id}},
    )
    configuration = api.create_mapping.await_args.args[1]["configuration"]
    assert configuration == {
        "version": 1,
        "entityId": energy.entity_id,
        "source": {"kind": "cumulative"},
    }
    assert "concept" not in configuration
    assert "intervalMinutes" not in configuration


async def test_retry_after_lost_device_response_reuses_stable_identity(hass):
    """A timeout-after-create retry finds the Device and never changes deviceId."""
    api = make_api()
    existing = {
        "id": DEVICE_INTERNAL_ID,
        "deviceId": EXTERNAL_DEVICE_ID,
        "type": "battery",
        "properties": {},
    }
    api.list_devices.side_effect = [[], [existing]]
    api.create_device.side_effect = FluksCannotConnect()
    result, _entry, _device, _power, soc, _energy, _weak = await reach_review(
        hass, api
    )
    existing["deviceId"] = stable_device_id(
        "external-integration-id", "battery", _device.id
    )
    selected = {
        SECTION_MEASUREMENTS: {"battery.soc": soc.entity_id},
        SECTION_ENERGY: {},
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], selected
    )
    assert result["step_id"] == "review_battery"
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], selected
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert DEVICE_INTERNAL_ID in result["data"][CONF_DEVICE_CONTEXTS]
    api.create_device.assert_awaited_once()


async def test_rejected_integration_key_starts_reauth_without_human_fallback(hass):
    """A rejected machine key aborts once into native credential recovery."""
    api = make_api()
    api.list_devices.side_effect = FluksUnauthorized("UNAUTHORIZED")
    result, entry, _device, _power, soc, _energy, _weak = await reach_review(
        hass, api
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {SECTION_MEASUREMENTS: {"battery.soc": soc.entity_id}},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_required"
    api.list_devices.assert_awaited_once_with(SITE_ID)
    api.create_device.assert_not_awaited()
    reauth_flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == config_entries.SOURCE_REAUTH
        and flow["context"].get("entry_id") == entry.entry_id
    ]
    assert len(reauth_flows) == 1


async def test_device_conflict_is_recovered_by_documented_listing(hass):
    """A 409 reuses the matching listed external identity, never a new UUID."""
    api = make_api()
    existing = {
        "id": DEVICE_INTERNAL_ID,
        "deviceId": EXTERNAL_DEVICE_ID,
        "type": "battery",
        "properties": {},
    }
    api.list_devices.side_effect = [[], [existing]]
    api.create_device.side_effect = FluksConflict("DEVICE_ID_CONFLICT")
    result, _entry, _device, _power, soc, _energy, _weak = await reach_review(
        hass, api
    )
    existing["deviceId"] = stable_device_id(
        "external-integration-id", "battery", _device.id
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {SECTION_MEASUREMENTS: {"battery.soc": soc.entity_id}, SECTION_ENERGY: {}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert DEVICE_INTERNAL_ID in result["data"][CONF_DEVICE_CONTEXTS]


async def test_partial_mapping_failure_retries_without_second_device(hass):
    """Already-created mappings are skipped after a recoverable partial failure."""
    api = make_api()
    device = {
        "id": DEVICE_INTERNAL_ID,
        "deviceId": EXTERNAL_DEVICE_ID,
        "type": "battery",
        "properties": {},
    }
    api.list_devices.side_effect = [[], [device]]
    api.create_device.return_value = device
    result, _entry, _device, power, soc, _energy, _weak = await reach_review(hass, api)
    device["deviceId"] = stable_device_id(
        "external-integration-id", "battery", _device.id
    )
    selected = {
        SECTION_MEASUREMENTS: {
            "battery.power": power.entity_id,
            "battery.soc": soc.entity_id,
        },
        SECTION_ENERGY: {},
    }
    first_mapping = {
        "id": "mapping-power",
        "integrationId": INTEGRATION_INTERNAL_ID,
        "deviceId": DEVICE_INTERNAL_ID,
        "concept": "battery.power",
        "direction": "input",
        "configuration": {"version": 1, "entityId": power.entity_id},
    }
    api.list_mappings.side_effect = [[], [first_mapping]]
    api.create_mapping.side_effect = [
        first_mapping,
        FluksCannotConnect(),
        {"id": "mapping-soc"},
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], selected
    )
    assert result["step_id"] == "review_battery"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], selected
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert api.create_device.await_count == 1
    assert api.create_mapping.await_count == 3


async def test_solar_properties_are_optional_and_type_specific(hass):
    """Only Solar exposes optional contract-bounded physical properties."""
    solar_catalog = [
        {
            "type": "solar",
            "concepts": [
                {
                    "concept": "solar.power",
                    "datatype": "number",
                    "unit": "W",
                    "cadence": "realtime",
                    "usages": ["fact"],
                    "source": "mapping",
                }
            ],
        }
    ]
    api = make_api(solar_catalog)
    api.create_device.return_value = {
        "id": DEVICE_INTERNAL_ID,
        "deviceId": EXTERNAL_DEVICE_ID,
        "type": "solar",
    }
    entry = make_entry(hass)
    device, *_ = make_ha_device(hass, entry.entry_id)
    result = await start_options(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "solar"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HA_DEVICE_ID: device.id}
    )
    serialized = serialize_form(hass, result)
    sections = {item["name"]: item for item in serialized["data_schema"]}
    names = [item["name"] for item in sections[SECTION_INSTALLATION]["schema"]]
    assert CONF_AZIMUTH_DEGREES in names
    assert CONF_TILT_DEGREES in names
    invalid = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            SECTION_MEASUREMENTS: {},
            SECTION_INSTALLATION: {CONF_AZIMUTH_DEGREES: 360},
        },
    )
    assert invalid["step_id"] == "review_solar"
    assert invalid["errors"] == {
        CONF_AZIMUTH_DEGREES: "invalid_solar_property"
    }
    api.create_device.assert_not_awaited()
    result = await hass.config_entries.options.async_configure(
        invalid["flow_id"],
        {
            SECTION_MEASUREMENTS: {},
            SECTION_INSTALLATION: {
                CONF_AZIMUTH_DEGREES: 180,
                CONF_TILT_DEGREES: 35,
            },
        },
    )
    properties = api.create_device.await_args.args[3]
    assert properties["azimuthDegrees"] == 180
    assert properties["tiltDegrees"] == 35


async def test_missing_optional_ha_metadata_does_not_block_creation(hass):
    """A Device with no name/vendor/model still meets CreateDevice minimums."""
    api = make_api()
    entry = make_entry(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test", "anonymous")},
    )
    result = await start_options(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "battery"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HA_DEVICE_ID: device.id}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {SECTION_MEASUREMENTS: {}, SECTION_ENERGY: {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    api.create_device.assert_awaited_once_with(
        SITE_ID,
        stable_device_id("external-integration-id", "battery", device.id),
        "battery",
        {},
    )
    api.create_mapping.assert_not_awaited()


async def test_configure_menu_supports_devices_and_additional_devices(hass):
    """Configure exposes management and reuses the approved Add Device path."""
    entry = make_entry(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_DEVICE: {}})
    result = await start_options_root(hass, entry, make_api())
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["menu_options"] == ["devices", "add_device", "site"]
    assert "delete" not in result["menu_options"]


async def test_site_delete_is_separate_confirmed_and_cancelable(hass):
    """Site administration requires confirmation and Cancel is mutation-free."""
    entry = make_entry(hass)
    original_data = dict(entry.data)
    original_options = dict(entry.options)
    api = make_api()

    result = await start_options_root(hass, entry, api)
    assert "delete_site" not in result["menu_options"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "site"}
    )
    assert result["step_id"] == "site"
    assert result["menu_options"] == ["delete_site"]
    serialized = serialize_form(hass, result)
    assert serialized["description_placeholders"] == {"site": "Home"}
    api.login.assert_not_awaited()
    api.delete_site.assert_not_awaited()
    api.delete_device.assert_not_awaited()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_site"}
    )
    assert result["step_id"] == "delete_site"
    assert result["menu_options"] == [
        "confirm_delete_site", "cancel_delete_site"
    ]
    serialized = serialize_form(hass, result)
    assert serialized["description_placeholders"] == {"site": "Home"}
    api.login.assert_not_awaited()
    api.delete_site.assert_not_awaited()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cancel_delete_site"}
    )
    assert result["step_id"] == "site"
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert entry.data == original_data
    assert entry.options == original_options
    assert entry.data[CONF_INTEGRATION_KEY] == INTEGRATION_KEY
    api.login.assert_not_awaited()
    api.delete_site.assert_not_awaited()


async def test_site_delete_wrong_valid_user_is_recoverable(hass):
    """A foreign Site 404 never removes the ConfigEntry or reports success."""
    entry = make_entry(hass)
    original_data = dict(entry.data)
    original_options = {
        CONF_DEVICE_CONTEXTS: {
            DEVICE_INTERNAL_ID: {
                CONF_HA_DEVICE_ID: "ha-device",
                "type": "battery",
            }
        }
    }
    hass.config_entries.async_update_entry(entry, options=original_options)
    api = make_api()
    api.login.return_value = ("valid-user-b-human-jwt", 3600)
    api.delete_site.side_effect = FluksNotFound("NOT_FOUND")
    result = await reach_site_delete_login(hass, entry, api)
    assert result["step_id"] == "site_delete_login"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "user-b@example.com", CONF_PASSWORD: "valid-password"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "site_delete_login"
    assert result["errors"] == {"base": "site_delete_access_denied"}
    api.login.assert_awaited_once_with(
        "user-b@example.com", "valid-password"
    )
    api.delete_site.assert_awaited_once_with(SITE_ID)
    assert api.set_human_access_token.call_args_list[-2:] == [
        call("valid-user-b-human-jwt"),
        call(None),
    ]
    api.delete_device.assert_not_awaited()
    api.delete_mapping.assert_not_awaited()
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert entry.data == original_data
    assert entry.options == original_options
    assert entry.data[CONF_INTEGRATION_KEY] == INTEGRATION_KEY


async def test_site_delete_success_removes_current_config_entry(hass):
    """Only a successful backend DELETE removes the active ConfigEntry."""
    entry = make_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_DEVICE_CONTEXTS: {
                DEVICE_INTERNAL_ID: {
                    CONF_HA_DEVICE_ID: "ha-device",
                    "type": "battery",
                }
            }
        },
    )
    original_key = entry.data[CONF_INTEGRATION_KEY]
    api = make_api()
    result = await reach_site_delete_login(hass, entry, api)
    serialized = serialize_form(hass, result)
    assert [item["name"] for item in serialized["data_schema"]] == [
        CONF_EMAIL, CONF_PASSWORD
    ]
    assert serialized["description_placeholders"] == {"site": "Home"}
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    api.delete_site.assert_not_awaited()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "owner@example.com", CONF_PASSWORD: "password"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "site_deleted"
    api.login.assert_awaited_once_with("owner@example.com", "password")
    api.delete_site.assert_awaited_once_with(SITE_ID)
    assert api.set_human_access_token.call_args_list[-2:] == [
        call("temporary-human-jwt"),
        call(None),
    ]
    api.delete_device.assert_not_awaited()
    api.delete_mapping.assert_not_awaited()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert entry.data[CONF_INTEGRATION_KEY] == original_key


async def test_site_delete_login_and_network_failure_are_recoverable(hass):
    """Authentication and DELETE failures never remove the ConfigEntry."""
    entry = make_entry(hass)
    original_data = dict(entry.data)
    api = make_api()
    api.login.side_effect = [
        FluksInvalidCredentials("INVALID_CREDENTIALS"),
        ("temporary-human-jwt", 3600),
    ]
    api.delete_site.side_effect = FluksCannotConnect()
    result = await reach_site_delete_login(hass, entry, api)
    credentials = {CONF_EMAIL: "owner@example.com", CONF_PASSWORD: "wrong"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], credentials
    )
    assert result["errors"] == {"base": "invalid_auth"}
    api.delete_site.assert_not_awaited()

    credentials[CONF_PASSWORD] = "correct"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], credentials
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert api.delete_site.await_count == 1
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
    assert entry.data == original_data
    assert entry.data[CONF_INTEGRATION_KEY] == INTEGRATION_KEY


async def test_add_device_can_repeat_for_same_entry_and_machine_key(hass):
    """Battery and Solar context accumulate under one ConfigEntry credential."""
    entry = make_entry(hass)
    ha_device, *_ = make_ha_device(hass, entry.entry_id)
    first_api = make_api()
    result = await start_options(hass, entry, first_api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "battery"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HA_DEVICE_ID: ha_device.id}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {SECTION_MEASUREMENTS: {}, SECTION_ENERGY: {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_DEVICE_CONTEXTS] == {
        DEVICE_INTERNAL_ID: {
            CONF_HA_DEVICE_ID: ha_device.id,
            "type": "battery",
        }
    }

    second_internal_id = "00000000-0000-0000-0000-000000000005"
    second_external_id = "00000000-0000-0000-0000-000000000006"
    solar_catalog = [{
        "type": "solar",
        "concepts": [{
            "concept": "solar.power", "datatype": "number", "unit": "W",
            "cadence": "realtime", "usages": ["fact"], "source": "mapping",
        }],
    }]
    second_api = make_api(solar_catalog)
    second_api.create_device.return_value = {
        "id": second_internal_id,
        "deviceId": second_external_id,
        "type": "solar",
        "properties": {},
    }
    result = await start_options(hass, entry, second_api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "solar"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HA_DEVICE_ID: ha_device.id}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {SECTION_MEASUREMENTS: {}, SECTION_INSTALLATION: {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_INTEGRATION_KEY] == INTEGRATION_KEY
    assert set(entry.options[CONF_DEVICE_CONTEXTS]) == {
        DEVICE_INTERNAL_ID, second_internal_id
    }


async def test_same_ha_device_and_type_is_rejected_before_device_creation(hass):
    """Add Battery cannot configure an already-associated HA Device twice."""
    entry = make_entry(hass)
    ha_device, *_ = make_ha_device(hass, entry.entry_id)
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_DEVICE_CONTEXTS: {
                DEVICE_INTERNAL_ID: {
                    CONF_HA_DEVICE_ID: ha_device.id,
                    "type": "battery",
                }
            }
        },
    )
    api = make_api()
    result = await start_options(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "battery"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HA_DEVICE_ID: ha_device.id}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ha_device_battery"
    assert result["errors"] == {
        CONF_HA_DEVICE_ID: "device_type_already_configured"
    }
    api.list_devices.assert_not_awaited()
    api.create_device.assert_not_awaited()
    api.create_mapping.assert_not_awaited()


async def test_different_battery_ha_device_remains_allowed(hass):
    """The uniqueness rule does not prevent genuinely different Batteries."""
    entry = make_entry(hass)
    existing, *_ = make_ha_device(hass, entry.entry_id)
    second = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("goodwe", "second-inverter")},
        name="Second battery inverter",
    )
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_DEVICE_CONTEXTS: {
                DEVICE_INTERNAL_ID: {
                    CONF_HA_DEVICE_ID: existing.id,
                    "type": "battery",
                }
            }
        },
    )
    result = await start_options(hass, entry, make_api())
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "battery"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HA_DEVICE_ID: second.id}
    )
    assert result["step_id"] == "review_battery"


async def test_legacy_context_resolves_type_from_backend_before_rejecting(hass):
    """Older saved HA context remains sufficient for duplicate prevention."""
    entry = make_entry(hass)
    ha_device, *_ = make_ha_device(hass, entry.entry_id)
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_DEVICE_CONTEXTS: {
                DEVICE_INTERNAL_ID: {CONF_HA_DEVICE_ID: ha_device.id}
            }
        },
    )
    api = make_api()
    api.list_devices.return_value = [
        {
            "id": DEVICE_INTERNAL_ID,
            "deviceId": EXTERNAL_DEVICE_ID,
            "type": "battery",
        }
    ]
    result = await start_options(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "battery"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HA_DEVICE_ID: ha_device.id}
    )
    assert result["errors"] == {
        CONF_HA_DEVICE_ID: "device_type_already_configured"
    }
    api.list_devices.assert_awaited_once_with(SITE_ID)
    api.create_device.assert_not_awaited()


def test_stable_device_identity_is_deterministic_per_type_pairing():
    """Repeated flows derive the same identity, while another type differs."""
    first = stable_device_id("integration", "battery", "ha-device")
    assert stable_device_id("integration", "battery", "ha-device") == first
    assert stable_device_id("integration", "solar", "ha-device") != first
    assert stable_device_id("other-integration", "battery", "ha-device") != first


async def test_deterministic_backend_match_is_rejected_not_silently_reused(hass):
    """Missing local context cannot turn Add Device into an implicit edit/reuse."""
    api = make_api()
    result, _entry, device, _power, _soc, _energy, _weak = await reach_review(
        hass, api
    )
    api.list_devices.return_value = [
        {
            "id": DEVICE_INTERNAL_ID,
            "deviceId": stable_device_id(
                "external-integration-id", "battery", device.id
            ),
            "type": "battery",
        }
    ]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {SECTION_MEASUREMENTS: {}, SECTION_ENERGY: {}}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "device_type_already_configured"}
    api.create_device.assert_not_awaited()
    api.create_mapping.assert_not_awaited()


async def open_battery_editor(
    hass, api, entry, mappings, *, edit=True, display_name="Home battery"
):
    """Open a configured Battery through the native management UI."""
    backend_device = {
        "id": DEVICE_INTERNAL_ID,
        "deviceId": EXTERNAL_DEVICE_ID,
        "type": "battery",
        "properties": {
            "displayName": display_name,
            "vendor": "GoodWe",
            "model": "GW10K-ET",
        },
    }
    api.list_devices.return_value = [
        backend_device,
        {"id": "site-internal", "deviceId": "site", "type": "site"},
    ]
    api.get_device.return_value = backend_device
    api.list_mappings.return_value = mappings
    result = await start_options_root(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    assert result["step_id"] == "devices"
    serialized = serialize_form(hass, result)
    assert f"Battery · {display_name}" in str(serialized["data_schema"])
    assert "site-internal" not in str(serialized["data_schema"])
    assert EXTERNAL_DEVICE_ID not in str(serialized)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"selected_device": DEVICE_INTERNAL_ID}
    )
    assert result["step_id"] == "device_actions"
    if not edit:
        return result
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "edit_device"}
    )
    assert result["type"] is FlowResultType.FORM, result
    return result


async def test_delete_is_device_scoped_confirmed_and_cancelable(hass):
    """Delete appears only after selection and Cancel performs no mutation."""
    entry = make_entry(hass)
    api = make_api()
    api.list_devices.return_value = [
        {
            "id": DEVICE_INTERNAL_ID,
            "deviceId": EXTERNAL_DEVICE_ID,
            "type": "battery",
            "properties": {"displayName": "Inverter Goodwe #1"},
        }
    ]
    result = await start_options_root(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    assert result["step_id"] == "devices"
    assert "delete_device" not in str(result)

    result = await open_battery_editor(
        hass,
        api,
        entry,
        [],
        edit=False,
        display_name="Inverter Goodwe #1",
    )
    assert result["menu_options"] == ["edit_device", "delete_device"]
    rendered = assert_menu_translations_render(
        hass, result, "Battery · Inverter Goodwe #1"
    )
    assert "Battery · Inverter Goodwe #1" in rendered["en"][1]
    assert "Battery · Inverter Goodwe #1" in rendered["da"][1]
    api.delete_device.assert_not_awaited()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_device"}
    )
    assert result["step_id"] == "delete_device"
    assert result["menu_options"] == ["confirm_delete", "cancel_delete"]
    rendered = assert_menu_translations_render(
        hass, result, "Battery · Inverter Goodwe #1"
    )
    assert "Delete Battery · Inverter Goodwe #1?" in rendered["en"][1]
    assert "Slet Battery · Inverter Goodwe #1?" in rendered["da"][1]
    api.delete_device.assert_not_awaited()

    original_data = dict(entry.data)
    original_options = dict(entry.options)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cancel_delete"}
    )
    assert result["step_id"] == "device_actions"
    assert entry.data == original_data
    assert entry.options == original_options
    api.login.assert_not_awaited()
    api.delete_device.assert_not_awaited()
    api.delete_mapping.assert_not_awaited()


async def test_confirmed_delete_uses_temporary_human_login_and_finishes(hass):
    """Explicit confirmation logs in once, deletes once, and preserves the key."""
    entry = make_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_DEVICE_CONTEXTS: {
                DEVICE_INTERNAL_ID: {
                    CONF_HA_DEVICE_ID: "ha-device",
                    "type": "battery",
                }
            }
        },
    )
    original_data = dict(entry.data)
    api = make_api()
    result = await open_battery_editor(hass, api, entry, [], edit=False)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_device"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "confirm_delete"}
    )
    assert result["step_id"] == "delete_login"
    serialized = serialize_form(hass, result)
    assert [item["name"] for item in serialized["data_schema"]] == [
        CONF_EMAIL, CONF_PASSWORD
    ]
    assert api.delete_device.await_count == 0

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "owner@example.com", CONF_PASSWORD: "password"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    api.login.assert_awaited_once_with("owner@example.com", "password")
    api.delete_device.assert_awaited_once_with(SITE_ID, DEVICE_INTERNAL_ID)
    api.delete_mapping.assert_not_awaited()
    assert result["data"][CONF_DEVICE_CONTEXTS] == {}
    assert entry.data == original_data
    assert entry.data[CONF_INTEGRATION_KEY] == INTEGRATION_KEY
    assert "temporary-human-jwt" not in str(result)
    assert "access_token" not in result["data"]

    refresh_api = make_api()
    refresh_api.list_devices.return_value = [
        {
            "id": "solar-internal",
            "deviceId": "solar-external",
            "type": "solar",
            "properties": {"displayName": "Home solar"},
        }
    ]
    refreshed = await start_options_root(hass, entry, refresh_api)
    refreshed = await hass.config_entries.options.async_configure(
        refreshed["flow_id"], {"next_step_id": "devices"}
    )
    rendered = str(serialize_form(hass, refreshed))
    assert "Solar · Home solar" in rendered
    assert "Battery · Home battery" not in rendered
    assert "restore" not in rendered.lower()
    assert "undo" not in rendered.lower()


async def test_delete_login_and_backend_failures_are_recoverable(hass):
    """Invalid credentials and one failed DELETE stay retryable without loops."""
    entry = make_entry(hass)
    api = make_api()
    api.login.side_effect = [
        FluksInvalidCredentials("INVALID_CREDENTIALS"),
        ("temporary-human-jwt", 3600),
        ("temporary-human-jwt", 3600),
    ]
    api.delete_device.side_effect = [FluksCannotConnect(), None]
    result = await open_battery_editor(hass, api, entry, [], edit=False)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_device"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "confirm_delete"}
    )
    credentials = {CONF_EMAIL: "owner@example.com", CONF_PASSWORD: "wrong"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], credentials
    )
    assert result["errors"] == {"base": "invalid_auth"}
    api.delete_device.assert_not_awaited()

    credentials[CONF_PASSWORD] = "correct"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], credentials
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert api.delete_device.await_count == 1
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], credentials
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert api.delete_device.await_count == 2


async def test_valid_wrong_user_404_is_recoverable_and_preserves_state(hass):
    """A valid foreign user cannot turn an access-hidden 404 into success."""
    entry = make_entry(hass)
    original_data = dict(entry.data)
    original_options = {
        CONF_DEVICE_CONTEXTS: {
            DEVICE_INTERNAL_ID: {
                CONF_HA_DEVICE_ID: "ha-device",
                "type": "battery",
            }
        }
    }
    hass.config_entries.async_update_entry(entry, options=original_options)
    api = make_api()
    api.login.return_value = ("valid-user-b-human-jwt", 3600)
    api.delete_device.side_effect = FluksNotFound("NOT_FOUND")
    result = await open_battery_editor(hass, api, entry, [], edit=False)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_device"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "confirm_delete"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_EMAIL: "user-b@example.com", CONF_PASSWORD: "valid-password"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "delete_login"
    assert result["errors"] == {"base": "delete_access_denied"}
    api.login.assert_awaited_once_with(
        "user-b@example.com", "valid-password"
    )
    api.delete_device.assert_awaited_once()
    assert entry.data == original_data
    assert entry.options == original_options
    assert entry.data[CONF_INTEGRATION_KEY] == INTEGRATION_KEY
    api.delete_mapping.assert_not_awaited()


async def test_device_labels_always_include_translated_type_and_resolve_id(hass):
    """Same-name Devices remain unambiguous without exposing canonical labels."""
    entry = make_entry(hass)
    catalog = [
        BATTERY_CATALOG[1],
        {
            "type": "solar",
            "concepts": [
                {
                    "concept": "solar.power",
                    "datatype": "number",
                    "unit": "W",
                    "cadence": "realtime",
                    "usages": ["fact"],
                    "source": "mapping",
                }
            ],
        },
        {
            "type": "electricVehicle",
            "concepts": [],
        },
    ]
    battery = {
        "id": DEVICE_INTERNAL_ID,
        "deviceId": EXTERNAL_DEVICE_ID,
        "type": "battery",
        "properties": {"displayName": "Inverter Goodwe #1"},
    }
    solar_internal_id = "00000000-0000-0000-0000-000000000005"
    solar = {
        "id": solar_internal_id,
        "deviceId": "00000000-0000-0000-0000-000000000006",
        "type": "solar",
        "properties": {"displayName": "Inverter Goodwe #1"},
    }
    vehicle = {
        "id": "00000000-0000-0000-0000-000000000007",
        "deviceId": "00000000-0000-0000-0000-000000000008",
        "type": "electricVehicle",
        "properties": {},
    }
    second_battery = {
        "id": "00000000-0000-0000-0000-000000000009",
        "deviceId": "00000000-0000-0000-0000-000000000010",
        "type": "battery",
        "properties": {"displayName": "Pylontech Force H2"},
    }
    duplicate_battery = {
        "id": "00000000-0000-0000-0000-000000000011",
        "deviceId": "00000000-0000-0000-0000-000000000012",
        "type": "battery",
        "properties": {"displayName": "Inverter Goodwe #1"},
    }
    api = make_api(catalog)
    api.list_devices.return_value = [
        solar, second_battery, vehicle, duplicate_battery, battery
    ]
    api.get_device.return_value = solar
    api.list_mappings.return_value = []

    result = await start_options_root(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    serialized = serialize_form(hass, result)
    rendered = str(serialized["data_schema"])
    assert "Battery · Inverter Goodwe #1" in rendered
    assert rendered.count("Battery · Inverter Goodwe #1") == 2
    assert "Solar · Inverter Goodwe #1" in rendered
    assert "Electric vehicle" in rendered
    assert "electricVehicle" not in rendered
    assert rendered.index("Battery · Inverter Goodwe #1") < rendered.index(
        "Battery · Pylontech Force H2"
    )
    assert rendered.index("Battery · Pylontech Force H2") < rendered.index(
        "Electric vehicle"
    )
    assert rendered.index("Electric vehicle") < rendered.index(
        "Solar · Inverter Goodwe #1"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"selected_device": solar_internal_id}
    )
    assert result["step_id"] == "device_actions"
    assert result["description_placeholders"] == {
        "device": "Solar · Inverter Goodwe #1"
    }
    rendered = assert_menu_translations_render(
        hass, result, "Solar · Inverter Goodwe #1"
    )
    assert "Solar · Inverter Goodwe #1" in rendered["en"][1]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "delete_device"}
    )
    rendered = assert_menu_translations_render(
        hass, result, "Solar · Inverter Goodwe #1"
    )
    assert "Delete Solar · Inverter Goodwe #1?" in rendered["en"][1]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cancel_delete"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "edit_device"}
    )
    assert result["step_id"] == "review_solar"
    api.get_device.assert_awaited_once_with(SITE_ID, solar_internal_id)


async def test_device_label_fallbacks_include_type(hass):
    """Vendor/model and type-only fallbacks retain the translated type."""
    translations = {
        "component.fluks.options.step.device_type.menu_options.battery": "Batteri",
        "component.fluks.options.step.devices.type_fallback": "Energienhed",
    }
    assert FluksOptionsFlow._device_display_name(
        {
            "type": "battery",
            "properties": {"vendor": "Pylontech", "model": "Force H2"},
        },
        translations,
    ) == "Batteri · Pylontech Force H2"
    assert FluksOptionsFlow._device_display_name(
        {"type": "battery", "properties": {}}, translations
    ) == "Batteri"


async def test_existing_mapping_wins_and_no_op_edit_writes_nothing(hass):
    """Backend mappings are preselected and bypass matcher replacement."""
    entry = make_entry(hass)
    device, power, soc, _energy, _weak = make_ha_device(hass, entry.entry_id)
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_DEVICE_CONTEXTS: {
                DEVICE_INTERNAL_ID: {CONF_HA_DEVICE_ID: device.id}
            }
        },
    )
    mappings = [
        {
            "id": "mapping-power",
            "integrationId": INTEGRATION_INTERNAL_ID,
            "deviceId": DEVICE_INTERNAL_ID,
            "concept": "battery.power",
            "direction": "input",
            "configuration": {"version": 1, "entityId": power.entity_id},
        },
        {
            "id": "mapping-soc",
            "integrationId": INTEGRATION_INTERNAL_ID,
            "deviceId": DEVICE_INTERNAL_ID,
            "concept": "battery.soc",
            "direction": "input",
            "configuration": {
                "version": 1,
                "entityId": soc.entity_id,
                "transforms": [{"type": "valueMap", "values": {"on": True}}],
            },
        },
    ]
    api = make_api()
    with patch(
        "custom_components.fluks.options_flow.suggest_entities",
        return_value={"battery.power": "sensor.wrong"},
    ) as matcher:
        result = await open_battery_editor(hass, api, entry, mappings)
    matched = [item["concept"] for item in matcher.call_args.args[1]]
    assert matched == ["battery.chargeEnergy"]
    serialized = serialize_form(hass, result)
    fields = {
        item["name"]: item
        for current in serialized["data_schema"]
        if "schema" in current
        for item in current["schema"]
    }
    assert fields["battery.power"]["description"]["suggested_value"] == power.entity_id
    assert fields["battery.soc"]["description"]["suggested_value"] == soc.entity_id

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            SECTION_MEASUREMENTS: {
                "battery.power": power.entity_id,
                "battery.soc": soc.entity_id,
            },
            SECTION_ENERGY: {},
            "device_information": {
                "display_name": "Home battery",
                "vendor": "GoodWe",
                "model": "GW10K-ET",
            },
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    api.update_device_properties.assert_not_awaited()
    api.create_mapping.assert_not_awaited()
    api.update_mapping.assert_not_awaited()
    api.delete_mapping.assert_not_awaited()
    assert mappings[1]["configuration"]["transforms"] == [
        {"type": "valueMap", "values": {"on": True}}
    ]


async def test_edit_reconciles_property_and_mapping_diffs_incrementally(hass):
    """One save PATCHes, POSTs, and DELETEs only the changed resources."""
    entry = make_entry(hass)
    _device, power, soc, energy, _weak = make_ha_device(hass, entry.entry_id)
    mappings = [
        {
            "id": "mapping-power",
            "integrationId": INTEGRATION_INTERNAL_ID,
            "deviceId": DEVICE_INTERNAL_ID,
            "concept": "battery.power",
            "direction": "input",
            "configuration": {"version": 1, "entityId": power.entity_id},
        },
        {
            "id": "mapping-soc",
            "integrationId": INTEGRATION_INTERNAL_ID,
            "deviceId": DEVICE_INTERNAL_ID,
            "concept": "battery.soc",
            "direction": "input",
            "configuration": {"version": 1, "entityId": soc.entity_id},
        },
    ]
    api = make_api()
    api.update_device_properties.return_value = {
        "id": DEVICE_INTERNAL_ID,
        "deviceId": EXTERNAL_DEVICE_ID,
        "type": "battery",
        "properties": {"displayName": "Garage battery", "vendor": "GoodWe", "model": "GW10K-ET"},
    }
    api.update_mapping.return_value = {
        **mappings[0], "configuration": {"version": 1, "entityId": soc.entity_id}
    }
    api.create_mapping.return_value = {
        "id": "mapping-energy",
        "integrationId": INTEGRATION_INTERNAL_ID,
        "deviceId": DEVICE_INTERNAL_ID,
        "concept": "battery.chargeEnergy",
        "direction": "input",
        "configuration": {"version": 1, "entityId": energy.entity_id, "source": {"kind": "cumulative"}},
    }
    result = await open_battery_editor(hass, api, entry, mappings)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            SECTION_MEASUREMENTS: {"battery.power": soc.entity_id},
            SECTION_ENERGY: {"battery.chargeEnergy": energy.entity_id},
            "device_information": {
                "display_name": "Garage battery",
                "vendor": "GoodWe",
                "model": "GW10K-ET",
            },
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    api.update_device_properties.assert_awaited_once_with(
        SITE_ID, DEVICE_INTERNAL_ID, {"displayName": "Garage battery"}
    )
    api.update_mapping.assert_awaited_once()
    assert api.update_mapping.await_args.args[:2] == (SITE_ID, "mapping-power")
    api.delete_mapping.assert_awaited_once_with(SITE_ID, "mapping-soc")
    api.create_mapping.assert_awaited_once()
    assert api.create_mapping.await_args.args[1]["concept"] == "battery.chargeEnergy"
    api.create_device.assert_not_awaited()


async def test_solar_edit_loads_changes_and_clears_optional_properties(hass):
    """Solar installation values round-trip through incremental null semantics."""
    entry = make_entry(hass)
    solar_catalog = [{
        "type": "solar",
        "concepts": [{
            "concept": "solar.power", "datatype": "number", "unit": "W",
            "cadence": "realtime", "usages": ["fact"], "source": "mapping",
        }],
    }]
    backend_device = {
        "id": DEVICE_INTERNAL_ID,
        "deviceId": EXTERNAL_DEVICE_ID,
        "type": "solar",
        "properties": {
            "displayName": "Solar panels",
            "azimuthDegrees": 180,
            "tiltDegrees": 35,
        },
    }
    api = make_api(solar_catalog)
    api.list_devices.return_value = [backend_device]
    api.get_device.return_value = backend_device
    api.list_mappings.return_value = []
    api.update_device_properties.return_value = {
        **backend_device,
        "properties": {
            "displayName": "Solar panels", "azimuthDegrees": 200
        },
    }
    result = await start_options_root(hass, entry, api)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"selected_device": DEVICE_INTERNAL_ID}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "edit_device"}
    )
    serialized = serialize_form(hass, result)
    installation = next(
        item for item in serialized["data_schema"]
        if item["name"] == SECTION_INSTALLATION
    )
    values = {
        item["name"]: item["description"]["suggested_value"]
        for item in installation["schema"]
    }
    assert values == {CONF_AZIMUTH_DEGREES: 180, CONF_TILT_DEGREES: 35}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            SECTION_MEASUREMENTS: {},
            SECTION_INSTALLATION: {CONF_AZIMUTH_DEGREES: 200},
            "device_information": {"display_name": "Solar panels"},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    api.update_device_properties.assert_awaited_once_with(
        SITE_ID,
        DEVICE_INTERNAL_ID,
        {"azimuthDegrees": 200, "tiltDegrees": None},
    )
    api.create_device.assert_not_awaited()


async def test_partial_edit_retry_reconciles_only_remaining_mutations(hass):
    """A retry does not repeat an already successful Mapping PATCH."""
    entry = make_entry(hass)
    _device, power, soc, _energy, _weak = make_ha_device(hass, entry.entry_id)
    mappings = [
        {
            "id": "mapping-power",
            "integrationId": INTEGRATION_INTERNAL_ID,
            "deviceId": DEVICE_INTERNAL_ID,
            "concept": "battery.power",
            "direction": "input",
            "configuration": {"version": 1, "entityId": power.entity_id},
        },
        {
            "id": "mapping-soc",
            "integrationId": INTEGRATION_INTERNAL_ID,
            "deviceId": DEVICE_INTERNAL_ID,
            "concept": "battery.soc",
            "direction": "input",
            "configuration": {"version": 1, "entityId": soc.entity_id},
        },
    ]
    api = make_api()
    api.update_mapping.return_value = {
        **mappings[0],
        "configuration": {"version": 1, "entityId": soc.entity_id},
    }
    api.delete_mapping.side_effect = [FluksCannotConnect(), None]
    result = await open_battery_editor(hass, api, entry, mappings)
    submitted = {
        SECTION_MEASUREMENTS: {"battery.power": soc.entity_id},
        SECTION_ENERGY: {},
        "device_information": {
            "display_name": "Home battery",
            "vendor": "GoodWe",
            "model": "GW10K-ET",
        },
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert api.update_mapping.await_count == 1
    assert api.delete_mapping.await_count == 2
    api.create_device.assert_not_awaited()
