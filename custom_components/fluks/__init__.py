"""The fluks Home Assistant integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import CONF_INTEGRATION_KEY


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a fluks config entry without runtime communication."""
    if not entry.data.get(CONF_INTEGRATION_KEY):
        raise ConfigEntryAuthFailed("The fluks Integration credential is missing")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a fluks config entry."""
    return True
