"""The fluks Home Assistant integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FluksApiClient
from .const import (
    API_BASE_URL,
    CONF_INTEGRATION_KEY,
    CONF_SITE_ID,
    DATA_OBSERVATIONS,
    DATA_RUNTIME,
    DOMAIN,
)
from .control_editor_panel import async_register_control_editor_panel
from .observations import RealtimeObservationPublisher
from .panel_api import async_register_panel_commands
from .runtime import FluksRuntimeWebSocket


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the embedded administration panel and its local command API."""
    await async_register_control_editor_panel(hass)
    async_register_panel_commands(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a fluks config entry and start its background runtime transport."""
    if not (integration_key := entry.data.get(CONF_INTEGRATION_KEY)):
        raise ConfigEntryAuthFailed("The fluks Integration credential is missing")
    runtime = FluksRuntimeWebSocket(
        async_get_clientsession(hass), integration_key, API_BASE_URL
    )
    runtimes = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_RUNTIME, {})
    if (previous := runtimes.pop(entry.entry_id, None)) is not None:
        await previous.async_stop()
    runtimes[entry.entry_id] = runtime
    runtime.start(
        lambda coroutine: entry.async_create_background_task(
            hass, coroutine, "fluks runtime"
        )
    )
    observations = RealtimeObservationPublisher(
        hass,
        FluksApiClient(
            async_get_clientsession(hass), integration_key=integration_key
        ),
        entry.data[CONF_SITE_ID],
        entry.entry_id,
        runtime.async_send,
    )
    publishers = hass.data[DOMAIN].setdefault(DATA_OBSERVATIONS, {})
    if (previous := publishers.pop(entry.entry_id, None)) is not None:
        await previous.async_stop()
    publishers[entry.entry_id] = observations
    entry.async_create_background_task(
        hass, observations.async_refresh(), "fluks observations"
    )

    async def _stop_runtime(_event: Event) -> None:
        await observations.async_stop()
        await runtime.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_runtime)
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a fluks config entry and stop its runtime transport."""
    runtimes = hass.data.get(DOMAIN, {}).get(DATA_RUNTIME, {})
    publishers = hass.data.get(DOMAIN, {}).get(DATA_OBSERVATIONS, {})
    if (publisher := publishers.pop(entry.entry_id, None)) is not None:
        await publisher.async_stop()
    if (runtime := runtimes.pop(entry.entry_id, None)) is not None:
        await runtime.async_stop()
    return True
