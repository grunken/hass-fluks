"""Realtime raw source publishing for persisted input Mappings."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from homeassistant.const import UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter

from .api import FluksApiClient
from .const import DATA_OBSERVATIONS, DOMAIN, ENERGY_LIFETIME_STORAGE_VERSION

INVALID_STATES = {"", "unknown", "unavailable", "none", "null"}
TEMPERATURE_UNITS = {
    UnitOfTemperature.CELSIUS,
    UnitOfTemperature.FAHRENHEIT,
    UnitOfTemperature.KELVIN,
}
AVAILABILITY_COOLDOWN = timedelta(minutes=5)
_LOGGER = logging.getLogger(__name__)


def _raw_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if value.lower() in INVALID_STATES:
            raise ValueError("Invalid Home Assistant state")
    if value is None or isinstance(value, (dict, list)):
        raise ValueError("Home Assistant value is not scalar")
    return value


def _source_value(state: State, attribute: str | None) -> Any:
    """Read the configured state or attribute value."""
    if attribute is None:
        return _raw_value(state.state)
    if attribute not in state.attributes:
        raise ValueError("Home Assistant attribute is unavailable")
    return _raw_value(state.attributes[attribute])


def _attribute_temperature_unit(
    hass: HomeAssistant, state: State, attribute: str
) -> str | None:
    """Return a reliable explicit or HA-native temperature attribute unit."""
    for key in (f"{attribute}_unit", "temperature_unit", "unit_of_measurement"):
        unit = state.attributes.get(key)
        if unit in TEMPERATURE_UNITS:
            return str(unit)
    domain = state.entity_id.split(".", 1)[0]
    if domain in {"climate", "water_heater"} or state.attributes.get(
        "device_class"
    ) == "temperature":
        unit = hass.config.units.temperature_unit
        return str(unit) if unit in TEMPERATURE_UNITS else None
    return None


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
        *,
        availability_cooldown: float = AVAILABILITY_COOLDOWN.total_seconds(),
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
        self._derived: dict[str, dict[str, str | None]] = {}
        self._derived_needs_rebaseline: set[str] = set()
        self._availability: dict[str, dict[str, str]] = {}
        self._availability_tasks: dict[str, asyncio.Task[None]] = {}
        self._availability_task_sources: dict[str, str] = {}
        self._availability_cooldown = availability_cooldown
        self._loaded = False
        self._unsubscribe: Callable[[], None] | None = None
        self._refresh_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._stopped = False

    async def _async_load(self) -> None:
        if self._loaded:
            return
        stored = await self._store.async_load()
        stored = stored if isinstance(stored, dict) else {}
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
        derived = stored.get("derived", {})
        if isinstance(derived, dict):
            self._derived = {
                str(key): dict(value)
                for key, value in derived.items()
                if isinstance(value, dict)
                and isinstance(value.get("lifetime"), str)
                and isinstance(value.get("source"), str)
            }
            self._derived_needs_rebaseline = set(self._derived)
        availability = stored.get("availability", {})
        if isinstance(availability, dict):
            self._availability = {
                str(key): {
                    "source": str(value["source"]),
                }
                for key, value in availability.items()
                if isinstance(value, dict)
                and isinstance(value.get("source"), str)
            }
        self._loaded = True

    def _store_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"streams": self._lifetime}
        if self._derived:
            payload["derived"] = self._derived
        if self._availability:
            payload["availability"] = self._availability
        return payload

    @staticmethod
    def _stream_key(device_id: str, concept: str) -> str:
        return f"{device_id}|{concept}"

    @staticmethod
    def _reset_marker(state: State) -> str | None:
        marker = state.attributes.get("last_reset")
        return str(marker) if marker is not None else None

    @staticmethod
    def _decimal_value(state: State, attribute: str | None) -> Decimal:
        value = _source_value(state, attribute)
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as err:
            raise ValueError("Energy state is not numeric") from err
        if not number.is_finite() or number < 0:
            raise ValueError("Energy state must be a finite non-negative number")
        return number

    @staticmethod
    def _rated_power(properties: Any) -> Decimal | None:
        if not isinstance(properties, dict) or properties.get("ratedPowerW") is None:
            return None
        try:
            value = Decimal(str(properties["ratedPowerW"]))
        except (InvalidOperation, ValueError):
            return None
        return value if value.is_finite() and value >= 0 else None

    @staticmethod
    def _operating_state(state: State, attribute: str | None) -> str | None:
        try:
            value = _source_value(state, attribute)
        except ValueError:
            return None
        if not isinstance(value, str):
            return None
        value = value.strip().lower()
        return value if value in {"heating", "idle"} else None

    @staticmethod
    def _json_decimal(value: Decimal) -> int | float:
        value = Decimal(str(value))
        return int(value) if value == value.to_integral_value() else float(value)

    def _mapped_value(
        self,
        state: State,
        attribute: str | None,
        canonical_temperature_unit: str | None,
        mapping_declares_unit: bool,
    ) -> Any:
        value = _source_value(state, attribute)
        if (
            attribute is None
            or canonical_temperature_unit is None
            or mapping_declares_unit
        ):
            return value
        source_unit = _attribute_temperature_unit(self._hass, state, attribute)
        try:
            number = _numeric_value(value)
        except ValueError:
            return value
        if source_unit is None:
            return value
        converted = TemperatureConverter.convert(
            number, source_unit, canonical_temperature_unit
        )
        return f"{converted:g} {canonical_temperature_unit}"

    async def async_refresh(self) -> None:
        """Replace subscriptions from the current persisted input Mappings."""
        async with self._refresh_lock:
            if self._stopped:
                return
            await self._async_load()
            try:
                devices = await self._api.list_devices(self._site_id)
                mappings = await self._api.list_mappings(self._site_id)
                catalog = (
                    await self._api.get_device_type_catalog()
                    if any(
                        isinstance(item.get("configuration"), dict)
                        and item["configuration"].get("attribute")
                        for item in mappings
                    )
                    else []
                )
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
            devices_by_id = {
                str(device["id"]): device
                for device in devices
                if isinstance(device.get("id"), str)
            }
            temperature_units = {
                str(concept["concept"]): str(concept["unit"])
                for device_type in catalog
                if isinstance(device_type, dict)
                for concept in device_type.get("concepts", [])
                if isinstance(concept, dict)
                and concept.get("datatype") == "number"
                and concept.get("unit") in TEMPERATURE_UNITS
                and isinstance(concept.get("concept"), str)
            }
            by_entity: dict[
                str,
                list[tuple[str, str, bool, str | None, str | None, bool]],
            ] = defaultdict(list)
            configured: dict[str, set[str]] = defaultdict(set)
            active_sources: dict[str, str] = {}
            state_sources: dict[str, tuple[str, str | None]] = {}
            baselines: list[tuple[str, str, str, str | None]] = []
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
                attribute = configuration.get("attribute")
                if attribute is not None and not isinstance(attribute, str):
                    continue
                concept = mapping["concept"]
                internal_id = str(mapping["deviceId"])
                configured[internal_id].add(concept)
                active_sources[self._stream_key(external_id, concept)] = self._source_id(
                    entity_id, attribute
                )
                if concept == "spaceHeater.state":
                    state_sources[internal_id] = (entity_id, attribute)
                cumulative = (
                    (configuration.get("source") or {}).get("kind") == "cumulative"
                )
                by_entity[entity_id].append(
                    (
                        external_id,
                        concept,
                        cumulative,
                        attribute,
                        temperature_units.get(concept),
                        isinstance(configuration.get("unit"), str),
                    )
                )
                stream = self._lifetime.get(self._stream_key(external_id, concept))
                source = self._source_id(entity_id, attribute)
                if cumulative and (stream is None or stream.get("source") != source):
                    baselines.append((entity_id, external_id, concept, attribute))
            for key in list(self._availability_tasks):
                if key not in active_sources:
                    self._cancel_availability_task(key)
                elif self._availability_task_sources.get(key) != active_sources[key]:
                    self._cancel_availability_task(key)
            stale_availability = set(self._availability) - set(active_sources)
            if stale_availability:
                for key in stale_availability:
                    self._availability.pop(key, None)
                await self._store.async_save(self._store_payload())
            derived_by_entity: dict[
                str,
                list[tuple[str, bool, bool, Decimal, str | None]],
            ] = defaultdict(list)
            for internal_id, source in state_sources.items():
                device = devices_by_id.get(internal_id)
                if not device or device.get("type") != "spaceHeater":
                    continue
                external_id = external_ids.get(internal_id)
                rated_power = self._rated_power(device.get("properties"))
                if external_id is None or rated_power is None:
                    continue
                need_power = "spaceHeater.power" not in configured[internal_id]
                need_energy = "spaceHeater.energy" not in configured[internal_id]
                if need_power or need_energy:
                    derived_by_entity[source[0]].append(
                        (external_id, need_power, need_energy, rated_power, source[1])
                    )
            unsubscribe = self._unsubscribe
            self._unsubscribe = None
            if unsubscribe is not None:
                unsubscribe()
            if by_entity or derived_by_entity:
                @callback
                def _state_changed(event: Event) -> None:
                    self._hass.async_create_task(
                        self._async_state_changed(event, by_entity, derived_by_entity),
                        "fluks observation",
                    )

                self._unsubscribe = async_track_state_change_event(
                    self._hass,
                    list(by_entity),
                    _state_changed,
                )
            for entity_id, device_id, concept, attribute in baselines:
                if (state := self._hass.states.get(entity_id)) is not None:
                    await self._async_publish_cumulative(
                        state, device_id, concept, attribute
                    )
            for entity_id, targets in derived_by_entity.items():
                if (state := self._hass.states.get(entity_id)) is None:
                    continue
                for device_id, power, energy, rated, attribute in targets:
                    await self._async_publish_derived(
                        state,
                        device_id,
                        power,
                        energy,
                        rated,
                        attribute,
                    )

    @staticmethod
    def _source_unavailable(state: State, attribute: str | None) -> bool:
        """Return whether a mapped state/attribute cannot currently be read."""
        if isinstance(state.state, str) and state.state.strip().lower() in INVALID_STATES:
            return True
        if attribute is None:
            value = state.state
        else:
            if attribute not in state.attributes:
                return True
            value = state.attributes[attribute]
        if value is None:
            return True
        return isinstance(value, str) and value.strip().lower() in INVALID_STATES

    def _cancel_availability_task(self, key: str) -> None:
        task = self._availability_tasks.pop(key, None)
        self._availability_task_sources.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    async def _async_schedule_unavailable(
        self,
        key: str,
        device_id: str,
        concept: str,
        source: str,
    ) -> None:
        """Start one bounded cooldown for a mapped source."""
        persisted = self._availability.get(key)
        if persisted is not None:
            if persisted.get("source") == source:
                return
            self._availability.pop(key, None)
            await self._store.async_save(self._store_payload())

        existing = self._availability_tasks.get(key)
        if existing is not None and not existing.done():
            if self._availability_task_sources.get(key) == source:
                return
            self._cancel_availability_task(key)

        async def _cooldown() -> None:
            try:
                await asyncio.sleep(self._availability_cooldown)
                if self._stopped:
                    return
                sent = await self._send(
                    {concept: {"deviceId": device_id, "status": "unavailable"}}
                )
                if not sent:
                    return
                async with self._state_lock:
                    self._availability[key] = {"source": source}
                    await self._store.async_save(self._store_payload())
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001 - availability must not break publishing
                _LOGGER.debug("Unable to publish fluks unavailable event", exc_info=True)
            finally:
                task = asyncio.current_task()
                if self._availability_tasks.get(key) is task:
                    self._availability_tasks.pop(key, None)
                    self._availability_task_sources.pop(key, None)

        task = asyncio.create_task(_cooldown(), name="fluks observation availability")
        self._availability_tasks[key] = task
        self._availability_task_sources[key] = source

    async def _async_complete_availability(
        self,
        key: str,
        device_id: str,
        concept: str,
        source: str,
        normal_published: bool,
    ) -> None:
        """Cancel cooldowns and recover a previously announced source."""
        self._cancel_availability_task(key)
        persisted = self._availability.get(key)
        if persisted is None or persisted.get("source") != source:
            return
        self._availability.pop(key, None)
        await self._store.async_save(self._store_payload())
        if normal_published:
            return
        try:
            sent = await self._send(
                {concept: {"deviceId": device_id, "status": "available"}}
            )
            if not sent:
                self._availability[key] = {"source": source}
                await self._store.async_save(self._store_payload())
        except Exception:  # noqa: BLE001 - recovery must not break publishing
            _LOGGER.debug("Unable to publish fluks available event", exc_info=True)
            self._availability[key] = {"source": source}
            await self._store.async_save(self._store_payload())

    async def _async_state_changed(
        self,
        event: Event,
        mappings: dict[
            str,
            list[tuple[str, str, bool, str | None, str | None, bool]],
        ],
        derived: dict[str, list[tuple[str, bool, bool, Decimal, str | None]]],
    ) -> None:
        state: State | None = event.data.get("new_state")
        entity_id = event.data.get("entity_id")
        if self._stopped or not isinstance(entity_id, str):
            return
        if state is None:
            for device_id, concept, _cumulative, attribute, _unit, _declares_unit in (
                mappings.get(entity_id, [])
            ):
                await self._async_schedule_unavailable(
                    self._stream_key(device_id, concept),
                    device_id,
                    concept,
                    self._source_id(entity_id, attribute),
                )
            return
        for (
            device_id,
            concept,
            cumulative,
            attribute,
            temperature_unit,
            mapping_declares_unit,
        ) in mappings.get(entity_id, []):
            key = self._stream_key(device_id, concept)
            source = self._source_id(state.entity_id, attribute)
            if self._source_unavailable(state, attribute):
                await self._async_schedule_unavailable(
                    key, device_id, concept, source
                )
                continue
            if cumulative:
                published = await self._async_publish_cumulative(
                    state, device_id, concept, attribute
                )
                await self._async_complete_availability(
                    key, device_id, concept, source, published
                )
                continue
            try:
                value = _json_value(
                    self._mapped_value(
                        state,
                        attribute,
                        temperature_unit,
                        mapping_declares_unit,
                    )
                )
            except (TypeError, ValueError):
                await self._async_complete_availability(
                    key, device_id, concept, source, False
                )
                continue
            published = bool(await self._send({"deviceId": device_id, concept: value}))
            await self._async_complete_availability(
                key, device_id, concept, source, published
            )
        for device_id, power, energy, rated, attribute in derived.get(
            state.entity_id, []
        ):
            await self._async_publish_derived(
                state, device_id, power, energy, rated, attribute, event.time_fired
            )

    async def _async_publish_derived(
        self,
        state: State,
        device_id: str,
        publish_power: bool,
        publish_energy: bool,
        rated_power: Decimal,
        attribute: str | None,
        observed_at: datetime | None = None,
    ) -> None:
        operating_state = self._operating_state(state, attribute)
        now = observed_at or dt_util.utcnow()
        key = self._stream_key(device_id, "spaceHeater.energy")
        power_value: Decimal | None = None
        energy_value: Decimal | None = None
        power_changed = False
        energy_changed = False
        async with self._state_lock:
            stream = self._derived.get(key)
            lifetime = Decimal("0")
            previous_lifetime: Decimal | None = None
            previous_state: str | None = None
            previous_at: datetime | None = None
            previous_power = rated_power
            source = self._source_id(state.entity_id, attribute)
            rebaseline = key in self._derived_needs_rebaseline
            if stream is not None and stream.get("source") == source:
                try:
                    lifetime = Decimal(stream["lifetime"])
                    previous_lifetime = lifetime
                    previous_state = stream.get("state")
                    if not rebaseline:
                        previous_power = Decimal(
                            stream.get("power") or str(rated_power)
                        )
                        previous_at = (
                            datetime.fromisoformat(stream["at"])
                            if stream.get("at")
                            else None
                        )
                except (KeyError, InvalidOperation, TypeError, ValueError):
                    lifetime = Decimal("0")
                    previous_lifetime = None
                    previous_state = None
                    previous_at = None
            self._derived_needs_rebaseline.discard(key)
            if (
                previous_state == "heating"
                and operating_state is not None
                and previous_at is not None
            ):
                elapsed = Decimal(str((now - previous_at).total_seconds()))
                if elapsed > 0:
                    lifetime += previous_power * elapsed / Decimal("3600000")
            if operating_state is not None:
                power_value = (
                    rated_power if operating_state == "heating" else Decimal("0")
                )
                energy_value = lifetime
                power_changed = publish_power and (
                    previous_state is not None and operating_state != previous_state
                )
                energy_changed = publish_energy and (
                    previous_lifetime is not None and energy_value > previous_lifetime
                )
            record = {
                "lifetime": str(lifetime),
                "source": source,
                "state": operating_state,
                "at": now.isoformat(),
                "power": str(rated_power),
            }
            self._derived[key] = record
            await self._store.async_save(self._store_payload())
        if operating_state is None:
            return
        payload: dict[str, Any] = {"deviceId": device_id}
        if power_changed and power_value is not None:
            payload["spaceHeater.power"] = self._json_decimal(power_value)
        if energy_changed and energy_value is not None:
            payload["spaceHeater.energy"] = self._json_decimal(energy_value)
        if len(payload) > 1:
            await self._send(payload)

    async def _async_publish_cumulative(
        self,
        state: State,
        device_id: str,
        concept: str,
        attribute: str | None = None,
    ) -> bool:
        try:
            raw = self._decimal_value(state, attribute)
        except ValueError:
            return False
        key = self._stream_key(device_id, concept)
        marker = self._reset_marker(state)
        async with self._state_lock:
            stream = self._lifetime.get(key)
            changed = True
            if stream is None:
                lifetime = raw
            elif stream["source"] != self._source_id(state.entity_id, attribute):
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
                    "source": self._source_id(state.entity_id, attribute),
                    "last": str(raw),
                    "last_reset": marker,
                }
                await self._store.async_save(self._store_payload())
        return bool(await self._send(
            {"deviceId": device_id, concept: self._json_decimal(lifetime)}
        ))

    @staticmethod
    def _source_id(entity_id: str, attribute: str | None) -> str:
        return entity_id if attribute is None else f"{entity_id}#{attribute}"

    async def async_stop(self) -> None:
        """Remove all listeners and prevent later refreshes."""
        self._stopped = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        tasks = list(self._availability_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._availability_tasks.clear()
        self._availability_task_sources.clear()


async def async_refresh_observations(hass: HomeAssistant, entry_id: str) -> None:
    """Refresh an active entry publisher after Mapping persistence changes."""
    publisher = hass.data.get(DOMAIN, {}).get(DATA_OBSERVATIONS, {}).get(entry_id)
    if publisher is not None:
        await publisher.async_refresh()
