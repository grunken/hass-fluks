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
    assert second.sent == [{"deviceId": "device", "site.power": 2}]
    assert len(session.calls) == 2
    await runtime.async_stop()
