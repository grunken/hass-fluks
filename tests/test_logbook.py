"""Native integration filtering and icon rendering for Decision results."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.logbook.helpers import (
    async_determine_event_types,
    event_forwarder_filtered,
)
from homeassistant.components.logbook.models import async_event_to_row
from homeassistant.components.logbook.processor import EventProcessor
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluks.const import CONF_INTEGRATION_KEY, DOMAIN, EVENT_DECISION_LOG
from custom_components.fluks.output_execution import RuntimeOutputExecutor


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url):
    """Prepare the native Recorder fixture before Home Assistant is created."""


@pytest.fixture
async def log_source(hass, recorder_mock):
    entry = MockConfigEntry(domain=DOMAIN, data={
        "site_id": "site", CONF_INTEGRATION_KEY: "fluks_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ",
    })
    entry.add_to_hass(hass)
    runtime = MagicMock()
    runtime.async_stop = AsyncMock()
    observations = MagicMock()
    observations.async_refresh = AsyncMock()
    observations.async_stop = AsyncMock()
    with (
        patch("custom_components.fluks.FluksRuntimeWebSocket", return_value=runtime),
        patch("custom_components.fluks.RealtimeObservationPublisher", return_value=observations),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("target", [None, "number.inverter_power"])
async def test_decision_log_has_native_integration_source_and_icon(hass, log_source, target):
    registry = er.async_get(hass)
    source = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{log_source.entry_id}_decision_log")
    assert registry.async_get(source).config_entry_id == log_source.entry_id
    assert hass.states.get(source).state == "on"
    event_types = async_determine_event_types(hass, [source], None)
    assert EVENT_DECISION_LOG in event_types

    events = []
    hass.bus.async_listen(EVENT_DECISION_LOG, events.append)
    executor = RuntimeOutputExecutor(hass, MagicMock(), "site", log_source.entry_id)
    await executor._async_log("decision_balance_suppressed", entity_id=target, concept="battery.power")
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["entity_id"] == source
    assert events[0].data["target_entity_id"] == target

    forwarded = []
    event_forwarder_filtered(forwarded.append, None, [source], None)(events[0])
    assert forwarded == events
    rendered = EventProcessor(hass, event_types, entity_ids=[source]).humanify([
        async_event_to_row(events[0]),
    ])
    assert len(rendered) == 1
    assert rendered[0]["domain"] == DOMAIN
    assert rendered[0]["icon"] == "mdi:lightning-bolt"
    assert rendered[0]["message"] == events[0].data["message"]
    assert rendered[0]["entity_id"] == (target or source)


async def test_log_source_rename_and_unload(hass, log_source):
    registry = er.async_get(hass)
    source = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{log_source.entry_id}_decision_log")
    renamed = "binary_sensor.renamed_fluks_log"
    registry.async_update_entity(source, new_entity_id=renamed)
    await hass.async_block_till_done()
    events = []
    hass.bus.async_listen(EVENT_DECISION_LOG, events.append)
    await RuntimeOutputExecutor(hass, MagicMock(), "site", log_source.entry_id)._async_log(
        "decision_no_controls", device="test",
    )
    await hass.async_block_till_done()
    assert events[0].data["entity_id"] == renamed
