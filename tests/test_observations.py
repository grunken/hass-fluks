"""Tests for realtime canonical Fact publishing."""

from unittest.mock import AsyncMock

from custom_components.fluks.observations import RealtimeObservationPublisher


async def test_mapped_state_publishes_raw_value_and_refreshes_without_duplicates(hass):
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
                "transforms": [
                    {"type": "invert"},
                    {"type": "scale", "factor": 2},
                    {"type": "offset", "amount": 10},
                ],
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
        {"deviceId": "site-external", "site.power": 4919}
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
