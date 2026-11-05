"""EV ownership follows actual Mapping results and local HA observations."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.fluks.const import EVENT_DECISION_LOG
from custom_components.fluks.observations import RealtimeObservationPublisher
from custom_components.fluks.output_execution import RuntimeOutputExecutor
from custom_components.fluks.plan import LocalPlan
from custom_components.fluks.runtime import FluksRuntimeWebSocket


class MemoryStore:
    def __init__(self):
        self.data = None

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


def context():
    mappings = [{
        "deviceId": "car-internal", "concept": "electricVehicle.power", "direction": "input",
        "configuration": {"entityId": "sensor.car_power"},
    }]
    for mode, option in (("charge", "charge"), ("hold", "idle"), ("discharge", "discharge"), ("release", "auto")):
        actions = [{
            "type": "serviceCall", "service": "select.select_option",
            "target": {"entityId": "select.car_mode"},
            "data": {"option": {"kind": "literal", "value": option}},
        }]
        if mode == "charge":
            actions.append({
                "type": "serviceCall", "service": "number.set_value",
                "target": {"entityId": "number.car_current"},
                "data": {"value": {"kind": "requestedValue", "transforms": [
                    {"type": "powerToCurrent", "phases": 1, "voltage": 230},
                    {"type": "round", "decimals": 0},
                ]}},
            })
        mappings.append({
            "deviceId": "car-internal", "concept": "electricVehicle.power",
            "direction": "output", "mode": mode,
            "configuration": {"version": 1, "actions": actions},
        })
    return {
        "devices": [{"id": "car-internal", "deviceId": "car", "type": "electricVehicle"}],
        "mappings": mappings,
        "catalog": [{"type": "electricVehicle", "concepts": [{
            "concept": "electricVehicle.power", "datatype": "number", "usages": ["fact", "control"],
        }]}],
    }


async def setup(hass, *, cache=None, store=None, initialize=True):
    cache = cache or context()
    api = MagicMock()
    api.list_devices = AsyncMock(return_value=cache["devices"])
    api.list_mappings = AsyncMock(return_value=cache["mappings"])
    api.get_device_type_catalog = AsyncMock(return_value=cache["catalog"])
    store = store or MemoryStore()
    calls, events = [], []
    hass.bus.async_listen(EVENT_DECISION_LOG, lambda event: events.append(event.data))
    executor = RuntimeOutputExecutor(
        hass, api, "site", ownership_store=store, sleep=AsyncMock(),
        observe_current=lambda: observations.async_observe_current(),
    )
    send = AsyncMock(return_value=False)
    observations = RealtimeObservationPublisher(
        hass, api, "site", "entry", send, observe=executor.async_observe,
        observe_concepts={"electricVehicle.power"}, get_cached_context=lambda: cache,
    )
    observations._store = MemoryStore()

    async def apply(call):
        calls.append(dict(call.data))
        hass.states.async_set(
            call.data["entity_id"], str(call.data.get("option", call.data.get("value"))),
        )

    hass.services.async_register("select", "select_option", apply)
    hass.services.async_register("number", "set_value", apply)
    if initialize:
        await observations.async_refresh(context=cache)
    return SimpleNamespace(
        executor=executor, observations=observations, api=api, store=store,
        calls=calls, events=events, send=send, cache=cache, apply=apply,
    )


@pytest.fixture
async def ev(hass):
    hass.states.async_set("sensor.car_power", "0")
    hass.states.async_set("select.car_mode", "auto")
    hass.states.async_set("number.car_current", "0")
    result = await setup(hass)
    yield result
    await result.observations.async_stop()


async def decide(ev, value="3000", mode="target", *, offline=False, device="car", **kwargs):
    await ev.executor.async_handle({"type": "decision.snapshot", "decisions": [{
        "deviceId": device, "deviceType": "electricVehicle", "power": value, "mode": mode,
    }]}, context=ev.cache if offline else None, **kwargs)


async def power(hass, value):
    hass.states.async_set("sensor.car_power", str(value))
    await hass.async_block_till_done()


@pytest.mark.parametrize("offline", [False, True])
@pytest.mark.parametrize("value,mode", [("6350", "target"), ("0", "target"), ("-2300", "target"), (None, "release")])
async def test_manual_charging_blocks_the_complete_decision_then_resumes(hass, ev, offline, value, mode):
    await power(hass, 3000)
    assert ev.store.data["ev_charging"]["car"] == {"owner": "manual", "active": True}
    await decide(ev, value, mode, offline=offline)
    await hass.async_block_till_done()
    assert ev.calls == []
    assert any("manual EV charging is active" in event["message"] for event in ev.events)
    # Local detection works even though no observation can be delivered to Core.
    assert ev.send.await_args.args[0] == {"deviceId": "car", "electricVehicle.power": 3000}
    await power(hass, 0)
    assert "ev_charging" not in ev.store.data
    await decide(ev, offline=offline)
    assert ev.calls == [
        {"option": "charge", "entity_id": "select.car_mode"},
        {"value": 13, "entity_id": "number.car_current"},
    ]
    assert ev.store.data["ev_charging"]["car"]["owner"] == "fluks"


@pytest.mark.parametrize("noop", [False, True])
@pytest.mark.parametrize("mode", ["target", "charge"])
async def test_success_or_satisfied_start_establishes_ownership(hass, ev, noop, mode):
    if noop:
        hass.states.async_set("select.car_mode", "charge")
        hass.states.async_set("number.car_current", "13.0")
    await decide(ev, mode=mode)
    assert len(ev.calls) == (0 if noop else 2)
    assert ev.store.data["ev_charging"]["car"] == {"owner": "fluks", "active": False}
    # A delayed power sensor may repeat zero before the charger starts.
    await ev.observations.async_observe_current()
    await power(hass, 3000)
    assert ev.store.data["ev_charging"]["car"] == {"owner": "fluks", "active": True}
    ev.calls.clear()
    await decide(ev, "4000")
    await decide(ev, "0")
    await decide(ev, "-1000")
    await decide(ev, None, "release")
    assert [call.get("option", call.get("value")) for call in ev.calls] == ["charge", 17, "idle", "discharge", "auto"]
    await power(hass, 0)
    await power(hass, 2000)
    await decide(ev, None, "release")
    assert ev.store.data["ev_charging"]["car"]["owner"] == "manual"
    assert len(ev.calls) == 5


@pytest.mark.parametrize("failure", [None, "transient", "call", "verification", "reconnect"])
async def test_observation_during_start_waits_for_the_whole_mapping_result(hass, ev, failure):
    attempts = []

    async def start(call):
        await ev.apply(call)
        hass.states.async_set("sensor.car_power", "3000")
        await ev.observations.async_observe_current()
        if failure == "reconnect":
            authoritative[0] = False

    async def set_current(call):
        attempts.append(dict(call.data))
        if failure == "call" or failure == "transient" and len(attempts) == 1:
            raise RuntimeError("charger refused current")
        if failure != "verification":
            await ev.apply(call)

    authoritative = [True]
    hass.services.async_register("select", "select_option", start)
    hass.services.async_register("number", "set_value", set_current)
    if failure == "reconnect":
        with pytest.raises(asyncio.CancelledError):
            await decide(ev, offline=True, guard=lambda: authoritative[0])
    else:
        await decide(ev)
    failed = failure in {"call", "verification", "reconnect"}
    expected_owner = "manual" if failed else "fluks"
    assert ev.store.data["ev_charging"]["car"] == {"owner": expected_owner, "active": True}
    assert len(attempts) == (0 if failure == "reconnect" else 3 if failed else 2 if failure == "transient" else 1)
    if failed:
        before = list(ev.calls)
        await decide(ev, None, "release")
        assert ev.calls == before


@pytest.mark.parametrize("invalid", [False, True])
async def test_missing_or_invalid_start_mapping_does_not_claim_ownership(hass, ev, invalid):
    charge = next(mapping for mapping in ev.cache["mappings"] if mapping.get("mode") == "charge")
    if invalid:
        charge["configuration"] = {"version": 1, "actions": []}
    else:
        ev.cache["mappings"].remove(charge)
    await decide(ev)
    assert ev.calls == []
    assert not ev.store.data or "ev_charging" not in ev.store.data
    await power(hass, 3000)
    assert ev.store.data["ev_charging"]["car"]["owner"] == "manual"


async def test_manual_session_is_not_claimed_by_an_already_satisfied_start(hass, ev):
    await power(hass, 3000)
    hass.states.async_set("select.car_mode", "charge")
    hass.states.async_set("number.car_current", "13")
    await decide(ev)
    assert ev.calls == []
    assert ev.store.data["ev_charging"]["car"]["owner"] == "manual"


@pytest.mark.parametrize("unavailable", ["unknown", "unavailable", "nonnumeric"])
async def test_unknown_power_cannot_clear_manual_protection(hass, ev, unavailable):
    await power(hass, 3000)
    await power(hass, unavailable)
    await decide(ev, "0")
    assert ev.calls == []
    assert ev.store.data["ev_charging"]["car"]["owner"] == "manual"


async def test_decision_checks_current_state_before_a_queued_observation(hass, ev):
    # Do not wait for the state event's background publishing task.
    hass.states.async_set("sensor.car_power", "3000")
    await decide(ev, None, "release")
    assert ev.calls == []


async def test_initial_cached_mappings_detect_manual_charging_before_first_decision(hass):
    hass.states.async_set("sensor.car_power", "3000")
    ev = await setup(hass, initialize=False)
    await decide(ev, "0", offline=True)
    assert ev.calls == []
    ev.api.list_devices.assert_not_awaited()
    ev.api.list_mappings.assert_not_awaited()
    await ev.observations.async_stop()


async def test_attribute_and_inverted_power_use_existing_mapping_transforms(hass):
    cache = context()
    cache["mappings"][0]["configuration"] = {
        "entityId": "sensor.car_power", "attribute": "power",
        "transforms": [{"type": "invert"}, {"type": "scale", "factor": 1000}],
    }
    hass.states.async_set("sensor.car_power", "ready", {"power": 0})
    ev = await setup(hass, cache=cache)
    hass.states.async_set("sensor.car_power", "ready", {"power": -3})
    await hass.async_block_till_done()
    await decide(ev, "0", offline=True)
    assert ev.calls == []
    assert ev.store.data["ev_charging"]["car"]["owner"] == "manual"
    # This local protection does not alter the existing outbound source contract.
    assert ev.send.await_args.args[0] == {"deviceId": "car", "electricVehicle.power": -3}
    await ev.observations.async_stop()


@pytest.mark.parametrize("owner", ["manual", "fluks"])
async def test_disconnect_restart_and_cached_plan_preserve_ownership(hass, ev, freezer, owner):
    if not hass.is_running:
        await hass.async_start()
    if owner == "fluks":
        await decide(ev)
    await power(hass, 3000)
    await ev.observations.async_stop()
    restored = await setup(hass, cache=ev.cache, store=ev.store, initialize=False)
    for method in (restored.api.list_devices, restored.api.list_mappings, restored.api.get_device_type_catalog):
        method.side_effect = RuntimeError("Core is unreachable")
    await restored.observations.async_refresh()
    assert restored.store.data["ev_charging"]["car"]["owner"] == owner
    plan = LocalPlan(hass, "entry", "site", restored.executor, store=MemoryStore())
    await plan.async_load()
    plan.data["cache"] = restored.cache
    now = dt_util.utcnow()
    await plan.async_receive({
        "generatedAt": now.isoformat(), "expiresAt": (now + timedelta(seconds=10)).isoformat(),
        "decisions": [{"at": (now + timedelta(seconds=5)).isoformat(), "electricVehicle.power": {
            "deviceId": "car", "mode": "target", "value": "0",
        }}, {"at": (now + timedelta(seconds=10)).isoformat(), "electricVehicle.power": {
            "deviceId": "car", "mode": "release", "value": None,
        }}],
    })
    for seconds in (5, 10):
        freezer.move_to(now + timedelta(seconds=seconds))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
    assert [call["option"] for call in restored.calls] == ([] if owner == "manual" else ["idle", "auto"])
    assert len(plan.data["claimed"]) == 2
    await plan.async_stop()
    # Reconnection continues through the same executor without resetting ownership.
    restored.api.list_devices.side_effect = None
    restored.api.list_mappings.side_effect = None
    restored.api.get_device_type_catalog.side_effect = None
    await decide(restored, "2000")
    if owner == "manual":
        assert restored.calls == []
    else:
        assert restored.calls[-1] == {"value": 9, "entity_id": "number.car_current"}
    await restored.observations.async_stop()


async def test_live_websocket_start_and_disconnect_do_not_reset_ownership(hass, ev):
    runtime = FluksRuntimeWebSocket(MagicMock(), "key", "http://example.test", message_handler=ev.executor.async_handle)
    runtime._set_status("connected")
    await runtime._handle_message({"electricVehicle.power": {"deviceId": "car", "mode": "target", "value": "3000"}})
    runtime._set_status("reconnecting")
    await power(hass, 3000)
    await decide(ev, "0", offline=True)
    assert ev.calls[-1] == {"option": "idle", "entity_id": "select.car_mode"}
    assert ev.store.data["ev_charging"]["car"]["owner"] == "fluks"


async def test_cached_subscriptions_recover_on_reconnect_and_stop_on_unload(hass):
    hass.states.async_set("sensor.car_power", "3000")
    ev = await setup(hass, initialize=False)
    ev.api.list_devices.side_effect = RuntimeError("Core unavailable")
    await ev.observations.async_refresh()
    assert ev.observations._using_cached_context
    ev.api.list_devices.side_effect = None
    ev.observations._async_runtime_status_changed({"state": "connected"})
    await hass.async_block_till_done()
    assert not ev.observations._using_cached_context
    assert ev.api.list_devices.await_count == 2
    await ev.observations.async_stop()
    await power(hass, 0)
    assert ev.send.await_count == 0
    assert ev.store.data["ev_charging"]["car"]["owner"] == "manual"


@pytest.mark.parametrize("other_type", ["electricVehicle", "battery"])
async def test_manual_protection_is_scoped_to_the_observed_ev(hass, ev, other_type):
    await power(hass, 3000)
    other = deepcopy(ev.cache["devices"][0])
    other.update(id="other-internal", deviceId="other", type=other_type)
    ev.cache["devices"].append(other)
    other_mapping = deepcopy(next(mapping for mapping in ev.cache["mappings"] if mapping.get("mode") == "charge"))
    other_mapping["deviceId"] = "other-internal"
    other_mapping["concept"] = f"{other_type}.power"
    ev.cache["mappings"].append(other_mapping)
    if other_type == "electricVehicle":
        await decide(ev, device="other")
    else:
        ev.cache["catalog"].append({"type": "battery", "concepts": [{
            "concept": "battery.power", "datatype": "number", "usages": ["control"],
        }]})
        await ev.executor.async_handle({"type": "decision.snapshot", "decisions": [{
            "deviceId": "other", "deviceType": "battery", "power": "3000", "mode": "target",
        }]})
    assert len(ev.calls) == 2
    assert ev.store.data["ev_charging"]["car"]["owner"] == "manual"
    assert ev.store.data["ev_charging"].get("other") == (
        {"owner": "fluks", "active": False} if other_type == "electricVehicle" else None
    )
