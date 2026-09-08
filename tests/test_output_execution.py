"""Tests for runtime execution of persisted output Mappings."""

from unittest.mock import AsyncMock, MagicMock

from custom_components.fluks.output_execution import RuntimeOutputExecutor


def _catalog():
    return [{
        "type": "battery",
        "concepts": [{
            "concept": "battery.power", "datatype": "number",
            "usages": ["fact", "control"], "mappingModes": [None, "target"],
        }],
    }]


def _mapping(mode=None):
    return {
        "deviceId": "internal-battery", "concept": "battery.power",
        "direction": "output", "mode": mode,
        "configuration": {
            "version": 1,
            "actions": [
                {
                    "type": "serviceCall", "service": "select.select_option",
                    "target": {"entityId": "select.inverter_mode"},
                    "data": {"option": {"kind": "literal", "value": "eco"}},
                },
                {
                    "type": "serviceCall", "service": "number.set_value",
                    "target": {"entityId": "number.battery_power"},
                    "data": {"value": {"kind": "requestedValue", "transforms": [
                        {"type": "invert"}, {"type": "scale", "factor": 0.5},
                    ]}},
                },
            ],
        },
    }


async def test_snapshot_resolves_device_mapping_transforms_and_order(hass):
    calls = []

    async def record(call):
        calls.append((call.domain, call.service, call.data))

    hass.services.async_register("select", "select_option", record)
    hass.services.async_register("number", "set_value", record)
    api = MagicMock()
    api.list_devices = AsyncMock(return_value=[{
        "id": "internal-battery", "deviceId": "external-battery", "type": "battery",
    }])
    api.list_mappings = AsyncMock(return_value=[_mapping()])
    api.get_device_type_catalog = AsyncMock(return_value=_catalog())

    await RuntimeOutputExecutor(hass, api, "site-a").async_handle({
        "type": "decision.snapshot",
        "decisions": [{
            "deviceId": "external-battery", "deviceType": "battery",
            "power": "3000", "mode": None,
        }],
    })

    assert calls == [
        ("select", "select_option", {"option": "eco", "entity_id": "select.inverter_mode"}),
        ("number", "set_value", {"value": -1500, "entity_id": "number.battery_power"}),
    ]


async def test_decision_mode_selects_only_the_exact_mapping_behavior(hass):
    calls = []

    async def record(call):
        calls.append(call.data["option"])

    hass.services.async_register("select", "select_option", record)
    default = _mapping()
    default["configuration"]["actions"] = [default["configuration"]["actions"][0]]
    target = _mapping("target")
    target["configuration"]["actions"] = [target["configuration"]["actions"][0]]
    target["configuration"]["actions"][0]["data"]["option"]["value"] = "target"
    api = MagicMock()
    api.list_devices = AsyncMock(return_value=[{
        "id": "internal-battery", "deviceId": "external-battery", "type": "battery",
    }])
    api.list_mappings = AsyncMock(return_value=[default, target])
    api.get_device_type_catalog = AsyncMock(return_value=_catalog())
    executor = RuntimeOutputExecutor(hass, api, "site-a")

    await executor.async_handle({"type": "decision.snapshot", "decisions": [{
        "deviceId": "external-battery", "deviceType": "battery", "power": 100, "mode": "target",
    }]})
    assert calls == ["target"]

    await executor.async_handle({"type": "decision.snapshot", "decisions": [{
        "deviceId": "external-battery", "deviceType": "battery", "power": 0, "mode": None,
    }]})
    assert calls == ["target", "eco"]


async def test_water_heater_temperature_decision_uses_renamed_canonical_field(hass):
    calls = []

    async def record(call):
        calls.append(call.data)

    hass.services.async_register("water_heater", "set_temperature", record)
    api = MagicMock()
    api.list_devices = AsyncMock(return_value=[
        {"id": "heater-internal", "deviceId": "heater-external", "type": "waterHeater"},
    ])
    api.list_mappings = AsyncMock(return_value=[{
        "deviceId": "heater-internal",
        "concept": "waterHeater.temperature",
        "direction": "output",
        "mode": "target",
        "configuration": {
            "version": 1,
            "actions": [{
                "type": "serviceCall",
                "service": "water_heater.set_temperature",
                "target": {"entityId": "water_heater.tank"},
                "data": {"temperature": {"kind": "requestedValue"}},
            }],
        },
    }])
    api.get_device_type_catalog = AsyncMock(return_value=[{
        "type": "waterHeater",
        "concepts": [{
            "concept": "waterHeater.temperature",
            "datatype": "number",
            "unit": "°C",
            "usages": ["fact", "control"],
        }],
    }])

    await RuntimeOutputExecutor(hass, api, "site-a").async_handle({
        "type": "decision.snapshot",
        "decisions": [{
            "deviceId": "heater-external",
            "deviceType": "waterHeater",
            "temperature": "55",
            "mode": "target",
        }],
    })

    assert calls == [{"temperature": 55, "entity_id": "water_heater.tank"}]


def _ownership_api():
    def action(entity_id):
        return {"version": 1, "actions": [{
            "type": "serviceCall", "service": "test.execute",
            "target": {"entityId": entity_id}, "data": {},
        }]}

    api = MagicMock()
    api.list_devices = AsyncMock(return_value=[
        {"id": "site-internal", "deviceId": "site-external", "type": "site"},
        {"id": "battery-internal", "deviceId": "battery-external", "type": "battery"},
    ])
    api.list_mappings = AsyncMock(return_value=[
        {"deviceId": "site-internal", "concept": "site.power", "direction": "output", "mode": "balance", "configuration": action("number.balance")},
        {"deviceId": "site-internal", "concept": "site.power", "direction": "output", "mode": "release", "configuration": action("button.release")},
        {"deviceId": "battery-internal", "concept": "battery.power", "direction": "output", "mode": None, "configuration": action("number.battery")},
    ])
    api.get_device_type_catalog = AsyncMock(return_value=[
        {"type": "site", "concepts": [{"concept": "site.power", "datatype": "number", "usages": ["control"]}]},
        {"type": "battery", "concepts": [{"concept": "battery.power", "datatype": "number", "usages": ["control"]}]},
    ])
    return api


def _decision(device_type, mode, value=0):
    return {
        "deviceId": f"{device_type}-external", "deviceType": device_type,
        "power": value, "mode": mode,
    }


async def test_site_balance_executes_first_and_suppresses_battery_power(hass):
    calls = []
    hass.services.async_register("test", "execute", lambda call: calls.append(call.data["entity_id"]))
    executor = RuntimeOutputExecutor(hass, _ownership_api(), "site-a")

    await executor.async_handle({"type": "decision.snapshot", "decisions": [
        _decision("battery", None, 1200), _decision("site", "balance"),
    ]})

    assert calls == ["number.balance"]


async def test_site_release_clears_ownership_and_resumes_battery_power(hass):
    calls = []
    hass.services.async_register("test", "execute", lambda call: calls.append(call.data["entity_id"]))
    executor = RuntimeOutputExecutor(hass, _ownership_api(), "site-a")
    await executor.async_handle({"type": "decision.snapshot", "decisions": [_decision("site", "balance")]})

    await executor.async_handle({"type": "decision.snapshot", "decisions": [
        _decision("battery", None, 1200), _decision("site", "release"),
    ]})

    assert calls == ["number.balance", "button.release", "number.battery"]


async def test_reconnect_balance_snapshot_establishes_suppression(hass):
    calls = []
    hass.services.async_register("test", "execute", lambda call: calls.append(call.data["entity_id"]))
    reconnected = RuntimeOutputExecutor(hass, _ownership_api(), "site-a")

    await reconnected.async_handle({"type": "decision.snapshot", "decisions": [
        _decision("battery", None), _decision("site", "balance"),
    ]})

    assert calls == ["number.balance"]


async def test_reconnect_release_snapshot_clears_existing_suppression(hass):
    calls = []
    hass.services.async_register("test", "execute", lambda call: calls.append(call.data["entity_id"]))
    executor = RuntimeOutputExecutor(hass, _ownership_api(), "site-a")
    await executor.async_handle({"type": "decision.snapshot", "decisions": [_decision("site", "balance")]})

    await executor.async_handle({"type": "decision.snapshot", "decisions": [
        _decision("battery", None), _decision("site", "release"),
    ]})

    assert calls[-2:] == ["button.release", "number.battery"]


async def test_unmapped_balance_does_not_take_ownership(hass):
    calls = []
    hass.services.async_register("test", "execute", lambda call: calls.append(call.data["entity_id"]))
    api = _ownership_api()
    api.list_mappings.return_value = [
        item for item in api.list_mappings.return_value if item["mode"] != "balance"
    ]

    await RuntimeOutputExecutor(hass, api, "site-a").async_handle({
        "type": "decision.snapshot", "decisions": [
            _decision("battery", None), _decision("site", "balance"),
        ],
    })

    assert calls == ["number.battery"]
