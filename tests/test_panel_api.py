"""Tests for the finite authenticated fluks panel command boundary."""

from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluks.api import FluksApiClient, FluksNotFound
from custom_components.fluks.const import (
    CONF_DEVICE_CONTEXTS,
    CONF_INTEGRATION_KEY,
    DOMAIN,
)
from custom_components.fluks.panel_api import (
    COMMAND_ADD_SAVE,
    COMMANDS,
    COMMAND_CONTEXT,
    COMMAND_DEVICE_DETAIL,
    COMMAND_DEVICE_DELETE,
    COMMAND_DEVICE_SAVE,
    COMMAND_SITE_DELETE,
    _has_local_context,
    _mappable_concepts,
    async_register_panel_commands,
    websocket_context,
    websocket_add_save,
    websocket_device_detail,
    websocket_device_delete,
    websocket_device_save,
    websocket_site_delete,
)
from custom_components.fluks.device import stable_device_id

KEY = "fluks_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ"


def make_entry(hass, *, title="Home", suffix="a"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=f"site-{suffix}",
        data={
            "site_id": f"site-{suffix}",
            "integration_id": f"external-{suffix}",
            "integration_internal_id": f"integration-{suffix}",
            CONF_INTEGRATION_KEY: f"{KEY}-{suffix}",
        },
        options={
            CONF_DEVICE_CONTEXTS: {
                f"device-{suffix}": {
                    "ha_device_id": f"ha-{suffix}",
                    "type": "battery",
                }
            }
        },
    )
    entry.add_to_hass(hass)
    return entry


def connection():
    result = MagicMock()
    result.user.is_admin = True
    return result


def test_registers_only_finite_product_commands(hass):
    """The panel cannot proxy an arbitrary backend operation."""
    with patch(
        "custom_components.fluks.panel_api.websocket_api.async_register_command"
    ) as register:
        async_register_panel_commands(hass)
    assert register.call_count == len(COMMANDS) == 7


def test_catalog_source_semantics_and_local_duplicate_context_are_reused(hass):
    """Panel Add Device keeps the approved M2 catalog and uniqueness rules."""
    entry = make_entry(hass)
    concepts = _mappable_concepts(
        {
            "concepts": [
                {"concept": "battery.soc", "usages": ["fact"], "source": "mapping"},
                {"concept": "future.derived", "usages": ["fact"], "source": "derived"},
            ]
        }
    )
    assert [item["concept"] for item in concepts] == ["battery.soc"]
    assert _has_local_context(entry, "ha-a", "battery")
    assert not _has_local_context(entry, "ha-a", "solar")


async def test_context_is_entry_scoped_and_never_returns_credentials(hass):
    """Two panel routes receive their own safe ConfigEntry context."""
    first = make_entry(hass, title="Home", suffix="a")
    second = make_entry(hass, title="Cabin", suffix="b")
    api = MagicMock(spec=FluksApiClient)
    api.list_devices = AsyncMock(return_value=[])
    catalog = {
        "battery": {"type": "battery", "concepts": []},
    }
    sent = []

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch(
            "custom_components.fluks.panel_api._panel_translations",
            AsyncMock(
                return_value={
                    "delete_site_title": "Delete {siteName}?",
                    "device_type_battery": "Battery",
                    "device_type_fallback": "Energy device",
                }
            ),
        ),
    ):
        for entry in (first, second):
            conn = connection()
            conn.send_result.side_effect = lambda _id, value: sent.append(value)
            websocket_context(
                hass, conn, {"id": 1, "type": COMMAND_CONTEXT, "entry_id": entry.entry_id}
            )
            await hass.async_block_till_done()

    assert [item["site"]["name"] for item in sent] == ["Home", "Cabin"]
    assert all(item["translations"]["delete_site_title"] == "Delete {siteName}?" for item in sent)
    serialized = repr(sent)
    assert "integration_key" not in serialized
    assert KEY not in serialized


async def test_wrong_valid_user_delete_is_error_and_keeps_local_context(hass):
    """Successful login cannot turn a rejected Device DELETE into success."""
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.login = AsyncMock(return_value=("temporary-human-jwt", 3600))
    api.delete_device = AsyncMock(side_effect=FluksNotFound("NOT_FOUND"))
    conn = connection()

    with patch("custom_components.fluks.panel_api._api", return_value=api):
        websocket_device_delete(
            hass,
            conn,
            {
                "id": 2,
                "type": COMMAND_DEVICE_DELETE,
                "entry_id": entry.entry_id,
                "device_id": "device-a",
                "email": "other@example.com",
                "password": "correct-password",
            },
        )
        await hass.async_block_till_done()

    conn.send_result.assert_not_called()
    conn.send_error.assert_called_once()
    assert "device-a" in entry.options[CONF_DEVICE_CONTEXTS]
    assert entry.data[CONF_INTEGRATION_KEY].endswith("-a")
    api.delete_device.assert_awaited_once()
    api.set_human_access_token.assert_called_with(None)


async def test_device_delete_mutates_local_context_only_after_backend_success(hass):
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.login = AsyncMock(return_value=("temporary-human-jwt", 3600))
    api.delete_device = AsyncMock()
    conn = connection()

    with patch("custom_components.fluks.panel_api._api", return_value=api):
        websocket_device_delete(
            hass,
            conn,
            {
                "id": 3,
                "type": COMMAND_DEVICE_DELETE,
                "entry_id": entry.entry_id,
                "device_id": "device-a",
                "email": "owner@example.com",
                "password": "correct-password",
            },
        )
        await hass.async_block_till_done()

    conn.send_result.assert_called_once_with(3, {})
    assert "device-a" not in entry.options[CONF_DEVICE_CONTEXTS]
    assert entry.data[CONF_INTEGRATION_KEY].endswith("-a")


async def test_device_noop_save_performs_no_mutations(hass):
    """The panel command preserves M3 incremental no-op semantics."""
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(
        return_value={"id": "device-a", "type": "battery", "properties": {}}
    )
    api.list_mappings = AsyncMock(return_value=[])
    api.update_device_properties = AsyncMock()
    api.create_mapping = AsyncMock()
    api.update_mapping = AsyncMock()
    api.delete_mapping = AsyncMock()
    conn = connection()
    catalog = {
        "battery": {
            "type": "battery",
            "concepts": [
                {
                    "concept": "battery.soc",
                    "usages": ["fact"],
                    "source": "mapping",
                    "cadence": "realtime",
                }
            ],
        }
    }

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
    ):
        websocket_device_save(
            hass,
            conn,
            {
                "id": 5,
                "type": "fluks/config/device_save",
                "entry_id": entry.entry_id,
                "device_id": "device-a",
                "mappings": {},
                "properties": {},
            },
        )
        await hass.async_block_till_done()

    conn.send_result.assert_called_once_with(5, {})
    api.update_device_properties.assert_not_awaited()
    api.create_mapping.assert_not_awaited()
    api.update_mapping.assert_not_awaited()
    api.delete_mapping.assert_not_awaited()


async def test_add_save_uses_deterministic_identity_and_only_confirmed_mappings(hass):
    """Production Add owns stable identity, optionality, and local context persistence."""
    entry = make_entry(hass)
    hass.states.async_set("sensor.new_soc", "55", {"unit_of_measurement": "%"})
    api = MagicMock(spec=FluksApiClient)
    api.list_devices = AsyncMock(return_value=[])
    api.create_device = AsyncMock(
        return_value={"id": "device-new", "type": "battery"}
    )
    api.list_mappings = AsyncMock(return_value=[])
    api.create_mapping = AsyncMock(return_value={"id": "mapping-new"})
    catalog = {
        "battery": {
            "type": "battery",
            "concepts": [
                {
                    "concept": "battery.soc",
                    "datatype": "number",
                    "unit": "%",
                    "cadence": "realtime",
                    "usages": ["fact"],
                    "source": "mapping",
                },
                {
                    "concept": "battery.futureDerived",
                    "usages": ["fact"],
                    "source": "derived",
                },
            ],
        }
    }
    conn = connection()

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
    ):
        websocket_add_save(
            hass,
            conn,
            {
                "id": 6,
                "type": COMMAND_ADD_SAVE,
                "entry_id": entry.entry_id,
                "device_type": "battery",
                "ha_device_id": "ha-new",
                "mappings": {"battery.soc": "sensor.new_soc"},
                "properties": {},
            },
        )
        await hass.async_block_till_done()

    expected_id = stable_device_id("external-a", "battery", "ha-new")
    assert api.create_device.await_args.args[:3] == ("site-a", expected_id, "battery")
    payload = api.create_mapping.await_args.args[1]
    assert payload["concept"] == "battery.soc"
    assert payload["configuration"]["entityId"] == "sensor.new_soc"
    assert "futureDerived" not in repr(payload)
    assert entry.options[CONF_DEVICE_CONTEXTS]["device-new"] == {
        "ha_device_id": "ha-new",
        "type": "battery",
    }
    conn.send_result.assert_called_once_with(6, {"device_id": "device-new"})


async def test_device_save_reconciles_property_and_mapping_diffs_incrementally(hass):
    """Production Edit performs PATCH, POST, and DELETE without recreating Device."""
    entry = make_entry(hass)
    for entity in ("sensor.new_soc", "sensor.new_energy"):
        hass.states.async_set(entity, "10", {"unit_of_measurement": "kWh"})
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(
        return_value={
            "id": "device-a",
            "type": "battery",
            "properties": {"vendor": "GoodWe", "model": "Old"},
        }
    )
    api.list_mappings = AsyncMock(
        return_value=[
            {"id": "soc-map", "concept": "battery.soc", "direction": "input", "configuration": {"entityId": "sensor.old_soc"}},
            {"id": "power-map", "concept": "battery.power", "direction": "input", "configuration": {"entityId": "sensor.old_power"}},
        ]
    )
    api.update_device_properties = AsyncMock()
    api.update_mapping = AsyncMock()
    api.delete_mapping = AsyncMock()
    api.create_mapping = AsyncMock()
    catalog = {
        "battery": {
            "type": "battery",
            "concepts": [
                {"concept": "battery.soc", "datatype": "number", "cadence": "realtime", "usages": ["fact"], "source": "mapping"},
                {"concept": "battery.power", "datatype": "number", "cadence": "realtime", "usages": ["fact"], "source": "mapping"},
                {"concept": "battery.energy", "datatype": "number", "cadence": "interval", "usages": ["fact"], "source": "mapping"},
            ],
        }
    }
    conn = connection()

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
    ):
        websocket_device_save(
            hass,
            conn,
            {
                "id": 7,
                "type": COMMAND_DEVICE_SAVE,
                "entry_id": entry.entry_id,
                "device_id": "device-a",
                "mappings": {
                    "battery.soc": "sensor.new_soc",
                    "battery.energy": "sensor.new_energy",
                },
                "properties": {"vendor": "GoodWe", "model": "New"},
            },
        )
        await hass.async_block_till_done()

    api.update_device_properties.assert_awaited_once_with(
        "site-a", "device-a", {"model": "New"}
    )
    assert api.update_mapping.await_args.args[:2] == ("site-a", "soc-map")
    api.delete_mapping.assert_awaited_once_with("site-a", "power-map")
    assert api.create_mapping.await_args.args[1]["concept"] == "battery.energy"
    api.create_device.assert_not_called()


async def test_device_detail_preserves_existing_mapping_and_matches_only_missing(hass):
    """Existing backend configuration remains authoritative over matcher assistance."""
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(
        return_value={"id": "device-a", "type": "battery", "properties": {}}
    )
    api.list_mappings = AsyncMock(
        return_value=[
            {"id": "soc-map", "concept": "battery.soc", "direction": "input", "configuration": {"entityId": "sensor.existing_soc"}}
        ]
    )
    catalog = {
        "battery": {
            "type": "battery",
            "concepts": [
                {"concept": "battery.soc", "cadence": "realtime", "usages": ["fact"], "source": "mapping"},
                {"concept": "battery.power", "cadence": "realtime", "usages": ["fact"], "source": "mapping"},
            ],
        }
    }
    conn = connection()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api._panel_translations", AsyncMock(return_value={"device_type_battery": "Battery"})),
        patch("custom_components.fluks.panel_api.suggest_entities", return_value={"battery.power": "sensor.suggested_power"}) as matcher,
    ):
        websocket_device_detail(
            hass,
            conn,
            {"id": 8, "type": COMMAND_DEVICE_DETAIL, "entry_id": entry.entry_id, "device_id": "device-a"},
        )
        await hass.async_block_till_done()

    result = conn.send_result.call_args.args[1]
    assert result["mappings"]["battery.soc"]["configuration"]["entityId"] == "sensor.existing_soc"
    assert result["suggestions"] == {"battery.power": "sensor.suggested_power"}
    assert [item["concept"] for item in matcher.call_args.args[1]] == ["battery.power"]


async def test_site_delete_removes_entry_only_after_backend_success(hass):
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.login = AsyncMock(return_value=("temporary-human-jwt", 3600))
    api.delete_site = AsyncMock()
    conn = connection()

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch.object(hass.config_entries, "async_remove", AsyncMock(return_value=True)) as remove,
    ):
        websocket_site_delete(
            hass,
            conn,
            {
                "id": 4,
                "type": COMMAND_SITE_DELETE,
                "entry_id": entry.entry_id,
                "email": "owner@example.com",
                "password": "correct-password",
            },
        )
        await hass.async_block_till_done()

    api.delete_site.assert_awaited_once_with("site-a")
    remove.assert_awaited_once_with(entry.entry_id)
    conn.send_result.assert_called_once_with(4, {"entry_removed": True})
