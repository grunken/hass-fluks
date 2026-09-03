"""Entry-scoped transport for the fluks runtime WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientWebSocketResponse, WSMsgType

_LOGGER = logging.getLogger(__name__)

RUNTIME_PATH = "/ws"
RUNTIME_HEARTBEAT_SECONDS = 30
RECONNECT_DELAYS = (1, 2, 5, 10, 30)


def runtime_websocket_url(base_url: str) -> str:
    """Derive the runtime WebSocket URL from the configured HTTP base URL."""
    parsed = urlsplit(base_url)
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme.lower())
    if scheme is None or not parsed.netloc:
        raise ValueError("The fluks API base URL must use http or https")
    return urlunsplit((scheme, parsed.netloc, RUNTIME_PATH, "", ""))


class FluksRuntimeWebSocket:
    """Maintain one authenticated runtime connection for one config entry."""

    def __init__(
        self,
        session: ClientSession,
        integration_key: str,
        base_url: str,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        reconnect_delays: tuple[float, ...] = RECONNECT_DELAYS,
        message_handler: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._session = session
        self._integration_key = integration_key
        self._url = runtime_websocket_url(base_url)
        self._sleep = sleep
        self._reconnect_delays = reconnect_delays
        self._message_handler = message_handler
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._socket: ClientWebSocketResponse | None = None

    @property
    def url(self) -> str:
        """Return the credential-free runtime URL."""
        return self._url

    def start(self, create_task: Callable[[Awaitable[None]], asyncio.Task[None]]) -> None:
        """Start the connection loop once."""
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = create_task(self._run())

    async def async_stop(self) -> None:
        """Close the socket and permanently stop this manager."""
        self._stopping = True
        socket = self._socket
        if socket is not None and not socket.closed:
            await socket.close()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def async_send(self, payload: dict[str, Any]) -> bool:
        """Send through the currently connected runtime socket, if available."""
        socket = self._socket
        if self._stopping or socket is None or socket.closed:
            return False
        try:
            await socket.send_json(payload)
        except (ClientError, OSError, RuntimeError):
            return False
        return True

    async def _run(self) -> None:
        attempt = 0
        while not self._stopping:
            try:
                socket = await self._session.ws_connect(
                    self._url,
                    headers={"Authorization": f"Bearer {self._integration_key}"},
                    heartbeat=RUNTIME_HEARTBEAT_SECONDS,
                    autoping=True,
                )
                self._socket = socket
                attempt = 0
                await self._receive(socket)
            except asyncio.CancelledError:
                raise
            except (ClientError, OSError, TimeoutError):
                _LOGGER.debug("fluks runtime WebSocket is unavailable; reconnecting")
            except Exception:  # noqa: BLE001 - transport failures must not kill the loop
                _LOGGER.debug(
                    "Unexpected fluks runtime WebSocket transport failure; reconnecting"
                )
            finally:
                socket = self._socket
                self._socket = None
                if socket is not None and not socket.closed:
                    with contextlib.suppress(Exception):
                        await socket.close()

            if self._stopping:
                break
            delay = self._reconnect_delays[min(attempt, len(self._reconnect_delays) - 1)]
            attempt += 1
            await self._sleep(delay)

    async def _receive(self, socket: ClientWebSocketResponse) -> None:
        async for message in socket:
            if message.type is WSMsgType.TEXT:
                try:
                    payload = json.loads(message.data)
                except (TypeError, ValueError):
                    _LOGGER.debug("Ignoring malformed fluks runtime message")
                    continue
                await self._handle_message(payload)
            elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                break

    async def _handle_message(self, payload: Any) -> None:
        """Validate and deliver supported runtime message envelopes."""
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            _LOGGER.debug("Ignoring malformed fluks runtime message envelope")
            return
        if payload["type"] == "decision.snapshot":
            if self._message_handler is not None:
                await self._message_handler(payload)
            return
        _LOGGER.debug("Ignoring unsupported fluks runtime message type %s", payload["type"])
