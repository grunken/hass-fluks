"""Tests for metadata-driven Home Assistant Control actions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.setup import async_setup_component

from custom_components.fluks.control_capabilities import (
    async_control_capabilities,
    async_validate_control_configuration,
)


def descriptions():
    return {
        "number": {"set_value": {"name": "Set value", "description": "Sets a number value.", "target": {"entity": {"domain": "number"}}, "fields": {"value": {"name": "Value", "required": True, "selector": {"text": {}}}}}},
        "select": {"select_option": {"name": "Select option", "target": {"entity": {"domain": "select"}}, "fields": {"option": {"name": "Option", "required": True, "selector": {"state": {}}}}}},
        "climate": {"set_temperature": {"name": "Set temperature", "target": {"entity": [{"domain": ["climate"], "supported_features": [1]}]}, "fields": {"temperature": {"name": "Temperature", "required": True, "selector": {"number": {}}, "filter": {"supported_features": [1]}}}}},
        "bad": {"opaque": {"name": "Opaque", "fields": {"payload": {"selector": {"object": {}}}}}, "no_target": {"name": "No target", "fields": {}}},
    }


async def test_actions_entities_fields_and_live_constraints_come_from_ha(hass):
    hass.states.async_set("number.vehicle_limit", "8", {"friendly_name": "Vehicle current", "min": 6, "max": 16, "step": 1})
    hass.states.async_set("number.home_battery", "50", {"friendly_name": "Battery target", "min": 0, "max": 100, "step": 5})
    hass.states.async_set("select.inverter_mode", "eco", {"friendly_name": "Inverter mode", "options": ["eco", "eco_charge"]})
    hass.states.async_set("climate.living_room", "heat", {"friendly_name": "Living room", "supported_features": 1, "min_temp": 7, "max_temp": 35, "target_temp_step": 0.5})
    hass.states.async_set("climate.unsupported", "off", {"supported_features": 0})
    hass.states.async_set("sensor.wrong_domain", "10")
    with patch("custom_components.fluks.control_capabilities.async_get_all_descriptions", AsyncMock(return_value=descriptions())):
        actions = await async_control_capabilities(hass)
    assert [item["service"] for item in actions] == ["select.select_option", "climate.set_temperature", "number.set_value"]
    number = next(item for item in actions if item["service"] == "number.set_value")
    assert {item["entity_id"] for item in number["entities"]} == {"number.vehicle_limit", "number.home_battery"}
    assert number["entities"][0]["fields"][0]["required"] is True
    assert number["entities"][0]["fields"][0]["selector"]["type"] == "number"
    assert number["entities"][0]["fields"][0]["constraints"] == {"min": 0, "max": 100, "step": 5}
    select = next(item for item in actions if item["service"] == "select.select_option")
    assert select["entities"][0]["fields"][0]["constraints"]["options"] == ["eco", "eco_charge"]
    climate = next(item for item in actions if item["service"] == "climate.set_temperature")
    assert [item["entity_id"] for item in climate["entities"]] == ["climate.living_room"]
    assert climate["entities"][0]["fields"][0]["constraints"] == {"min": 7, "max": 35, "step": 0.5}
    assert all(not item["service"].startswith("bad.") for item in actions)


async def test_target_only_action_is_supported_when_entity_target_is_described(hass):
    hass.states.async_set("switch.pump", "off")
    metadata = {"switch": {"turn_on": {"name": "Turn on", "target": {"entity": {"domain": "switch"}}, "fields": {}}}}
    with patch("custom_components.fluks.control_capabilities.async_get_all_descriptions", AsyncMock(return_value=metadata)):
        actions = await async_control_capabilities(hass)
    assert actions[0]["entities"][0]["fields"] == []


async def test_target_integration_prevents_unrelated_services_from_matching_entities(hass):
    """Target integration metadata must scope services to their real platform."""
    hass.states.async_set("sensor.aquarea_energy", "1")
    hass.states.async_set("sensor.utility_meter", "1")
    entity_registry = MagicMock()
    entity_registry.async_get.side_effect = lambda entity_id: SimpleNamespace(
        device_id=None,
        platform="utility_meter" if entity_id == "sensor.utility_meter" else "aquarea",
    )
    descriptions = {
        "utility_meter": {
            "calibrate": {
                "name": "Calibrate",
                "target": {"entity": {"domain": "sensor", "integration": "utility_meter"}},
                "fields": {"value": {"required": True, "selector": {"text": {}}}},
            }
        }
    }
    with (
        patch(
            "custom_components.fluks.control_capabilities.async_get_all_descriptions",
            AsyncMock(return_value=descriptions),
        ),
        patch("custom_components.fluks.control_capabilities.er.async_get", return_value=entity_registry),
    ):
        actions = await async_control_capabilities(hass)

    assert [item["entity_id"] for item in actions[0]["entities"]] == [
        "sensor.utility_meter"
    ]


async def test_water_heater_temperature_capability_keeps_live_constraints(hass):
    """The cleanup must retain the water-heater setter and its HA ranges."""
    hass.states.async_set(
        "water_heater.naervarme_tank",
        "eco",
        {"friendly_name": "Tank", "min_temp": 40, "max_temp": 65, "target_temp_step": 1},
    )
    entity_registry = MagicMock()
    entity_registry.async_get.return_value = SimpleNamespace(
        device_id="heat-pump", platform="aquarea"
    )
    descriptions = {
        "water_heater": {
            "set_temperature": {
                "name": "Set temperature",
                "target": {"entity": {"domain": "water_heater", "integration": "aquarea"}},
                "fields": {
                    "temperature": {
                        "required": True,
                        "selector": {"number": {"min": 0, "max": 250, "step": 0.5}},
                    }
                },
            }
        }
    }
    with (
        patch(
            "custom_components.fluks.control_capabilities.async_get_all_descriptions",
            AsyncMock(return_value=descriptions),
        ),
        patch("custom_components.fluks.control_capabilities.er.async_get", return_value=entity_registry),
    ):
        actions = await async_control_capabilities(hass)

    entity = actions[0]["entities"][0]
    assert entity["entity_id"] == "water_heater.naervarme_tank"
    assert entity["fields"][0]["constraints"] == {"min": 40, "max": 65, "step": 1}


async def test_entity_search_metadata_comes_from_ha_registries_without_fluks_scoping(hass):
    hass.states.async_set("switch.powerful", "off", {"friendly_name": "Powerful"})
    descriptions = {
        "switch": {
            "turn_on": {
                "name": "Turn on",
                "target": {"entity": {"domain": "switch"}},
                "fields": {},
            }
        }
    }
    entity_registry = MagicMock()
    entity_registry.async_get.return_value = SimpleNamespace(
        device_id="device-1", platform="panasonic_aquarea"
    )
    device_registry = MagicMock()
    device_registry.async_get.return_value = SimpleNamespace(
        name_by_user="Nærvarme",
        name="Heat pump",
        manufacturer="Panasonic",
        model="WH-ADC0309J3E5",
    )
    with (
        patch(
            "custom_components.fluks.control_capabilities.async_get_all_descriptions",
            AsyncMock(return_value=descriptions),
        ),
        patch(
            "custom_components.fluks.control_capabilities.er.async_get",
            return_value=entity_registry,
        ),
        patch(
            "custom_components.fluks.control_capabilities.dr.async_get",
            return_value=device_registry,
        ),
    ):
        actions = await async_control_capabilities(hass)

    entity = actions[0]["entities"][0]
    assert entity["entity_id"] == "switch.powerful"
    assert entity["metadata"] == (
        "Nærvarme · panasonic_aquarea · Panasonic · WH-ADC0309J3E5"
    )


async def test_real_home_assistant_number_and_select_descriptions_normalize(hass):
    """Exercise HA's real runtime services.yaml path, not a fixture shape."""
    assert await async_setup_component(hass, "number", {})
    assert await async_setup_component(hass, "select", {})
    hass.states.async_set("number.real_target", "5", {"friendly_name": "Real target", "min": 0, "max": 10, "step": 1})
    hass.states.async_set("select.real_mode", "eco", {"friendly_name": "Real mode", "options": ["eco", "charge"]})

    actions = await async_control_capabilities(hass)

    number = next(item for item in actions if item["service"] == "number.set_value")
    number_entity = next(item for item in number["entities"] if item["entity_id"] == "number.real_target")
    assert number_entity["fields"][0]["selector"]["type"] == "number"
    select = next(item for item in actions if item["service"] == "select.select_option")
    select_entity = next(item for item in select["entities"] if item["entity_id"] == "select.real_mode")
    assert select_entity["fields"][0]["selector"]["type"] == "select"
    assert select_entity["fields"][0]["constraints"]["options"] == ["eco", "charge"]


async def test_current_metadata_validates_entity_required_field_type_and_range(hass):
    hass.states.async_set("number.target", "5", {"min": 0, "max": 10, "step": 1})
    valid = {"version": 1, "actions": [{"type": "serviceCall", "service": "number.set_value", "target": {"entityId": "number.target"}, "data": {"value": {"kind": "requestedValue"}}}]}
    wrong_domain = {"version": 1, "actions": [{**valid["actions"][0], "target": {"entityId": "sensor.target"}}]}
    out_of_range = {"version": 1, "actions": [{**valid["actions"][0], "data": {"value": {"kind": "literal", "value": 11}}}]}
    with patch("custom_components.fluks.control_capabilities.async_get_all_descriptions", AsyncMock(return_value=descriptions())):
        assert await async_validate_control_configuration(hass, valid)
        assert not await async_validate_control_configuration(hass, wrong_domain)
        assert not await async_validate_control_configuration(hass, out_of_range)
