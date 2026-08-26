"""Tests for realtime canonical Fact publishing."""

from unittest.mock import AsyncMock

from custom_components.fluks.observations import (
    RealtimeObservationPublisher,
    apply_input_transforms,
)


def test_raw_values_and_ordered_input_transforms_are_canonical_json_values():
    assert apply_input_transforms("4919", [{"type": "invert"}]) == -4919
    assert apply_input_transforms(
        "10.5",
        [{"type": "scale", "factor": 2}, {"type": "offset", "amount": 1}],
    ) == 22.0
    assert apply_input_transforms(
        "on", [{"type": "valueMap", "values": {"on": True, "off": False}}]
    ) is True


async def test_mapped_state_changes_publish_and_refresh_without_duplicate_listeners(hass):
    api = AsyncMock()
    api.list_devices.return_value = [
        {"id": "site-internal", "deviceId": "site-external", "type": "site"},
        {"id": "battery-internal", "deviceId": "battery-external", "type": "battery"},
    ]
    api.list_mappings.return_value = [
        {
            "direction": "input",
            "deviceId": "site-internal",
            "concept": "site.power",
            "configuration": {
                "entityId": "sensor.grid_power",
                "transforms": [{"type": "invert"}],
            },
        },
        {
            "direction": "input",
            "deviceId": "battery-internal",
            "concept": "battery.soc",
            "configuration": {"entityId": "sensor.battery_soc"},
        },
    ]
    send = AsyncMock(return_value=True)
    publisher = RealtimeObservationPublisher(hass, api, "site", send)
    await publisher.async_refresh()

    hass.states.async_set("sensor.grid_power", "4919")
    await hass.async_block_till_done()
    send.assert_awaited_once_with(
        {"deviceId": "site-external", "site.power": -4919}
    )

    hass.states.async_set("sensor.unmapped", "12")
    hass.states.async_set("sensor.battery_soc", "unavailable")
    hass.states.async_set("sensor.grid_power", "unknown")
    await hass.async_block_till_done()
    assert send.await_count == 1

    api.list_mappings.return_value = [
        {
            "direction": "input",
            "deviceId": "battery-internal",
            "concept": "battery.soc",
            "configuration": {"entityId": "sensor.grid_power"},
        }
    ]
    await publisher.async_refresh()
    await publisher.async_refresh()
    hass.states.async_set("sensor.grid_power", "21.5")
    await hass.async_block_till_done()
    assert send.await_count == 2
    send.assert_awaited_with(
        {"deviceId": "battery-external", "battery.soc": 21.5}
    )

    await publisher.async_stop()
    hass.states.async_set("sensor.grid_power", "22")
    await hass.async_block_till_done()
    assert send.await_count == 2
