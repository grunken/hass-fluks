"""Tests for realtime canonical Fact publishing."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import State

from custom_components.fluks.observations import RealtimeObservationPublisher


class MemoryStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


def publisher(hass, api, send, store=None, entry_id="entry", cooldown=300):
    result = RealtimeObservationPublisher(
        hass, api, "site", entry_id, send, availability_cooldown=cooldown
    )
    result._store = store or MemoryStore()
    return result


def state_event(state, when):
    return SimpleNamespace(
        data={"entity_id": state.entity_id, "new_state": state}, time_fired=when
    )


def space_heater_state_mapping(*, power=False, energy=False):
    mappings = [{
        "direction": "input",
        "deviceId": "heater-internal",
        "concept": "spaceHeater.state",
        "configuration": {"entityId": "sensor.heater_state"},
    }]
    if power:
        mappings.append({
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "spaceHeater.power",
            "configuration": {"entityId": "sensor.real_power"},
        })
    if energy:
        mappings.append({
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "spaceHeater.energy",
            "configuration": {"entityId": "sensor.real_energy"},
        })
    return mappings


async def test_mapped_state_publishes_raw_value_and_refreshes_without_duplicates(hass):
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "site-internal", "deviceId": "site-external", "type": "site"},
        {"id": "heater-internal", "deviceId": "heater-external", "type": "spaceHeater"},
    ]
    api.list_mappings.return_value = [
        {
            "direction": "input",
            "deviceId": "site-internal",
            "concept": "site.power",
            "configuration": {
                "entityId": "sensor.grid_power",
                "transforms": [
                    {"type": "invert"},
                    {"type": "scale", "factor": 2},
                    {"type": "offset", "amount": 10},
                ],
            },
        },
        {
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "spaceHeater.power",
            "configuration": {"entityId": "sensor.heater_power"},
        },
    ]
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    await observations.async_refresh()

    hass.states.async_set("sensor.grid_power", "4919")
    await hass.async_block_till_done()
    send.assert_awaited_once_with(
        {"deviceId": "site-external", "site.power": 4919}
    )

    hass.states.async_set("sensor.unmapped", "12")
    hass.states.async_set("sensor.heater_power", "unavailable")
    hass.states.async_set("sensor.grid_power", "unknown")
    await hass.async_block_till_done()
    assert send.await_count == 1

    api.list_mappings.return_value = [
        {
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "spaceHeater.power",
            "configuration": {"entityId": "sensor.grid_power"},
        }
    ]
    await observations.async_refresh()
    await observations.async_refresh()
    hass.states.async_set("sensor.grid_power", "21.5")
    await hass.async_block_till_done()
    assert send.await_count == 2
    send.assert_awaited_with(
        {"deviceId": "heater-external", "spaceHeater.power": 21.5}
    )

    await observations.async_stop()
    hass.states.async_set("sensor.grid_power", "22")
    await hass.async_block_till_done()
    assert send.await_count == 2


async def test_site_outdoor_temperature_state_mapping_publishes_canonical_fact(hass):
    """A normal Site entity mapping publishes the backend canonical concept unchanged."""
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "site-internal", "deviceId": "site-external", "type": "site"}
    ]
    api.list_mappings.return_value = [{
        "direction": "input",
        "deviceId": "site-internal",
        "concept": "site.outdoorTemperature",
        "configuration": {"version": 1, "entityId": "sensor.outdoor_temperature"},
    }]
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    await observations.async_refresh()

    hass.states.async_set(
        "sensor.outdoor_temperature", "12.5", {"unit_of_measurement": "°C"}
    )
    await hass.async_block_till_done()

    send.assert_awaited_once_with(
        {"deviceId": "site-external", "site.outdoorTemperature": 12.5}
    )


async def test_site_outdoor_temperature_attribute_mapping_uses_mapping_conversion_once(hass):
    """Explicit source units keep conversion in the persisted Mapping pipeline."""
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "site-internal", "deviceId": "site-external", "type": "site"}
    ]
    api.list_mappings.return_value = [{
        "direction": "input",
        "deviceId": "site-internal",
        "concept": "site.outdoorTemperature",
        "configuration": {
            "version": 1,
            "entityId": "sensor.weather",
            "attribute": "outdoor_temperature",
            "unit": "°F",
            "transforms": [
                {"type": "offset", "amount": -32},
                {"type": "scale", "factor": 5 / 9},
            ],
        },
    }]
    api.get_device_type_catalog.return_value = [{
        "type": "site",
        "concepts": [{
            "concept": "site.outdoorTemperature",
            "datatype": "number",
            "unit": "°C",
        }],
    }]
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    await observations.async_refresh()

    hass.states.async_set(
        "sensor.weather", "ok", {"outdoor_temperature": 68, "outdoor_temperature_unit": "°F"}
    )
    await hass.async_block_till_done()

    # The backend Mapping applies °F -> °C; HA must not convert it a second time.
    send.assert_awaited_once_with(
        {"deviceId": "site-external", "site.outdoorTemperature": 68}
    )


async def test_state_and_attribute_mappings_publish_selected_raw_source(hass):
    """Input Mappings default to state and optionally select one HA attribute."""
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "heater-internal", "deviceId": "heater-external"}
    ]
    api.list_mappings.return_value = [
        {
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "heatPump.state",
            "configuration": {"entityId": "climate.buffer"},
        },
        {
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "heatPump.temperature",
            "configuration": {
                "entityId": "climate.buffer",
                "attribute": "current_temperature",
                "transforms": [{"type": "offset", "amount": -1}],
            },
        },
    ]
    api.get_device_type_catalog.return_value = [{
        "type": "heatPump",
        "concepts": [
            {
                "concept": "heatPump.temperature",
                "datatype": "number",
                "unit": "°C",
            }
        ],
    }]
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    await observations.async_refresh()

    hass.states.async_set(
        "climate.buffer",
        "heat",
        {"temperature": 50, "current_temperature": 56},
    )
    await hass.async_block_till_done()

    assert [call.args[0] for call in send.await_args_list] == [
        {"deviceId": "heater-external", "heatPump.state": "heat"},
        {"deviceId": "heater-external", "heatPump.temperature": "56 °C"},
    ]


async def test_temperature_attribute_unit_is_inferred_and_normalized(hass):
    """Only canonical temperature attributes use HA temperature units."""
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "heater-internal", "deviceId": "heater-external"}
    ]
    api.list_mappings.return_value = [
        {
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "heatPump.temperature",
            "configuration": {
                "entityId": "climate.buffer",
                "attribute": "current_temperature",
            },
        },
        {
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "heatPump.tankTemperature",
            "configuration": {
                "entityId": "sensor.controller_temperature",
                "attribute": "reading",
            },
        },
        {
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "spaceHeater.temperature",
            "configuration": {"entityId": "sensor.tank_temperature"},
        },
        {
            "direction": "input",
            "deviceId": "heater-internal",
            "concept": "heatPump.power",
            "configuration": {
                "entityId": "sensor.controller",
                "attribute": "raw_value",
            },
        },
    ]
    api.get_device_type_catalog.return_value = [{
        "type": "heatPump",
        "concepts": [
            {"concept": "heatPump.temperature", "datatype": "number", "unit": "°C"},
            {"concept": "heatPump.tankTemperature", "datatype": "number", "unit": "°C"},
            {"concept": "heatPump.power", "datatype": "number", "unit": "W"},
        ],
    }, {
        "type": "spaceHeater",
        "concepts": [
            {"concept": "spaceHeater.temperature", "datatype": "number", "unit": "°C"},
        ],
    }]
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    await observations.async_refresh()

    hass.states.async_set(
        "climate.buffer",
        "heat",
        {"current_temperature": 68, "current_temperature_unit": "°F"},
    )
    hass.states.async_set(
        "sensor.tank_temperature", "68", {"unit_of_measurement": "°F"}
    )
    hass.states.async_set("sensor.controller_temperature", "online", {"reading": 68})
    hass.states.async_set("sensor.controller", "idle", {"raw_value": 68})
    await hass.async_block_till_done()

    assert [call.args[0] for call in send.await_args_list] == [
        {"deviceId": "heater-external", "heatPump.temperature": "20 °C"},
        {"deviceId": "heater-external", "spaceHeater.temperature": 68},
        {"deviceId": "heater-external", "heatPump.tankTemperature": 68},
        {"deviceId": "heater-external", "heatPump.power": 68},
    ]


async def test_declared_temperature_mapping_defers_its_transform_to_backend_once(hass):
    """New explicit-unit mappings publish raw source values for Mapping transforms."""
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "heater-internal", "deviceId": "heater-external"}
    ]
    api.list_mappings.return_value = [{
        "direction": "input",
        "deviceId": "heater-internal",
        "concept": "heatPump.temperature",
        "configuration": {
            "version": 1,
            "entityId": "climate.buffer",
            "attribute": "current_temperature",
            "unit": "°F",
            "transforms": [
                {"type": "offset", "amount": -32},
                {"type": "scale", "factor": 5 / 9},
            ],
        },
    }]
    api.get_device_type_catalog.return_value = [{
        "type": "heatPump",
        "concepts": [
            {"concept": "heatPump.temperature", "datatype": "number", "unit": "°C"}
        ],
    }]
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    await observations.async_refresh()

    hass.states.async_set(
        "climate.buffer",
        "heat",
        {"current_temperature": 68, "current_temperature_unit": "°F"},
    )
    await hass.async_block_till_done()

    send.assert_awaited_once_with(
        {"deviceId": "heater-external", "heatPump.temperature": 68}
    )


async def test_non_numeric_temperature_attribute_keeps_text_without_unit(hass):
    """HVAC action text is not mislabeled as a temperature."""
    api = AsyncMock()
    api.list_devices.return_value = [{"id": "heater", "deviceId": "heater"}]
    api.list_mappings.return_value = [{
        "direction": "input",
        "deviceId": "heater",
        "concept": "heatPump.temperature",
        "configuration": {"entityId": "climate.buffer", "attribute": "hvac_action"},
    }]
    api.get_device_type_catalog.return_value = [{
        "type": "heatPump",
        "concepts": [{"concept": "heatPump.temperature", "datatype": "number", "unit": "°C"}],
    }]
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    await observations.async_refresh()
    hass.states.async_set("climate.buffer", "heat", {"hvac_action": "heating"})
    await hass.async_block_till_done()
    send.assert_awaited_once_with({"deviceId": "heater", "heatPump.temperature": "heating"})


async def test_cumulative_energy_starts_at_source_and_survives_resets(hass):
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "heater-internal", "deviceId": "heater-external"}
    ]
    api.list_mappings.return_value = [{
        "direction": "input",
        "deviceId": "heater-internal",
        "concept": "spaceHeater.energy",
        "configuration": {
            "entityId": "sensor.heater_energy",
            "source": {"kind": "cumulative"},
        },
    }]
    hass.states.async_set(
        "sensor.heater_energy", "237.4", {"state_class": "total_increasing"}
    )
    send = AsyncMock(return_value=True)
    store = MemoryStore()
    observations = publisher(hass, api, send, store)
    await observations.async_refresh()
    for value in ("237.7", "238.1", "238.1", "0", "0.3", "0", "0.2"):
        hass.states.async_set(
            "sensor.heater_energy", value, {"state_class": "total_increasing"}
        )
        await hass.async_block_till_done()
    hass.states.async_set(
        "sensor.heater_energy", "-1", {"state_class": "total_increasing"}
    )
    await hass.async_block_till_done()

    assert [call.args[0]["spaceHeater.energy"] for call in send.await_args_list] == [
        237.4, 237.7, 238.1, 238.1, 238.4, 238.4, 238.6
    ]
    stream = store.data["streams"]["heater-external|spaceHeater.energy"]
    assert stream == {
        "lifetime": "238.6",
        "source": "sensor.heater_energy",
        "last": "0.2",
        "last_reset": None,
    }


async def test_cumulative_energy_survives_reload_and_source_replacement(hass):
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "heater-internal", "deviceId": "heater-external"}
    ]
    mapping = {
        "direction": "input",
        "deviceId": "heater-internal",
        "concept": "spaceHeater.energy",
        "configuration": {
            "entityId": "sensor.old_energy",
            "source": {"kind": "cumulative"},
        },
    }
    api.list_mappings.return_value = [mapping]
    hass.states.async_set(
        "sensor.old_energy", "8421.7", {"state_class": "total_increasing"}
    )
    store = MemoryStore()
    first_send = AsyncMock(return_value=True)
    first = publisher(hass, api, first_send, store)
    await first.async_refresh()
    await first.async_stop()

    mapping["configuration"]["entityId"] = "sensor.new_energy"
    hass.states.async_set(
        "sensor.new_energy", "37.2", {"state_class": "total_increasing"}
    )
    second_send = AsyncMock(return_value=True)
    second = publisher(hass, api, second_send, store)
    await second.async_refresh()
    hass.states.async_set(
        "sensor.new_energy", "37.5", {"state_class": "total_increasing"}
    )
    await hass.async_block_till_done()

    assert [
        call.args[0]["spaceHeater.energy"] for call in second_send.await_args_list
    ] == [8421.7, 8422.0]
    assert (
        store.data["streams"]["heater-external|spaceHeater.energy"]["last"]
        == "37.5"
    )


async def test_cumulative_streams_are_independent_and_unclassified_drop_is_frozen(hass):
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "one", "deviceId": "device-one"},
        {"id": "two", "deviceId": "device-two"},
    ]
    api.list_mappings.return_value = [
        {
            "direction": "input", "deviceId": internal, "concept": concept,
            "configuration": {"entityId": entity, "source": {"kind": "cumulative"}},
        }
        for internal, concept, entity in (
            ("one", "site.importEnergy", "sensor.one_import"),
            ("one", "site.exportEnergy", "sensor.one_export"),
            ("two", "site.importEnergy", "sensor.two_import"),
        )
    ]
    for entity, value in (
        ("sensor.one_import", "10"),
        ("sensor.one_export", "20"),
        ("sensor.two_import", "30"),
    ):
        hass.states.async_set(entity, value, {"state_class": "total"})
    send = AsyncMock(return_value=True)
    store = MemoryStore()
    observations = publisher(hass, api, send, store)
    await observations.async_refresh()
    hass.states.async_set("sensor.one_import", "9.9", {"state_class": "total"})
    await hass.async_block_till_done()

    assert set(store.data["streams"]) == {
        "device-one|site.importEnergy",
        "device-one|site.exportEnergy",
        "device-two|site.importEnergy",
    }
    assert store.data["streams"]["device-one|site.importEnergy"]["lifetime"] == "10"
    send.assert_awaited_with({"deviceId": "device-one", "site.importEnergy": 10})


async def test_space_heater_fallback_power_and_energy_use_elapsed_heating_time(hass):
    api = AsyncMock()
    api.list_devices.return_value = [{
        "id": "heater-internal",
        "deviceId": "heater-external",
        "type": "spaceHeater",
        "properties": {"ratedPowerW": 2000},
    }]
    api.list_mappings.return_value = space_heater_state_mapping()
    hass.states.async_set("sensor.heater_state", "idle")
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with patch("custom_components.fluks.observations.dt_util.utcnow", return_value=start):
        await observations.async_refresh()

    idle = hass.states.get("sensor.heater_state").__class__(
        "sensor.heater_state", "idle", {}
    )
    await observations._async_state_changed(
        state_event(idle, start + timedelta(minutes=5)),
        {"sensor.heater_state": []},
        {"sensor.heater_state": [("heater-external", True, True, 2000, None)]},
    )

    heating = hass.states.get("sensor.heater_state").__class__(
        "sensor.heater_state", "heating", {}
    )
    await observations._async_state_changed(
        state_event(heating, start + timedelta(minutes=10)),
        {"sensor.heater_state": []},
        {"sensor.heater_state": [("heater-external", True, True, 2000, None)]},
    )
    await observations._async_state_changed(
        state_event(heating, start + timedelta(hours=2, minutes=40)),
        {"sensor.heater_state": []},
        {"sensor.heater_state": [("heater-external", True, True, 2000, None)]},
    )
    idle = hass.states.get("sensor.heater_state").__class__(
        "sensor.heater_state", "idle", {}
    )
    await observations._async_state_changed(
        state_event(idle, start + timedelta(hours=3)),
        {"sensor.heater_state": []},
        {"sensor.heater_state": [("heater-external", True, True, 2000, None)]},
    )

    assert [call.args[0] for call in send.await_args_list] == [
        {"deviceId": "heater-external", "spaceHeater.power": 2000},
        {"deviceId": "heater-external", "spaceHeater.energy": 5},
        {"deviceId": "heater-external", "spaceHeater.power": 0, "spaceHeater.energy": 5.666666666666667},
    ]


async def test_space_heater_fallback_deduplicates_unchanged_power_and_energy(hass):
    """Derived fallback observations are emitted only for changed values."""
    api = AsyncMock()
    api.list_devices.return_value = [{
        "id": "heater-internal",
        "deviceId": "heater-external",
        "type": "spaceHeater",
        "properties": {"ratedPowerW": 450},
    }]
    api.list_mappings.return_value = space_heater_state_mapping()
    hass.states.async_set("sensor.heater_state", "idle")
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with patch("custom_components.fluks.observations.dt_util.utcnow", return_value=start):
        await observations.async_refresh()

    def event(value, minutes):
        state = hass.states.get("sensor.heater_state").__class__(
            "sensor.heater_state", value, {}
        )
        return state_event(state, start + timedelta(minutes=minutes))

    derived = {"sensor.heater_state": [("heater-external", True, True, 450, None)]}
    await observations._async_state_changed(event("idle", 1), {}, derived)
    await observations._async_state_changed(event("heating", 2), {}, derived)
    await observations._async_state_changed(event("heating", 2), {}, derived)
    await observations._async_state_changed(event("idle", 3), {}, derived)
    await observations._async_state_changed(event("idle", 3), {}, derived)

    assert [call.args[0] for call in send.await_args_list] == [
        {"deviceId": "heater-external", "spaceHeater.power": 450},
        {"deviceId": "heater-external", "spaceHeater.power": 0, "spaceHeater.energy": 0.0075},
    ]


async def test_space_heater_fallback_energy_persists_across_reload(hass):
    api = AsyncMock()
    api.list_devices.return_value = [{
        "id": "heater-internal", "deviceId": "heater-external", "type": "spaceHeater",
        "properties": {"ratedPowerW": 1000},
    }]
    api.list_mappings.return_value = space_heater_state_mapping()
    hass.states.async_set("sensor.heater_state", "heating")
    store = MemoryStore()
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    first_send = AsyncMock(return_value=True)
    first = publisher(hass, api, first_send, store)
    with patch("custom_components.fluks.observations.dt_util.utcnow", return_value=start):
        await first.async_refresh()
    state = hass.states.get("sensor.heater_state")
    await first._async_state_changed(
        state_event(state, start + timedelta(hours=1)),
        {"sensor.heater_state": []},
        {"sensor.heater_state": [("heater-external", True, True, 1000, None)]},
    )
    await first.async_stop()

    second_send = AsyncMock(return_value=True)
    second = publisher(hass, api, second_send, store)
    with patch(
        "custom_components.fluks.observations.dt_util.utcnow",
        return_value=start + timedelta(hours=3),
    ):
        await second.async_refresh()
    assert second_send.await_args_list == []
    await second._async_state_changed(
        state_event(state, start + timedelta(hours=4)),
        {"sensor.heater_state": []},
        {"sensor.heater_state": [("heater-external", True, True, 1000, None)]},
    )
    assert second_send.await_args_list[-1].args[0]["spaceHeater.energy"] == 2


async def test_space_heater_fallback_restart_gap_to_idle_does_not_add_energy(hass):
    api = AsyncMock()
    api.list_devices.return_value = [{
        "id": "heater-internal", "deviceId": "heater-external", "type": "spaceHeater",
        "properties": {"ratedPowerW": 1000},
    }]
    api.list_mappings.return_value = space_heater_state_mapping()
    hass.states.async_set("sensor.heater_state", "heating")
    store = MemoryStore()
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    first = publisher(hass, api, AsyncMock(return_value=True), store)
    with patch("custom_components.fluks.observations.dt_util.utcnow", return_value=start):
        await first.async_refresh()
    state = hass.states.get("sensor.heater_state")
    await first._async_state_changed(
        state_event(state, start + timedelta(hours=1)),
        {"sensor.heater_state": []},
        {"sensor.heater_state": [("heater-external", True, True, 1000, None)]},
    )
    await first.async_stop()
    hass.states.async_set("sensor.heater_state", "idle")

    send = AsyncMock(return_value=True)
    second = publisher(hass, api, send, store)
    with patch(
        "custom_components.fluks.observations.dt_util.utcnow",
        return_value=start + timedelta(hours=8),
    ):
        await second.async_refresh()
    assert send.await_args_list[-1].args[0] == {
        "deviceId": "heater-external", "spaceHeater.power": 0,
    }


async def test_real_space_heater_mappings_override_fallback_values(hass):
    api = AsyncMock()
    api.list_devices.return_value = [{
        "id": "heater-internal", "deviceId": "heater-external", "type": "spaceHeater",
        "properties": {"ratedPowerW": 2000},
    }]
    api.list_mappings.return_value = space_heater_state_mapping(power=True, energy=True)
    hass.states.async_set("sensor.heater_state", "idle")
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send)
    await observations.async_refresh()
    state = hass.states.get("sensor.heater_state").__class__(
        "sensor.heater_state", "heating", {}
    )
    await observations._async_state_changed(
        state_event(state, datetime.now(timezone.utc)),
        {"sensor.heater_state": []},
        {},
    )
    assert not any("spaceHeater.power" in call.args[0] for call in send.await_args_list)
    await observations._async_state_changed(
        state_event(
            hass.states.get("sensor.heater_state").__class__(
                "sensor.real_power", "321", {}
            ),
            datetime.now(timezone.utc),
        ),
        {
                "sensor.real_power": [("heater-external", "spaceHeater.power", False, None, None, False)],
        },
        {},
    )
    assert send.await_args_list[-1].args[0] == {
        "deviceId": "heater-external", "spaceHeater.power": 321
    }
    await observations._async_state_changed(
        state_event(
            hass.states.get("sensor.heater_state").__class__(
                "sensor.real_energy", "12.5", {}
            ),
            datetime.now(timezone.utc),
        ),
        {
            "sensor.real_energy": [("heater-external", "spaceHeater.energy", False, None, None, False)],
        },
        {},
    )
    assert send.await_args_list[-1].args[0] == {
        "deviceId": "heater-external", "spaceHeater.energy": 12.5
    }


def _availability_mapping(concept="solar.power", entity="sensor.source", attribute=None):
    configuration = {"entityId": entity}
    if attribute is not None:
        configuration["attribute"] = attribute
    return [{
        "direction": "input",
        "deviceId": "device-internal",
        "concept": concept,
        "configuration": configuration,
    }]


def _availability_api(api, mappings):
    api.list_devices.return_value = [{
        "id": "device-internal", "deviceId": "device-external"
    }]
    api.list_mappings.return_value = mappings


async def test_mapped_source_recovery_before_cooldown_sends_no_availability_event(hass):
    api = AsyncMock()
    _availability_api(api, _availability_mapping("solar.power"))
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send, cooldown=300)
    await observations.async_refresh()

    source = State("sensor.source", "unavailable", {})
    await observations._async_state_changed(
        state_event(source, datetime.now(timezone.utc)),
        {"sensor.source": [("device-external", "solar.power", False, None, None, False)]},
        {},
    )
    assert send.await_count == 0
    recovered = State("sensor.source", "12", {})
    await observations._async_state_changed(
        state_event(recovered, datetime.now(timezone.utc)),
        {"sensor.source": [("device-external", "solar.power", False, None, None, False)]},
        {},
    )

    send.assert_awaited_once_with({"deviceId": "device-external", "solar.power": 12})
    await observations.async_stop()


async def test_mapped_source_unavailable_after_cooldown_is_sent_once(hass):
    api = AsyncMock()
    _availability_api(api, _availability_mapping("site.power"))
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send, cooldown=0)
    await observations.async_refresh()
    mappings = {
        "sensor.source": [("device-external", "site.power", False, None, None, False)]
    }
    unavailable = State("sensor.source", "unavailable", {})
    await observations._async_state_changed(
        state_event(unavailable, datetime.now(timezone.utc)), mappings, {}
    )
    await observations._availability_tasks["device-external|site.power"]

    send.assert_awaited_once_with({
        "site.power": {"deviceId": "device-external", "status": "unavailable"}
    })
    await observations._async_state_changed(
        state_event(unavailable, datetime.now(timezone.utc)), mappings, {}
    )
    assert send.await_count == 1
    await observations.async_stop()


async def test_mapped_attribute_recovery_sends_available_after_unavailable(hass):
    api = AsyncMock()
    _availability_api(
        api, _availability_mapping("heatPump.tankTemperature", "climate.tank", "current_temperature")
    )
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send, cooldown=0)
    await observations.async_refresh()
    mappings = {
        "climate.tank": [(
            "device-external",
            "heatPump.tankTemperature",
            False,
            "current_temperature",
            None,
            False,
        )]
    }
    unavailable = State("climate.tank", "unavailable", {"current_temperature": "unknown"})
    await observations._async_state_changed(
        state_event(unavailable, datetime.now(timezone.utc)), mappings, {}
    )
    await observations._availability_tasks["device-external|heatPump.tankTemperature"]

    available = State("climate.tank", "heat", {"current_temperature": []})
    await observations._async_state_changed(
        state_event(available, datetime.now(timezone.utc)), mappings, {}
    )
    assert [call.args[0] for call in send.await_args_list] == [
        {
            "heatPump.tankTemperature": {
                "deviceId": "device-external", "status": "unavailable"
            }
        },
        {
            "heatPump.tankTemperature": {
                "deviceId": "device-external", "status": "available"
            }
        },
    ]
    await observations.async_stop()


async def test_valid_observation_recovers_without_separate_available_event(hass):
    api = AsyncMock()
    _availability_api(api, _availability_mapping("battery.soc"))
    send = AsyncMock(return_value=True)
    observations = publisher(hass, api, send, cooldown=0)
    await observations.async_refresh()
    mappings = {
        "sensor.source": [(
            "device-external", "battery.soc", False, None, None, False
        )]
    }
    await observations._async_state_changed(
        state_event(State("sensor.source", "unavailable", {}), datetime.now(timezone.utc)),
        mappings,
        {},
    )
    await observations._availability_tasks["device-external|battery.soc"]
    await observations._async_state_changed(
        state_event(State("sensor.source", "57", {}), datetime.now(timezone.utc)),
        mappings,
        {},
    )
    assert [call.args[0] for call in send.await_args_list] == [
        {"battery.soc": {"deviceId": "device-external", "status": "unavailable"}},
        {"deviceId": "device-external", "battery.soc": 57},
    ]
    assert observations._availability == {}
    await observations.async_stop()


async def test_unavailable_sent_state_survives_restart(hass):
    api = AsyncMock()
    _availability_api(api, _availability_mapping("solar.power"))
    store = MemoryStore()
    first_send = AsyncMock(return_value=True)
    first = publisher(hass, api, first_send, store, cooldown=0)
    await first.async_refresh()
    mappings = {
        "sensor.source": [(
            "device-external", "solar.power", False, None, None, False
        )]
    }
    await first._async_state_changed(
        state_event(State("sensor.source", "unavailable", {}), datetime.now(timezone.utc)),
        mappings,
        {},
    )
    await first._availability_tasks["device-external|solar.power"]
    await first.async_stop()

    second_send = AsyncMock(return_value=True)
    second = publisher(hass, api, second_send, store, cooldown=0)
    await second.async_refresh()
    assert second._availability == {
        "device-external|solar.power": {"source": "sensor.source"}
    }
    await second._async_state_changed(
        state_event(State("sensor.source", "9", {}), datetime.now(timezone.utc)),
        mappings,
        {},
    )
    second_send.assert_awaited_once_with({"deviceId": "device-external", "solar.power": 9})
    assert "availability" not in store.data
    await second.async_stop()
