"""Tests for realtime canonical Fact publishing."""

from copy import deepcopy
from unittest.mock import AsyncMock

from custom_components.fluks.observations import RealtimeObservationPublisher


class MemoryStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


def publisher(hass, api, send, store=None, entry_id="entry"):
    result = RealtimeObservationPublisher(hass, api, "site", entry_id, send)
    result._store = store or MemoryStore()
    return result


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
            "concept": "heatPump.bufferTemperature",
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
                "concept": "heatPump.bufferTemperature",
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
        {"deviceId": "heater-external", "heatPump.bufferTemperature": 56},
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
            "concept": "heatPump.bufferTemperature",
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
            {"concept": "heatPump.bufferTemperature", "datatype": "number", "unit": "°C"},
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
        {"deviceId": "heater-external", "heatPump.bufferTemperature": 20},
        {"deviceId": "heater-external", "spaceHeater.temperature": 68},
        {"deviceId": "heater-external", "heatPump.tankTemperature": 68},
        {"deviceId": "heater-external", "heatPump.power": 68},
    ]


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
