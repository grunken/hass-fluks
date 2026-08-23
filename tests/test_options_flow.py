"""Tests for the native Milestone 2 Add Device Options Flow."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluks.api import (
    FluksApiClient,
    FluksCannotConnect,
    FluksConflict,
)
from custom_components.fluks.const import CONF_DEVICE, DOMAIN
from custom_components.fluks.options_flow import (
    CONF_AZIMUTH_DEGREES,
    CONF_HA_DEVICE_ID,
    CONF_TILT_DEGREES,
    SECTION_ENERGY,
    SECTION_INSTALLATION,
    SECTION_MEASUREMENTS,
)

SITE_ID = "00000000-0000-0000-0000-000000000001"
INTEGRATION_INTERNAL_ID = "00000000-0000-0000-0000-000000000002"
DEVICE_INTERNAL_ID = "00000000-0000-0000-0000-000000000003"
EXTERNAL_DEVICE_ID = "00000000-0000-0000-0000-000000000004"

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
            "access_token": "human-jwt",
            "access_token_expires_at": 9999999999,
        },
    )
    entry.add_to_hass(hass)
    return entry


def make_api(catalog=BATTERY_CATALOG):
    """Return a successful Milestone 2 API mock."""
    api = MagicMock(spec=FluksApiClient)
    api.get_device_type_catalog = AsyncMock(return_value=catalog)
    api.list_devices = AsyncMock(return_value=[])
    api.create_device = AsyncMock(
        return_value={
            "id": DEVICE_INTERNAL_ID,
            "deviceId": EXTERNAL_DEVICE_ID,
            "type": "battery",
            "properties": {},
        }
    )
    api.list_mappings = AsyncMock(return_value=[])

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
    with (
        patch(
            "custom_components.fluks.options_flow.uuid4",
            return_value=UUID(EXTERNAL_DEVICE_ID),
        ),
        patch(
            "custom_components.fluks.options_flow.FluksApiClient", return_value=api
        ),
    ):
        return await hass.config_entries.options.async_init(entry.entry_id)


def serialize_form(hass, result):
    """Serialize through the actual HA frontend flow response path."""
    return _BaseFlowManagerView(hass.config_entries.options)._prepare_result_json(
        result
    )


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
    saved = result["data"][CONF_DEVICE]
    assert saved["device_id"] == EXTERNAL_DEVICE_ID
    assert saved["internal_id"] == DEVICE_INTERNAL_ID
    assert saved["ha_device_id"] == device.id
    assert saved["ha_device_id"] != saved["device_id"]
    assert [item["concept"] for item in saved["mappings"]] == ["battery.soc"]
    api.create_device.assert_awaited_once_with(
        SITE_ID,
        EXTERNAL_DEVICE_ID,
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
    assert "battery.power" not in [item["concept"] for item in saved["mappings"]]


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
    assert {item["concept"] for item in result["data"][CONF_DEVICE]["mappings"]} == {
        "battery.power",
        "battery.soc",
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
    configuration = result["data"][CONF_DEVICE]["mappings"][0]["configuration"]
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
    assert result["data"][CONF_DEVICE]["device_id"] == EXTERNAL_DEVICE_ID
    api.create_device.assert_awaited_once()


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
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {SECTION_MEASUREMENTS: {"battery.soc": soc.entity_id}, SECTION_ENERGY: {}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE]["device_id"] == EXTERNAL_DEVICE_ID


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
        SITE_ID, EXTERNAL_DEVICE_ID, "battery", {}
    )
    api.create_mapping.assert_not_awaited()


async def test_second_device_is_not_a_milestone_two_management_flow(hass):
    """An entry with one Device does not expose list/edit/add-another UI."""
    entry = make_entry(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_DEVICE: {}})
    result = await start_options(hass, entry, make_api())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "device_already_configured"
