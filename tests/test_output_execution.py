"""Tests for runtime execution of persisted output Mappings."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

from custom_components.fluks.output_execution import RuntimeOutputExecutor


class MemoryStore:
    def __init__(self, data=None):
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


def temperature_mapping(concept="heatPump.temperature", entity="climate.zone"):
    return {
        "deviceId": "temperature-internal", "concept": concept,
        "direction": "output", "mode": "target",
        "configuration": {"version": 1, "actions": [{
            "type": "serviceCall", "service": "climate.set_temperature",
            "target": {"entityId": entity},
            "data": {"temperature": {"kind": "requestedValue"}},
        }]},
    }


def temperature_api(mapping, device_type="heatPump"):
    api = MagicMock()
    api.list_devices = AsyncMock(return_value=[{
        "id": "temperature-internal", "deviceId": "temperature-external",
        "type": device_type,
    }])
    api.list_mappings = AsyncMock(return_value=[mapping])
    api.get_device_type_catalog = AsyncMock(return_value=[{
        "type": device_type,
        "concepts": [{
            "concept": mapping["concept"], "datatype": "number",
            "usages": ["fact", "control"],
        }],
    }])
    return api


def temperature_decision(device_type="heatPump", field="temperature", value="22.05", mode="target"):
    return {
        "deviceId": "temperature-external", "deviceType": device_type,
        field: value, "mode": mode,
    }


def _catalog():
    return [{
        "type": "battery",
        "concepts": [{
            "concept": "battery.power", "datatype": "number",
            "usages": ["fact", "control"], "mappingModes": [None, "target"],
        }],
    }]


def _retry_mapping(*, actions=None):
    return {
        "deviceId": "internal-battery", "concept": "battery.power",
        "direction": "output", "mode": None,
        "configuration": {"version": 1, "actions": actions or [{
            "type": "serviceCall", "service": "test.execute",
            "target": {"entityId": "number.battery_power"},
            "data": {"value": {"kind": "requestedValue"}},
        }]},
    }


def _retry_api(mapping):
    api = MagicMock()
    api.list_devices = AsyncMock(return_value=[{
        "id": "internal-battery", "deviceId": "external-battery", "type": "battery",
    }])
    api.list_mappings = AsyncMock(return_value=[mapping])
    api.get_device_type_catalog = AsyncMock(return_value=_catalog())
    return api


async def _run_retry(hass, mapping, failures, *, sleep=None):
    calls = []

    async def execute(call):
        calls.append(call)
        if len(calls) <= failures:
            raise RuntimeError("service failed")

    hass.services.async_register("test", "execute", execute)
    executor = RuntimeOutputExecutor(
        hass, _retry_api(mapping), "site-a", sleep=sleep or (lambda _delay: asyncio.sleep(0))
    )
    await executor.async_handle({
        "type": "decision.snapshot",
        "decisions": [{
            "deviceId": "external-battery", "deviceType": "battery",
            "power": "3000", "mode": None,
        }],
    })
    return calls


async def _run_verified(hass, mapping, *, updates=(), failures=0, attribute=None, sleep=None):
    calls = []
    entity_id = mapping["configuration"]["actions"][0]["target"]["entityId"]
    field = next(iter(mapping["configuration"]["actions"][0]["data"]), "value")
    domain, service = ("climate", "set_temperature") if attribute else ("number", "set_value")
    mapping["configuration"]["actions"][0]["service"] = f"{domain}.{service}"
    if attribute is None:
        hass.states.async_set(entity_id, "0", {})
    else:
        hass.states.async_set(entity_id, "idle", {attribute: 0})

    async def execute(call):
        calls.append(call)
        attempt = len(calls)
        if attempt <= failures:
            raise RuntimeError("service failed")
        if attempt in updates:
            if attribute is None:
                hass.states.async_set(entity_id, str(call.data[field]), {})
            else:
                hass.states.async_set(entity_id, "idle", {attribute: call.data[field]})

    hass.services.async_register(domain, service, execute)
    executor = RuntimeOutputExecutor(
        hass, _retry_api(mapping), "site-a", sleep=sleep or (lambda _delay: asyncio.sleep(0))
    )
    await executor.async_handle({
        "type": "decision.snapshot",
        "decisions": [{
            "deviceId": "external-battery", "deviceType": "battery",
            "power": "3000", "mode": None,
        }],
    })
    return calls


async def test_action_result_is_verified_after_first_attempt(hass):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    calls = await _run_verified(hass, _retry_mapping(), updates={1}, sleep=sleep)

    assert len(calls) == 1
    assert delays == [1.0]


async def test_successful_service_without_result_change_is_retried(hass):
    calls = await _run_verified(hass, _retry_mapping(), updates={2})

    assert len(calls) == 2


async def test_action_result_change_after_second_attempt_stops_retries(hass):
    calls = await _run_verified(hass, _retry_mapping(), updates={2}, failures=1)

    assert len(calls) == 2


async def test_action_result_never_changes_after_three_attempts(hass, caplog):
    calls = await _run_verified(hass, _retry_mapping())

    assert len(calls) == 3
    assert "did not reach its expected value after 3 attempts" in caplog.text


async def test_action_result_verifies_mapped_attribute(hass):
    mapping = _retry_mapping(actions=[{
        "type": "serviceCall", "service": "test.execute",
        "target": {"entityId": "climate.zone"},
        "data": {"temperature": {"kind": "requestedValue"}},
    }])
    calls = await _run_verified(
        hass, mapping, updates={1}, attribute="temperature"
    )

    assert len(calls) == 1


async def test_numeric_state_verification_accepts_equivalent_representations(hass):
    executor = RuntimeOutputExecutor(hass, MagicMock(), "site-a")

    assert executor._same_value(55, "55.0")


async def test_action_retry_succeeds_on_second_attempt(hass):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    calls = await _run_retry(hass, _retry_mapping(), 1, sleep=sleep)

    assert len(calls) == 2
    assert [call.data["value"] for call in calls] == [3000, 3000]
    assert delays == [1.0]


async def test_action_success_does_not_retry(hass):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    calls = await _run_retry(hass, _retry_mapping(), 0, sleep=sleep)

    assert len(calls) == 1
    assert delays == []


async def test_action_retry_succeeds_on_third_attempt(hass):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    calls = await _run_retry(hass, _retry_mapping(), 2, sleep=sleep)

    assert len(calls) == 3
    assert delays == [1.0, 1.0]


async def test_action_retry_is_bounded_after_final_failure(hass, caplog):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    calls = await _run_retry(hass, _retry_mapping(), 3, sleep=sleep)

    assert len(calls) == 3
    assert delays == [1.0, 1.0]
    assert "failed after 3 attempts" in caplog.text


async def test_action_retry_does_not_repeat_successful_actions(hass):
    calls = []
    failures = {"number.battery_power": 1}

    async def execute(call):
        entity_id = call.data["entity_id"]
        calls.append(entity_id)
        if entity_id in failures:
            failures[entity_id] -= 1
            if failures[entity_id] == 0:
                raise RuntimeError("service failed")

    hass.services.async_register("test", "execute", execute)
    mapping = _retry_mapping(actions=[
        {
            "type": "serviceCall", "service": "test.execute",
            "target": {"entityId": "select.inverter_mode"},
            "data": {"option": {"kind": "literal", "value": "eco"}},
        },
        {
            "type": "serviceCall", "service": "test.execute",
            "target": {"entityId": "number.battery_power"},
            "data": {"value": {"kind": "requestedValue"}},
        },
    ])
    executor = RuntimeOutputExecutor(
        hass, _retry_api(mapping), "site-a", sleep=lambda _delay: asyncio.sleep(0)
    )

    await executor.async_handle({
        "type": "decision.snapshot",
        "decisions": [{
            "deviceId": "external-battery", "deviceType": "battery",
            "power": "3000", "mode": None,
        }],
    })

    assert calls == ["select.inverter_mode", "number.battery_power", "number.battery_power"]


async def test_action_retry_wait_does_not_block_an_unrelated_action(hass):
    retry_started = asyncio.Event()
    release_retry = asyncio.Event()
    calls = []

    async def failing(call):
        calls.append("failing")
        raise RuntimeError("service failed")

    async def unrelated(call):
        calls.append("unrelated")

    async def sleep(_delay):
        retry_started.set()
        await release_retry.wait()

    hass.services.async_register("test", "failing", failing)
    hass.services.async_register("test", "unrelated", unrelated)
    executor = RuntimeOutputExecutor(
        hass, _retry_api(_retry_mapping()), "site-a", sleep=sleep
    )
    retry_task = asyncio.create_task(executor._async_execute_action(
        {"service": "test.failing", "target": {"entityId": "number.one"}},
        "test", "failing", {},
    ))
    await retry_started.wait()
    await asyncio.wait_for(executor._async_execute_action(
        {"service": "test.unrelated", "target": {"entityId": "number.two"}},
        "test", "unrelated", {},
    ), timeout=0.1)
    release_retry.set()
    result = await asyncio.gather(retry_task, return_exceptions=True)

    assert calls == ["failing", "unrelated", "failing", "failing"]
    assert isinstance(result[0], RuntimeError)


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


async def test_temperature_target_persists_previous_and_actual_applied_value(hass):
    store = MemoryStore()
    api = temperature_api(temperature_mapping())
    calls = []

    async def apply(call):
        calls.append(call.data["temperature"])
        accepted = round(call.data["temperature"])
        hass.states.async_set("climate.zone", "heat", {"temperature": accepted})

    hass.services.async_register("climate", "set_temperature", apply)
    hass.states.async_set("climate.zone", "heat", {"temperature": 21})
    executor = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)

    await executor.async_handle({"type": "decision.snapshot", "decisions": [
        temperature_decision(),
    ]})

    assert calls == [22.05]
    assert store.data == {"controls": {"temperature-external|heatPump.temperature": {
        "previous": 21, "applied": 22,
    }}}


async def test_temperature_release_restores_native_previous_without_reapplying_transforms(hass):
    store = MemoryStore()
    mapping = temperature_mapping()
    mapping["configuration"]["actions"][0]["data"]["temperature"]["transforms"] = [
        {"type": "difference", "reference": {"entityId": "climate.reference", "attribute": "temperature"}},
        {"type": "round", "decimals": 0},
        {"type": "clamp", "min": -5, "max": 5},
    ]
    api = temperature_api(mapping)
    calls = []

    async def apply(call):
        calls.append(call.data["temperature"])
        hass.states.async_set("climate.zone", "heat", {"temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_temperature", apply)
    hass.states.async_set("climate.zone", "heat", {"temperature": 0})
    hass.states.async_set("climate.reference", "heat", {"temperature": 35.2})
    executor = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)

    await executor.async_handle({"type": "decision.snapshot", "decisions": [
        temperature_decision(value="38.2"),
    ]})
    await executor.async_handle({"type": "decision.snapshot", "decisions": [
        temperature_decision(value=None, mode="release"),
    ]})

    assert calls == [3, 0]
    assert store.data == {"controls": {}}


async def test_missing_output_reference_does_not_execute_action(hass):
    mapping = temperature_mapping()
    mapping["configuration"]["actions"][0]["data"]["temperature"]["transforms"] = [
        {"type": "difference", "reference": {"entityId": "climate.missing"}},
    ]
    api = temperature_api(mapping)
    calls = []
    hass.services.async_register("climate", "set_temperature", lambda call: calls.append(call))
    hass.states.async_set("climate.zone", "heat", {"temperature": 21})
    await RuntimeOutputExecutor(hass, api, "site-a").async_handle({
        "type": "decision.snapshot", "decisions": [temperature_decision()],
    })
    assert calls == []


async def test_output_reference_reads_entity_state(hass):
    mapping = temperature_mapping()
    mapping["configuration"]["actions"][0]["data"]["temperature"]["transforms"] = [
        {"type": "difference", "reference": {"entityId": "sensor.reference"}},
    ]
    api = temperature_api(mapping)
    calls = []
    async def apply(call):
        calls.append(call.data["temperature"])
        hass.states.async_set("climate.zone", "heat", {"temperature": call.data["temperature"]})
    hass.services.async_register("climate", "set_temperature", apply)
    hass.states.async_set("climate.zone", "heat", {"temperature": 0})
    hass.states.async_set("sensor.reference", "35.2", {})
    await RuntimeOutputExecutor(hass, api, "site-a").async_handle({
        "type": "decision.snapshot", "decisions": [temperature_decision(value="38.2")],
    })
    assert calls == [3.0]


async def test_temperature_release_restores_matching_applied_value_and_clears_state(hass):
    store = MemoryStore()
    api = temperature_api(temperature_mapping())
    calls = []

    async def apply(call):
        calls.append(call.data["temperature"])
        hass.states.async_set("climate.zone", "heat", {"temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_temperature", apply)
    hass.states.async_set("climate.zone", "heat", {"temperature": 21})
    executor = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)
    await executor.async_handle({"type": "decision.snapshot", "decisions": [temperature_decision()]})
    await executor.async_handle({"type": "decision.snapshot", "decisions": [
        temperature_decision(value=None, mode="release"),
    ]})

    assert calls == [22.05, 21]
    assert store.data == {"controls": {}}


async def test_temperature_release_preserves_manual_override(hass):
    store = MemoryStore()
    api = temperature_api(temperature_mapping())
    calls = []

    async def apply(call):
        calls.append(call.data["temperature"])
        hass.states.async_set("climate.zone", "heat", {"temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_temperature", apply)
    hass.states.async_set("climate.zone", "heat", {"temperature": 21})
    executor = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)
    await executor.async_handle({"type": "decision.snapshot", "decisions": [temperature_decision()]})
    hass.states.async_set("climate.zone", "heat", {"temperature": 23})
    await executor.async_handle({"type": "decision.snapshot", "decisions": [
        temperature_decision(value=None, mode="release"),
    ]})

    assert calls == [22.05]
    assert hass.states.get("climate.zone").attributes["temperature"] == 23
    assert store.data == {"controls": {}}


async def test_consecutive_temperature_targets_preserve_original_previous(hass):
    store = MemoryStore()
    api = temperature_api(temperature_mapping())

    async def apply(call):
        hass.states.async_set("climate.zone", "heat", {"temperature": round(call.data["temperature"])})

    hass.services.async_register("climate", "set_temperature", apply)
    hass.states.async_set("climate.zone", "heat", {"temperature": 21})
    executor = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)
    await executor.async_handle({"type": "decision.snapshot", "decisions": [temperature_decision()]})
    await executor.async_handle({"type": "decision.snapshot", "decisions": [temperature_decision(value="23.1")]})
    assert store.data["controls"]["temperature-external|heatPump.temperature"] == {
        "previous": 21, "applied": 23,
    }


async def test_lost_temperature_ownership_starts_a_new_period(hass):
    store = MemoryStore()
    api = temperature_api(temperature_mapping())

    async def apply(call):
        hass.states.async_set("climate.zone", "heat", {"temperature": round(call.data["temperature"])})

    hass.services.async_register("climate", "set_temperature", apply)
    hass.states.async_set("climate.zone", "heat", {"temperature": 21})
    executor = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)
    await executor.async_handle({"type": "decision.snapshot", "decisions": [temperature_decision()]})
    hass.states.async_set("climate.zone", "heat", {"temperature": 23})
    await executor.async_handle({"type": "decision.snapshot", "decisions": [temperature_decision(value="24.2")]})
    await executor.async_handle({"type": "decision.snapshot", "decisions": [temperature_decision(value=None, mode="release")]})

    assert hass.states.get("climate.zone").attributes["temperature"] == 23
    assert store.data == {"controls": {}}


async def test_temperature_ownership_survives_executor_reload_and_numeric_equivalence(hass):
    store = MemoryStore()
    api = temperature_api(temperature_mapping())
    calls = []

    async def apply(call):
        calls.append(call.data["temperature"])
        hass.states.async_set("climate.zone", "heat", {"temperature": round(call.data["temperature"])})

    hass.services.async_register("climate", "set_temperature", apply)
    hass.states.async_set("climate.zone", "heat", {"temperature": 21})
    first = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)
    await first.async_handle({"type": "decision.snapshot", "decisions": [temperature_decision()]})
    hass.states.async_set("climate.zone", "heat", {"temperature": 22})
    reloaded = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)
    await reloaded.async_handle({"type": "decision.snapshot", "decisions": [temperature_decision(value=None, mode="release")]})

    assert calls == [22.05, 21]
    assert store.data == {"controls": {}}


async def test_temperature_release_without_valid_ownership_is_fail_safe(hass):
    store = MemoryStore({"controls": {"temperature-external|heatPump.temperature": {"previous": "bad"}}})
    api = temperature_api(temperature_mapping())
    calls = []
    hass.services.async_register("climate", "set_temperature", lambda call: calls.append(call))
    hass.states.async_set("climate.zone", "heat", {"temperature": 22})
    executor = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)
    await executor.async_handle({"type": "decision.snapshot", "decisions": [
        temperature_decision(value=None, mode="release"),
    ]})

    assert calls == []
    assert store.data == {"controls": {}}


async def test_all_supported_temperature_controls_use_temporary_ownership(hass):
    store = MemoryStore()
    calls = []

    async def apply(call):
        calls.append(call.data["temperature"])
        hass.states.async_set("climate.zone", "heat", {"temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_temperature", apply)
    hass.states.async_set("climate.zone", "heat", {"temperature": 20})
    for device_type, concept in (
        ("heatPump", "heatPump.temperature"),
        ("heatPump", "heatPump.tankTemperature"),
        ("waterHeater", "waterHeater.temperature"),
        ("spaceHeater", "spaceHeater.temperature"),
    ):
        api = temperature_api(temperature_mapping(concept), device_type)
        executor = RuntimeOutputExecutor(hass, api, "site-a", ownership_store=store)
        await executor.async_handle({"type": "decision.snapshot", "decisions": [
            temperature_decision(device_type=device_type, field=concept.rsplit(".", 1)[1]),
        ]})

    assert calls == [22.05, 22.05, 22.05, 22.05]
    assert set(store.data["controls"]) == {
        "temperature-external|heatPump.temperature",
        "temperature-external|heatPump.tankTemperature",
        "temperature-external|waterHeater.temperature",
        "temperature-external|spaceHeater.temperature",
    }


async def test_water_heater_temperature_decision_uses_renamed_canonical_field(hass):
    calls = []

    async def record(call):
        calls.append(call.data)
        hass.states.async_set(
            "water_heater.tank", "idle", {"temperature": call.data["temperature"]}
        )

    hass.services.async_register("water_heater", "set_temperature", record)
    hass.states.async_set("water_heater.tank", "idle", {"temperature": 50})
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


async def test_heat_pump_temperature_and_tank_temperature_decisions_remain_distinct(hass):
    calls = []

    async def record(call):
        calls.append((call.service, call.data))
        entity_id = call.data["entity_id"]
        field = next(field for field in call.data if field != "entity_id")
        hass.states.async_set(entity_id, str(call.data[field]), {})

    hass.services.async_register("number", "set_value", record)
    hass.states.async_set("number.flow_target", "20", {})
    hass.states.async_set("number.tank_target", "20", {})
    api = MagicMock()
    api.list_devices = AsyncMock(return_value=[
        {"id": "heat-internal", "deviceId": "heat-external", "type": "heatPump"},
    ])
    def mapping(concept, entity):
        field = concept.rsplit(".", 1)[1]
        return {
            "deviceId": "heat-internal", "concept": concept,
            "direction": "output", "mode": "target",
            "configuration": {"version": 1, "actions": [{
                "type": "serviceCall", "service": "number.set_value",
                "target": {"entityId": entity},
                "data": {field: {"kind": "requestedValue"}},
            }]},
        }
    api.list_mappings = AsyncMock(return_value=[
        mapping("heatPump.temperature", "number.flow_target"),
        mapping("heatPump.tankTemperature", "number.tank_target"),
    ])
    api.get_device_type_catalog = AsyncMock(return_value=[{
        "type": "heatPump",
        "concepts": [
            {"concept": "heatPump.temperature", "datatype": "number", "usages": ["fact", "control"]},
            {"concept": "heatPump.tankTemperature", "datatype": "number", "usages": ["fact", "control"]},
        ],
    }])

    await RuntimeOutputExecutor(hass, api, "site-a").async_handle({
        "type": "decision.snapshot",
        "decisions": [{
            "deviceId": "heat-external", "deviceType": "heatPump",
            "temperature": "40", "tankTemperature": "55", "mode": "target",
        }],
    })

    assert calls == [
        ("set_value", {"temperature": 40, "entity_id": "number.flow_target"}),
        ("set_value", {"tankTemperature": 55, "entity_id": "number.tank_target"}),
    ]


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


def _site_condition_api(mappings):
    api = MagicMock()
    api.list_devices = AsyncMock(return_value=[
        {"id": "site-internal", "deviceId": "site-external", "type": "site"},
    ])
    api.list_mappings = AsyncMock(return_value=mappings)
    api.get_device_type_catalog = AsyncMock(return_value=[
        {"type": "site", "concepts": [
            {"concept": "site.power", "datatype": "number", "usages": ["control"]},
        ]},
    ])
    return api


def _site_power_mapping(mode="balance", value_condition=None, entity="number.balance"):
    mapping = {
        "deviceId": "site-internal", "concept": "site.power",
        "direction": "output", "mode": mode,
        "configuration": {"version": 1, "actions": [{
            "type": "serviceCall", "service": "test.execute",
            "target": {"entityId": entity},
            "data": {"value": {"kind": "requestedValue"}},
        }]},
    }
    if value_condition is not None:
        mapping["valueCondition"] = value_condition
    return mapping


async def test_site_balance_value_condition_selects_signed_mapping_and_preserves_value(hass):
    calls = []
    hass.services.async_register("test", "execute", lambda call: calls.append(call.data))
    mappings = [
        _site_power_mapping(value_condition="gtZero", entity="number.import"),
        _site_power_mapping(value_condition="ltZero", entity="number.export"),
        _site_power_mapping(value_condition="eqZero", entity="number.zero"),
    ]
    executor = RuntimeOutputExecutor(hass, _site_condition_api(mappings), "site-a")

    await executor.async_handle({"type": "decision.snapshot", "decisions": [_decision("site", "balance", 2906)]})
    await executor.async_handle({"type": "decision.snapshot", "decisions": [_decision("site", "balance", -1500)]})
    await executor.async_handle({"type": "decision.snapshot", "decisions": [_decision("site", "balance", 0)]})

    assert calls == [
        {"value": 2906, "entity_id": "number.import"},
        {"value": -1500, "entity_id": "number.export"},
        {"value": 0, "entity_id": "number.zero"},
    ]


async def test_site_balance_wrong_condition_is_not_executed(hass):
    calls = []
    hass.services.async_register("test", "execute", lambda call: calls.append(call.data))
    executor = RuntimeOutputExecutor(
        hass,
        _site_condition_api([_site_power_mapping(value_condition="ltZero")]),
        "site-a",
    )

    await executor.async_handle({"type": "decision.snapshot", "decisions": [_decision("site", "balance", 12)]})

    assert calls == []


async def test_site_balance_null_value_condition_preserves_unrestricted_behavior(hass):
    calls = []
    hass.services.async_register("test", "execute", lambda call: calls.append(call.data))
    executor = RuntimeOutputExecutor(
        hass,
        _site_condition_api([_site_power_mapping(entity="number.unrestricted")]),
        "site-a",
    )

    await executor.async_handle({"type": "decision.snapshot", "decisions": [_decision("site", "balance", -8)]})

    assert calls == [{"value": -8, "entity_id": "number.unrestricted"}]
