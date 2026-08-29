"""Realtime raw source publishing for persisted input Mappings."""

from __future__ import annotations

import asyncio
import math
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

from .api import FluksApiClient
from .const import DATA_OBSERVATIONS, DOMAIN

INVALID_STATES = {"", "unknown", "unavailable", "none", "null"}
_LOGGER = logging.getLogger(__name__)


def _raw_value(state: str) -> str:
    value = state.strip()
    if value.lower() in INVALID_STATES:
        raise ValueError("Invalid Home Assistant state")
    return value


def _numeric_value(value: Any) -> int | float:
    if isinstance(value, bool):
        raise ValueError("Boolean is not numeric")
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str) and value.lstrip("+-").isdigit():
        return int(value)
    else:
        try:
            number = float(value)
        except (TypeError, ValueError) as err:
            raise ValueError("State is not numeric") from err
    if not math.isfinite(number):
        raise ValueError("Non-finite Home Assistant state")
    return number


def _json_value(value: Any) -> str | int | float | bool:
    if isinstance(value, str):
        try:
            return _numeric_value(value)
        except ValueError:
            return value
    return value


class RealtimeObservationPublisher:
    """Maintain one entry-scoped set of mapped HA state listeners."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: FluksApiClient,
        site_id: str,
        send: Callable[[dict[str, Any]], Awaitable[bool]],
    ) -> None:
        self._hass = hass
        self._api = api
        self._site_id = site_id
        self._send = send
        self._unsubscribe: Callable[[], None] | None = None
        self._refresh_lock = asyncio.Lock()
        self._stopped = False

    async def async_refresh(self) -> None:
        """Replace subscriptions from the current persisted input Mappings."""
        async with self._refresh_lock:
            if self._stopped:
                return
            try:
                devices = await self._api.list_devices(self._site_id)
                mappings = await self._api.list_mappings(self._site_id)
            except Exception:  # noqa: BLE001 - discovery outage must not break HA
                _LOGGER.debug("Unable to refresh fluks realtime input Mappings")
                return
            if self._stopped:
                return
            external_ids = {
                str(device["id"]): str(device["deviceId"])
                for device in devices
                if isinstance(device.get("id"), str)
                and isinstance(device.get("deviceId"), str)
            }
            by_entity: dict[str, list[tuple[str, str]]] = defaultdict(list)
            for mapping in mappings:
                configuration = mapping.get("configuration")
                external_id = external_ids.get(str(mapping.get("deviceId")))
                if (
                    mapping.get("direction") != "input"
                    or not isinstance(configuration, dict)
                    or not isinstance(configuration.get("entityId"), str)
                    or not isinstance(mapping.get("concept"), str)
                    or external_id is None
                ):
                    continue
                by_entity[configuration["entityId"]].append(
                    (external_id, mapping["concept"])
                )
            unsubscribe = self._unsubscribe
            self._unsubscribe = None
            if unsubscribe is not None:
                unsubscribe()
            if by_entity:
                @callback
                def _state_changed(event: Event) -> None:
                    self._hass.async_create_task(
                        self._async_state_changed(event, by_entity),
                        "fluks observation",
                    )

                self._unsubscribe = async_track_state_change_event(
                    self._hass,
                    list(by_entity),
                    _state_changed,
                )

    async def _async_state_changed(
        self,
        event: Event,
        mappings: dict[str, list[tuple[str, str]]],
    ) -> None:
        state: State | None = event.data.get("new_state")
        if self._stopped or state is None:
            return
        for device_id, concept in mappings.get(state.entity_id, []):
            try:
                value = _json_value(_raw_value(state.state))
            except (TypeError, ValueError):
                continue
            await self._send({"deviceId": device_id, concept: value})

    async def async_stop(self) -> None:
        """Remove all listeners and prevent later refreshes."""
        self._stopped = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


async def async_refresh_observations(hass: HomeAssistant, entry_id: str) -> None:
    """Refresh an active entry publisher after Mapping persistence changes."""
    publisher = hass.data.get(DOMAIN, {}).get(DATA_OBSERVATIONS, {}).get(entry_id)
    if publisher is not None:
        await publisher.async_refresh()
