"""Persist Core's latest plan and schedule it only while Core is disconnected."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def plan_time(value: Any) -> datetime:
    """Require an unambiguous wire timestamp; scheduling always uses UTC."""
    if not isinstance(value, str):
        raise ValueError("Plan timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Plan timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def plan_decisions(plan: dict) -> list[tuple[datetime, str, dict]]:
    """Validate the complete plan before replacing the last good copy."""
    generated, expires = plan_time(plan.get("generatedAt")), plan_time(plan.get("expiresAt"))
    if generated > expires or not isinstance(plan.get("decisions"), list):
        raise ValueError("Invalid plan interval or Decisions")
    result = []
    for slot in plan["decisions"]:
        if not isinstance(slot, dict):
            raise ValueError("Invalid plan Decision")
        at = plan_time(slot.get("at"))
        if at > expires:
            raise ValueError("Decision is after plan expiry")
        for concept, decision in slot.items():
            if concept == "at":
                continue
            if (
                not isinstance(concept, str) or "." not in concept
                or not all(concept.split(".", 1))
                or not isinstance(decision, dict)
                or not isinstance(decision.get("deviceId"), str) or not decision["deviceId"]
                or "value" not in decision
                or decision.get("mode") is not None and not isinstance(decision["mode"], str)
            ):
                raise ValueError("Invalid concept-keyed plan Decision")
            result.append((at, concept, decision))
    return sorted(result, key=lambda item: item[0])


class LocalPlan:
    """One atomic entry-scoped plan, execution journal and offline Mapping cache."""

    def __init__(self, hass: HomeAssistant, entry_id: str, site_id: str, executor, *, store=None):
        self.hass, self.site_id, self.executor = hass, site_id, executor
        self.store = store or Store(hass, 1, f"{DOMAIN}.plan.{entry_id}", atomic_writes=True)
        self.data: dict[str, Any] = {"plan": None, "claimed": [], "active": {}, "cache": {}, "panel": {}}
        self._lock = asyncio.Lock()
        self._connected = False
        self._stopped = False
        self._revision = 0
        self._offline_since = dt_util.utcnow()
        self._cancel_timer = None
        self._task = None
        self._unsubscribe_started = None
        self.notify = lambda: None

    @property
    def plan(self):
        return self.data["plan"]

    @property
    def state(self) -> str:
        if self.plan is None:
            return "none"
        if dt_util.utcnow() >= plan_time(self.plan["expiresAt"]):
            return "expired"
        if not all(key in self.data["cache"] for key in ("devices", "mappings", "catalog")):
            return "unavailable"
        return "ready" if self._connected else "executing"

    async def async_load(self):
        try:
            stored = await self.store.async_load()
        except OSError:
            _LOGGER.warning("Unable to restore the local fluks plan", exc_info=True)
            stored = None
        if isinstance(stored, dict):
            try:
                if stored.get("plan") is not None:
                    plan_decisions(stored["plan"])
                for key in self.data:
                    if key in stored:
                        self.data[key] = stored[key]
            except (TypeError, ValueError):
                _LOGGER.warning("Ignoring invalid locally stored fluks plan")
        if not self.hass.is_running:
            @callback
            def started(_event):
                self._unsubscribe_started = None
                self._schedule()
            self._unsubscribe_started = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, started)
        self._schedule()

    async def _save(self, data):
        await self.store.async_save(data)
        self.data = data

    async def async_receive(self, plan):
        if not isinstance(plan, dict):
            raise ValueError("Plan must be an object")
        plan_decisions(plan)
        async with self._lock:
            if self.plan and plan_time(plan["generatedAt"]) < plan_time(self.plan["generatedAt"]):
                return  # An older reconnect snapshot must not replace a newer plan.
            data = deepcopy(self.data)
            data["plan"] = deepcopy(plan)
            # Keep claims for overlapping plans; a retransmission cannot replay a slot.
            relevant = {self._key(at, concept, d) for at, concept, d in plan_decisions(plan)}
            data["claimed"] = [key for key in data["claimed"] if key in relevant]
            await self._save(data)
            self._revision += 1
        self._schedule()
        self.notify()

    @callback
    def set_connected(self, connected: bool):
        if connected == self._connected:
            return
        self._revision += 1
        if not connected:
            self._offline_since = dt_util.utcnow()
        self._connected = connected
        self._schedule()

    async def async_core_authority(self):
        """Hand ownership to Core without sending local release actions."""
        async with self._lock:
            if self.data["active"]:
                data = deepcopy(self.data)
                data["active"] = {}
                try:
                    await self._save(data)
                except OSError:
                    # Local storage cannot veto Core taking over execution.
                    self.data = data
                    _LOGGER.warning("Unable to persist the fluks Core handoff", exc_info=True)

    async def async_cache_response(self, method, path, body, params):
        """Cache only responses from existing Core requests; never fetch here."""
        data = body.get("data")
        prefix = f"/sites/{self.site_id}/"
        resource = "catalog" if path == "/canonical/device-types" else path.removeprefix(prefix)
        if resource not in {"devices", "mappings", "catalog"} and not resource.startswith("mappings/"):
            return
        previous_state = self.state
        async with self._lock:
            updated = deepcopy(self.data)
            cache = updated["cache"]
            if method == "GET" and isinstance(data, list):
                if resource == "mappings" and params and params.get("deviceId"):
                    cache[resource] = [m for m in cache.get(resource, []) if m.get("deviceId") != params["deviceId"]] + data
                else:
                    cache[resource] = data
            elif resource.startswith("mappings/") or resource == "mappings":
                mapping_id = resource.split("/", 1)[1] if "/" in resource else (data or {}).get("id")
                if method not in {"POST", "PATCH", "DELETE"} or method != "DELETE" and not isinstance(data, dict):
                    return
                cache["mappings"] = [m for m in cache.get("mappings", []) if m.get("id") != mapping_id]
                if method != "DELETE":
                    cache["mappings"].append(data)
            else:
                return
            await self._save(updated)
        if self.state != previous_state:
            self.notify()

    async def async_cache_panel(self, panel):
        async with self._lock:
            data = deepcopy(self.data)
            data["panel"] = deepcopy({key: panel[key] for key in ("site", "devices", "device_types", "plan_devices")})
            await self._save(data)

    @staticmethod
    def _key(at, concept, decision):
        return f"{at.isoformat()}|{decision['deviceId']}|{concept}"

    def _schedule(self):
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        if self._stopped or self._connected or not self.plan or not self.hass.is_running:
            return
        now = dt_util.utcnow()
        expires = plan_time(self.plan["expiresAt"])
        future = [at for at, c, d in plan_decisions(self.plan)
                  if at >= max(now, self._offline_since) and self._key(at, c, d) not in self.data["claimed"]]
        if expires > now:
            future.append(expires)
        elif self.data["active"] or any(
            at == expires and decision.get("mode") == "release"
            and self._key(at, concept, decision) not in self.data["claimed"]
            for at, concept, decision in plan_decisions(self.plan)
        ):
            future.append(now)
        if future:
            self._scheduled_at = min(future)
            self._cancel_timer = async_track_point_in_utc_time(self.hass, self._due, self._scheduled_at)

    async def _due(self, now):
        if self._task and not self._task.done():
            return
        self._task = asyncio.current_task()
        revision = self._revision
        scheduled_at = self._scheduled_at
        expiry_batch = False
        guard = lambda: (
            not self._stopped and not self._connected and revision == self._revision
            and (expiry_batch or dt_util.utcnow() < plan_time(self.plan["expiresAt"]))
        )
        try:
            async with self._lock:
                if self._stopped or self._connected or revision != self._revision or not self.plan:
                    return
                expires = plan_time(self.plan["expiresAt"])
                expired = now >= expires
                expiry_batch = expired
                pending = [(at, c, d) for at, c, d in plan_decisions(self.plan)
                           if at == scheduled_at and self._offline_since <= at <= now
                           and self._key(at, c, d) not in self.data["claimed"]]
                if expired:
                    # Releases are cleanup, including when HA/Core was unavailable
                    # at expiry. Never catch up the expired target Decisions.
                    pending = [(at, c, d) for at, c, d in plan_decisions(self.plan)
                               if at == expires and d.get("mode") == "release"
                               and self._key(at, c, d) not in self.data["claimed"]]
                    existing = {(c, d["deviceId"]) for _, c, d in pending}
                    terminal = {(c, d["deviceId"]): d for at, c, d in plan_decisions(self.plan)
                                if at == expires and d.get("mode") == "release"}
                    pending += [(expires, a["concept"], terminal.get((a["concept"], a["deviceId"]),
                                {"deviceId": a["deviceId"], "mode": "release", "value": None}))
                                for a in self.data["active"].values() if (a["concept"], a["deviceId"]) not in existing]
                updated = deepcopy(self.data)
                decisions = []
                for at, concept, decision in pending:
                    key = self._key(at, concept, decision)
                    if key in updated["claimed"]:
                        continue
                    updated["claimed"].append(key)
                    control_key = f"{decision['deviceId']}|{concept}"
                    if decision.get("mode") == "release":
                        updated["active"].pop(control_key, None)
                    else:
                        updated["active"][control_key] = {"concept": concept, "deviceId": decision["deviceId"], "mode": decision.get("mode")}
                    device_type, field = concept.split(".", 1)
                    decisions.append({"deviceId": decision["deviceId"], "deviceType": device_type,
                                      "mode": decision.get("mode"), field: decision["value"]})
                if decisions:
                    # Durable claims precede HA side effects, including release actions.
                    await self._save(updated)
            if decisions and guard():
                await self.executor.async_handle(
                    {"type": "decision.snapshot", "decisions": decisions},
                    context=self.data["cache"], guard=guard,
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.exception("Unable to execute the saved fluks plan")
        finally:
            self._task = None
            self._schedule()
            self.notify()

    async def async_stop(self):
        self._stopped = True
        if self._unsubscribe_started is not None:
            self._unsubscribe_started()
            self._unsubscribe_started = None
        self._schedule()
        if self._task and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
