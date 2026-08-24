"""The fluks Home Assistant integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import CONF_INTEGRATION_KEY
from .control_editor_panel import async_register_control_editor_panel
from .panel_api import async_register_panel_commands


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the embedded administration panel and its local command API."""
    await async_register_control_editor_panel(hass)
    async_register_panel_commands(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a fluks config entry without runtime communication."""
    if not entry.data.get(CONF_INTEGRATION_KEY):
        raise ConfigEntryAuthFailed("The fluks Integration credential is missing")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a fluks config entry."""
    return True
