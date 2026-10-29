"""Describe Decision results using Home Assistant's native Logbook platform."""

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_ICON,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
    LazyEventPartialState,
)
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, EVENT_DECISION_LOG


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[
        [str, str, Callable[[LazyEventPartialState], dict[str, Any]]], None
    ],
) -> None:
    """Register the integration association and supported event icon."""

    @callback
    def describe(event: LazyEventPartialState) -> dict[str, Any]:
        return {
            LOGBOOK_ENTRY_NAME: event.data["name"],
            LOGBOOK_ENTRY_MESSAGE: event.data["message"],
            LOGBOOK_ENTRY_ICON: "mdi:lightning-bolt",
            LOGBOOK_ENTRY_ENTITY_ID: event.data.get("target_entity_id")
            or event.data.get("entity_id"),
        }

    async_describe_event(DOMAIN, EVENT_DECISION_LOG, describe)
