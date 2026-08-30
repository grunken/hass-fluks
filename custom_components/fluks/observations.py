"""Realtime raw source publishing for persisted input Mappings."""

from __future__ import annotations

import asyncio
import math
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .api import FluksApiClient
from .const import DATA_OBSERVATIONS, DOMAIN, ENERGY_LIFETIME_STORAGE_VERSION

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
        entry_id: str,
        send: Callable[[dict[str, Any]], Awaitable[bool]],
    ) -> None:
        self._hass = hass
        self._api = api
        self._site_id = site_id
        self._send = send
        self._store = Store[dict[str, Any]](
            hass,
            ENERGY_LIFETIME_STORAGE_VERSION,
            f"{DOMAIN}.energy_lifetime.{entry_id}",
            atomic_writes=True,
        )
        self._lifetime: dict[str, dict[str, str | None]] = {}
        self._loaded = False
        self._unsubscribe: Callable[[], None] | None = None
        self._refresh_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._stopped = False

    async def _async_load(self) -> None:
        if self._loaded:
            return
        stored = await self._store.async_load()
        streams = stored.get("streams", {}) if isinstance(stored, dict) else {}
        if isinstance(streams, dict):
            self._lifetime = {
                str(key): dict(value)
                for key, value in streams.items()
                if isinstance(value, dict)
                and isinstance(value.get("lifetime"), str)
                and isinstance(value.get("last"), str)
                and isinstance(value.get("source"), str)
            }
        self._loaded = True

    @staticmethod
    def _stream_key(device_id: str, concept: str) -> str:
        return f"{device_id}|{concept}"

    @staticmethod
    def _reset_marker(state: State) -> str | None:
        marker = state.attributes.get("last_reset")
        return str(marker) if marker is not None else None

    @staticmethod
    def _decimal_value(state: State) -> Decimal:
        value = _raw_value(state.state)
        try:
            number = Decimal(value)
        except InvalidOperation as err:
            raise ValueError("Energy state is not numeric") from err
        if not number.is_finite() or number < 0:
            raise ValueError("Energy state must be a finite non-negative number")
        return number

    @staticmethod
    def _json_decimal(value: Decimal) -> int | float:
        return int(value) if value == value.to_integral_value() else float(value)

    async def async_refresh(self) -> None:
        """Replace subscriptions from the current persisted input Mappings."""
        async with self._refresh_lock:
            if self._stopped:
                return
            await self._async_load()
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
            by_entity: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
            baselines: list[tuple[str, str, str]] = []
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
                entity_id = configuration["entityId"]
                concept = mapping["concept"]
                cumulative = (
                    (configuration.get("source") or {}).get("kind") == "cumulative"
                )
                by_entity[entity_id].append((external_id, concept, cumulative))
                stream = self._lifetime.get(self._stream_key(external_id, concept))
                if cumulative and (stream is None or stream.get("source") != entity_id):
                    baselines.append((entity_id, external_id, concept))
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
            for entity_id, device_id, concept in baselines:
                if (state := self._hass.states.get(entity_id)) is not None:
                    await self._async_publish_cumulative(state, device_id, concept)

    async def _async_state_changed(
        self,
        event: Event,
        mappings: dict[str, list[tuple[str, str, bool]]],
    ) -> None:
        state: State | None = event.data.get("new_state")
        if self._stopped or state is None:
            return
        for device_id, concept, cumulative in mappings.get(state.entity_id, []):
            if cumulative:
                await self._async_publish_cumulative(state, device_id, concept)
                continue
            try:
                value = _json_value(_raw_value(state.state))
            except (TypeError, ValueError):
                continue
            await self._send({"deviceId": device_id, concept: value})

    async def _async_publish_cumulative(
        self, state: State, device_id: str, concept: str
    ) -> None:
        try:
            raw = self._decimal_value(state)
        except ValueError:
            return
        key = self._stream_key(device_id, concept)
        marker = self._reset_marker(state)
        async with self._state_lock:
            stream = self._lifetime.get(key)
            changed = True
            if stream is None:
                lifetime = raw
            elif stream["source"] != state.entity_id:
                lifetime = Decimal(stream["lifetime"])
            else:
                lifetime = Decimal(stream["lifetime"])
                previous = Decimal(stream["last"])
                previous_marker = stream.get("last_reset")
                marker_changed = (
                    marker is not None
                    and previous_marker is not None
                    and marker != previous_marker
                )
                if marker_changed or (
                    raw < previous
                    and state.attributes.get("state_class") == "total_increasing"
                ):
                    lifetime += raw
                elif raw >= previous:
                    lifetime += raw - previous
                else:
                    changed = False
            if changed:
                self._lifetime[key] = {
                    "lifetime": str(lifetime),
                    "source": state.entity_id,
                    "last": str(raw),
                    "last_reset": marker,
                }
                await self._store.async_save({"streams": self._lifetime})
        await self._send(
            {"deviceId": device_id, concept: self._json_decimal(lifetime)}
        )

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
