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
