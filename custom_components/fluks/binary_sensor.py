"""A native entity source for integration-filtered Decision Logbook entries."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Associate the Logbook source with this fluks config entry."""
    async_add_entities([FluksDecisionLogSource(entry)])


class FluksDecisionLogSource(BinarySensorEntity):
    """Identify the loaded integration as the source of its Decision log."""

    _attr_name = "fluks"
    _attr_icon = "mdi:lightning-bolt"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_is_on = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_decision_log"
