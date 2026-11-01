"""Tests for the entry-scoped fluks runtime WebSocket transport."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import ClientConnectionError, WSMsgType

from custom_components.fluks.const import API_BASE_URL
from custom_components.fluks.runtime import (
    RUNTIME_HEARTBEAT_SECONDS,
    FluksRuntimeWebSocket,
    runtime_websocket_url,
)

KEY = "fluks_runtime-secret"


class FakeSocket:
    """Small asynchronous WebSocket test double."""

    def __init__(self, messages=(), *, hold=False):
        self.closed = False
        self.sent = []
        self._messages = iter(messages)
        self._hold = hold
        self._released = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            if self._hold and not self.closed:
                await self._released.wait()
            raise StopAsyncIteration

    async def close(self):
        self.closed = True
        self._released.set()

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def ws_connect(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _wait_for(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


def test_runtime_url_is_derived_from_the_configured_base_url():
    assert runtime_websocket_url("https://example.test") == "wss://example.test/ws"
    assert runtime_websocket_url("https://example.test:8443/api") == "wss://example.test:8443/ws"
    assert runtime_websocket_url("http://localhost:8124/") == "ws://localhost:8124/ws"


async def test_connection_uses_integration_authorization_and_heartbeat():
    socket = FakeSocket(hold=True)
    session = FakeSession([socket])
    runtime = FluksRuntimeWebSocket(session, KEY, API_BASE_URL, reconnect_delays=(0,))
    runtime.start(asyncio.create_task)
    await _wait_for(lambda: len(session.calls) == 1)
    url, kwargs = session.calls[0]
    assert url == "wss://app.fluks.one/ws"
    assert kwargs["headers"] == {"Authorization": f"Bearer {KEY}"}
    assert kwargs["heartbeat"] == RUNTIME_HEARTBEAT_SECONDS
    assert kwargs["autoping"] is True
    await runtime.async_stop()


async def test_unexpected_disconnect_reconnects_without_duplicate_socket():
    first = FakeSocket()
    second = FakeSocket(hold=True)
    session = FakeSession([first, second])
    runtime = FluksRuntimeWebSocket(session, KEY, API_BASE_URL, reconnect_delays=(0,))
    runtime.start(asyncio.create_task)
    runtime.start(asyncio.create_task)
    await _wait_for(lambda: len(session.calls) == 2)
    assert first.closed
    assert not second.closed
    assert len(session.calls) == 2
    await runtime.async_stop()
    assert second.closed
    await asyncio.sleep(0)
    assert len(session.calls) == 2


async def test_runtime_status_notifies_disconnect_and_reconnect_with_timestamp():
    first = FakeSocket()
    second = FakeSocket(hold=True)
    session = FakeSession([first, second])
    now = iter((1000, 2000))
    runtime = FluksRuntimeWebSocket(
        session,
        KEY,
        API_BASE_URL,
        reconnect_delays=(0,),
        now_ms=lambda: next(now),
    )
    statuses = []
    runtime.add_status_listener(statuses.append)
    runtime.start(asyncio.create_task)
    await _wait_for(lambda: len(session.calls) == 2)

    assert statuses == [
        {"state": "connected", "disconnected_at": None},
        {"state": "reconnecting", "disconnected_at": 1000},
        {"state": "connected", "disconnected_at": None},
    ]
    assert runtime.status == {"state": "connected", "disconnected_at": None}
    await runtime.async_stop()


async def test_temporary_unavailability_reconnects_in_background():
    socket = FakeSocket(hold=True)
    session = FakeSession([ClientConnectionError(), socket])
    runtime = FluksRuntimeWebSocket(session, KEY, API_BASE_URL, reconnect_delays=(0,))
    runtime.start(asyncio.create_task)
    await _wait_for(lambda: len(session.calls) == 2)
    await runtime.async_stop()


async def test_decision_snapshot_is_parsed_without_execution():
    message = SimpleNamespace(
        type=WSMsgType.TEXT,
        data='{"type":"decision.snapshot","decisions":[]}',
    )
    socket = FakeSocket([message], hold=True)
    session = FakeSession([socket])
    runtime = FluksRuntimeWebSocket(session, KEY, API_BASE_URL, reconnect_delays=(0,))
    handled = []
    async def handle(payload):
        handled.append(payload)
    runtime._message_handler = handle
    runtime.start(asyncio.create_task)
    await _wait_for(lambda: len(handled) == 1)
    assert handled == [{"type": "decision.snapshot", "decisions": []}]
    await runtime.async_stop()


async def test_malformed_and_unsupported_messages_do_not_end_connection():
    messages = [
        SimpleNamespace(type=WSMsgType.TEXT, data="not-json"),
        SimpleNamespace(type=WSMsgType.TEXT, data='{"type":"future.message"}'),
    ]
    socket = FakeSocket(messages, hold=True)
    session = FakeSession([socket])
    runtime = FluksRuntimeWebSocket(session, KEY, API_BASE_URL, reconnect_delays=(0,))
    runtime.start(asyncio.create_task)
    await _wait_for(lambda: runtime._socket is socket)
    await asyncio.sleep(0)
    assert not socket.closed
    await runtime.async_stop()


async def test_observation_send_reuses_current_socket_after_reconnect():
    first = FakeSocket()
    second = FakeSocket(hold=True)
    session = FakeSession([first, second])
    runtime = FluksRuntimeWebSocket(session, KEY, API_BASE_URL, reconnect_delays=(0,))
    runtime.start(asyncio.create_task)
    assert not await runtime.async_send({"deviceId": "device", "site.power": 1})
    await _wait_for(lambda: runtime._socket is second)
    assert await runtime.async_send({"deviceId": "device", "site.power": 2})
    false_observation = {"deviceId": "device", "electricVehicle.connected": False}
    assert await runtime.async_send(false_observation)
    assert second.sent == [
        {"deviceId": "device", "site.power": 2},
        false_observation,
    ]
    assert len(session.calls) == 2
    await runtime.async_stop()


async def test_concept_keyed_decisions_are_dispatched_on_connect_live_and_reconnect():
    import json
    first = FakeSocket([
        SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps({"electricVehicle.power": {"deviceId": "ev", "value": "6350", "mode": "target"}})),
        SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps({"site.power": {"deviceId": "site", "value": "1000", "mode": "target"}})),
    ])
    second = FakeSocket([
        SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps({"electricVehicle.power": {"deviceId": "ev", "value": "0", "mode": "target"}})),
    ], hold=True)
    handler = AsyncMock()
    runtime = FluksRuntimeWebSocket(FakeSession([first, second]), KEY, API_BASE_URL, message_handler=handler, reconnect_delays=(0,))
    runtime.start(asyncio.create_task)
    await _wait_for(lambda: handler.await_count == 3)
    assert [call.args[0] for call in handler.await_args_list] == [
        {"type": "decision.snapshot", "decisions": [{"deviceId": "ev", "deviceType": "electricVehicle", "power": "6350", "mode": "target"}]},
        {"type": "decision.snapshot", "decisions": [{"deviceId": "site", "deviceType": "site", "power": "1000", "mode": "target"}]},
        {"type": "decision.snapshot", "decisions": [{"deviceId": "ev", "deviceType": "electricVehicle", "power": "0", "mode": "target"}]},
    ]
    await runtime.async_stop()


async def test_concept_keyed_batch_preserves_zero_false_and_release_null():
    handler = AsyncMock()
    runtime = FluksRuntimeWebSocket(FakeSession([]), KEY, API_BASE_URL, message_handler=handler)
    await runtime._handle_message({
        "battery.power": {"deviceId": "battery", "value": 0, "mode": "hold"},
        "waterHeater.state": {"deviceId": "water", "value": False},
        "heatPump.temperature": {"deviceId": "heat", "value": None, "mode": "release"},
        "invalid": {}, "solar.power": {"deviceId": "solar"},
    })
    handler.assert_awaited_once_with({"type": "decision.snapshot", "decisions": [
        {"deviceId": "battery", "deviceType": "battery", "power": 0, "mode": "hold"},
        {"deviceId": "water", "deviceType": "waterHeater", "state": False, "mode": None},
        {"deviceId": "heat", "deviceType": "heatPump", "temperature": None, "mode": "release"},
    ]})


async def test_balance_recheck_does_not_block_following_websocket_site_target(hass):
    import json
    from custom_components.fluks.output_execution import RuntimeOutputExecutor

    devices = [{"id": kind + "-internal", "deviceId": kind, "type": kind} for kind in ("site", "battery")]
    mappings = [{
        "deviceId": kind + "-internal", "direction": "output", "concept": kind + ".power", "mode": mode,
        "configuration": {"version": 1, "actions": [{
            "type": "serviceCall", "service": "test.execute", "target": {"entityId": entity},
            "data": {"value": {"kind": "requestedValue"}} if kind == "battery" else {},
        }]},
    } for kind, mode, entity in [("site", "balance", "number.balance"), ("site", "target", "number.target"), ("battery", "charge", "number.battery")]]
    api = SimpleNamespace(
        list_devices=AsyncMock(return_value=devices), list_mappings=AsyncMock(return_value=mappings),
        get_device_type_catalog=AsyncMock(return_value=[{
            "type": kind, "concepts": [{"concept": kind + ".power", "datatype": "number", "usages": ["control"]}],
        } for kind in ("site", "battery")]),
    )
    waiting, elapsed, target_received = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls, delays, tasks = [], [], []
    def receive(call):
        calls.append(dict(call.data))
        if call.data["entity_id"] == "number.target":
            target_received.set()
    hass.services.async_register("test", "execute", receive)
    async def sleep(delay):
        delays.append(delay)
        waiting.set()
        await elapsed.wait()
    def create(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task
    executor = RuntimeOutputExecutor(hass, api, "site", sleep=sleep, create_task=create)
    messages = [
        {"battery.power": {"deviceId": "battery", "mode": "target", "value": "7468"},
         "site.power": {"deviceId": "site", "mode": "balance", "value": "0"}},
        {"site.power": {"deviceId": "site", "mode": "target", "value": "1000"}},
    ]
    socket = FakeSocket([SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(message)) for message in messages], hold=True)
    runtime = FluksRuntimeWebSocket(FakeSession([socket]), KEY, API_BASE_URL, message_handler=executor.async_handle)
    runtime.start(asyncio.create_task)
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        await asyncio.wait_for(target_received.wait(), 1)
        assert calls == [{"entity_id": "number.balance"}, {"entity_id": "number.target"}]
        assert delays == [5.0] and len(tasks) == 1 and not tasks[0].done()
        elapsed.set()
        await asyncio.gather(*tasks)
        assert calls == [
            {"entity_id": "number.balance"}, {"entity_id": "number.target"},
            {"entity_id": "number.battery", "value": 7468},
        ]
        assert delays == [5.0] and len(tasks) == 1
    finally:
        await runtime.async_stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
