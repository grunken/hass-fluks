"""Tests for the finite authenticated fluks panel command boundary."""

from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fluks.api import FluksApiClient, FluksNotFound
from custom_components.fluks.const import (
    CONF_CLEARED_MAPPING_CONCEPTS,
    CONF_DEVICE_CONTEXTS,
    CONF_INTEGRATION_KEY,
    DOMAIN,
)
from custom_components.fluks.device import stable_device_id
from custom_components.fluks.panel_api import (
    COMMAND_ADD_REVIEW,
    COMMAND_ADD_SAVE,
    COMMAND_CONTEXT,
    COMMAND_CONTROL_CAPABILITIES,
    COMMAND_CONTROL_SAVE,
    COMMAND_DEVICE_DELETE,
    COMMAND_DEVICE_DETAIL,
    COMMAND_DEVICE_SAVE,
    COMMAND_SITE_DELETE,
    COMMANDS,
    _editable_property_keys,
    _has_local_context,
    _mappable_concepts,
    _migrate_legacy_mappings,
    async_register_panel_commands,
    websocket_add_review,
    websocket_add_save,
    websocket_context,
    websocket_control_capabilities,
    websocket_control_save,
    websocket_device_delete,
    websocket_device_detail,
    websocket_device_save,
    websocket_site_delete,
)

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


def input_proposal(concept, entity_id, *, classification="auto", configuration=None):
    """Build the rich matcher contract used by review responses."""
    configured = configuration or {"version": 1, "entityId": entity_id}
    return {
        "concept": concept,
        "source": {"entityId": entity_id},
        "configuration": configured,
        "score": 20,
        "evidence": [
            {"family": "relationship", "code": "selected_device", "weight": 4},
            {"family": "unit", "code": "exact_unit", "weight": 4},
            {"family": "semantics", "code": "name_role", "weight": 4},
        ],
        "runner_up_gap": 8,
        "classification": classification,
        "alternatives": [],
    }


def test_registers_only_finite_product_commands(hass):
    """The panel cannot proxy an arbitrary backend operation."""
    with patch(
        "custom_components.fluks.panel_api.websocket_api.async_register_command"
    ) as register:
        async_register_panel_commands(hass)
    assert register.call_count == len(COMMANDS) == 9


async def test_control_capabilities_command_is_admin_and_never_returns_credentials(hass):
    entry = make_entry(hass)
    conn = connection()
    capabilities = [{"service": "number.set_value", "entities": []}]
    with patch("custom_components.fluks.panel_api.async_control_capabilities", AsyncMock(return_value=capabilities)):
        websocket_control_capabilities(hass, conn, {"id": 31, "type": COMMAND_CONTROL_CAPABILITIES, "entry_id": entry.entry_id})
        await hass.async_block_till_done()
    conn.send_result.assert_called_once_with(31, {"actions": capabilities})
    assert KEY not in repr(conn.send_result.call_args)


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


async def test_context_contains_required_site_device_separate_from_devices(hass):
    """The one canonical Device list produces both overview sections."""
    first = make_entry(hass, title="Home", suffix="a")
    second = make_entry(hass, title="Cabin", suffix="b")
    api = MagicMock(spec=FluksApiClient)
    api.list_devices = AsyncMock(
        return_value=[{"id": "site-device", "type": "site", "properties": {}}]
    )
    catalog = {
        "battery": {"type": "battery", "concepts": []},
        "spaceHeater": {"type": "spaceHeater", "concepts": []},
        "site": {"type": "site", "concepts": []},
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
                    "device_type_spaceHeater": "Space heater",
                    "device_type_site": "Site",
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
    assert all(item["site"] == {
        "id": "site-device",
        "type": "site",
        "type_name": "Site",
        "name": item["site"]["name"],
        "label": f'Site · {item["site"]["name"]}',
        "metadata": "",
    } for item in sent)
    assert all(item["devices"] == [] for item in sent)
    assert all(item["device_types"] == [
        {"type": "battery", "name": "Battery"},
        {"type": "spaceHeater", "name": "Space heater"},
    ] for item in sent)
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

    conn.send_result.assert_called_once_with(5, {"properties": {}})
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
                "mappings": {
                    "battery.soc": {
                        "version": 1,
                        "entityId": "sensor.new_soc",
                        "transforms": [
                            {"type": "invert"},
                            {"type": "scale", "factor": 0.5},
                        ],
                    }
                },
                "properties": {
                    "capacityKwh": 15.8,
                    "battery.socMinimum": 15,
                    "battery.socMaximum": 97,
                },
            },
        )
        await hass.async_block_till_done()

    expected_id = stable_device_id("external-a", "battery", "ha-new")
    assert api.create_device.await_args.args[:3] == ("site-a", expected_id, "battery")
    assert api.create_device.await_args.args[3] == {
        "capacityKwh": 15.8,
        "battery.socMinimum": 15,
        "battery.socMaximum": 97,
    }
    payload = api.create_mapping.await_args.args[1]
    assert payload["concept"] == "battery.soc"
    assert payload["configuration"]["entityId"] == "sensor.new_soc"
    assert payload["configuration"]["unit"] == "%"
    assert payload["configuration"]["transforms"] == [
        {"type": "invert"},
        {"type": "scale", "factor": 0.5},
    ]
    assert "futureDerived" not in repr(payload)
    assert entry.options[CONF_DEVICE_CONTEXTS]["device-new"] == {
        "ha_device_id": "ha-new",
        "type": "battery",
    }
    conn.send_result.assert_called_once_with(6, {"device_id": "device-new"})


async def test_add_review_proposal_persists_unchanged_through_add_save(hass):
    """The rich review configuration is the configuration persisted by Add Save."""
    entry = make_entry(hass)
    hass.states.async_set(
        "sensor.new_battery_power",
        "1.25",
        {"unit_of_measurement": "kW", "device_class": "power"},
    )
    api = MagicMock(spec=FluksApiClient)
    api.list_devices = AsyncMock(return_value=[])
    api.create_device = AsyncMock(return_value={"id": "device-new", "type": "battery"})
    api.list_mappings = AsyncMock(return_value=[])
    api.create_mapping = AsyncMock(return_value={"id": "mapping-new"})
    concept = {
        "concept": "battery.power", "datatype": "number", "unit": "W",
        "cadence": "realtime", "usages": ["fact"], "source": "mapping",
    }
    catalog = {"battery": {"type": "battery", "concepts": [concept]}}
    configuration = {
        "version": 1,
        "entityId": "sensor.new_battery_power",
        "unit": "kW",
        "transforms": [{"type": "scale", "factor": 1000}],
    }
    proposal = input_proposal(
        "battery.power", "sensor.new_battery_power", configuration=configuration
    )
    registry_device = MagicMock(
        name_by_user="Battery", name="Battery", manufacturer="Example", model="One"
    )
    registry = MagicMock()
    registry.async_get.return_value = registry_device
    review_connection = connection()
    save_connection = connection()

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api._panel_translations", AsyncMock(return_value={})),
        patch("custom_components.fluks.panel_api.dr.async_get", return_value=registry),
        patch(
            "custom_components.fluks.panel_api.match_entities",
            return_value={"battery.power": proposal},
        ),
    ):
        websocket_add_review(hass, review_connection, {
            "id": 61, "type": COMMAND_ADD_REVIEW, "entry_id": entry.entry_id,
            "device_type": "battery", "ha_device_id": "ha-new",
        })
        await hass.async_block_till_done()
        review = review_connection.send_result.call_args.args[1]

        websocket_add_save(hass, save_connection, {
            "id": 62, "type": COMMAND_ADD_SAVE, "entry_id": entry.entry_id,
            "device_type": "battery", "ha_device_id": "ha-new",
            "mappings": {
                "battery.power": review["proposals"]["battery.power"]["configuration"]
            },
            "properties": {},
        })
        await hass.async_block_till_done()

    assert review["proposals"] == {"battery.power": proposal}
    assert api.create_mapping.await_args.args[1]["configuration"] == configuration
    save_connection.send_result.assert_called_once_with(62, {"device_id": "device-new"})


async def test_device_save_reconciles_property_and_mapping_diffs_incrementally(hass):
    """Production Edit performs PATCH, POST, and DELETE without recreating Device."""
    entry = make_entry(hass)
    hass.states.async_set(
        "sensor.new_soc", "online", {"battery_level": 10}
    )
    hass.states.async_set(
        "sensor.new_energy", "10", {"unit_of_measurement": "kWh"}
    )
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
    api.update_device_properties = AsyncMock(return_value={
        "id": "device-a", "type": "battery",
        "properties": {"vendor": "GoodWe", "model": "New"},
    })
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
                    "battery.soc": {
                        "version": 1,
                        "entityId": "sensor.new_soc",
                        "attribute": "battery_level",
                    },
                    "battery.energy": {"version": 1, "entityId": "sensor.new_energy"},
                },
                "properties": {"vendor": "GoodWe", "model": "New"},
            },
        )
        await hass.async_block_till_done()

    api.update_device_properties.assert_awaited_once_with(
        "site-a", "device-a", {"model": "New"}
    )
    assert api.update_mapping.await_args.args[:2] == ("site-a", "soc-map")
    assert api.update_mapping.await_args.args[2] == {
        "version": 1,
        "entityId": "sensor.new_soc",
        "attribute": "battery_level",
    }
    api.delete_mapping.assert_awaited_once_with("site-a", "power-map")
    assert api.create_mapping.await_args.args[1]["concept"] == "battery.energy"
    api.create_device.assert_not_called()


async def test_battery_physical_properties_patch_without_writing_learned_values(hass):
    """Battery configuration uses Device PATCH while learned properties stay read-only."""
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={
        "id": "device-a", "type": "battery", "properties": {
            "battery.capacityKwhEstimated": 15.8,
            "battery.socMinimumObserved": 15,
            "battery.socMaximumObserved": 97,
        },
    })
    api.list_mappings = AsyncMock(return_value=[])
    updated_properties = {
        "capacityKwh": 20,
        "battery.socMinimum": 10,
        "battery.socMaximum": 90,
        "battery.capacityKwhEstimated": 15.8,
        "battery.socMinimumObserved": 15,
        "battery.socMaximumObserved": 97,
    }
    api.update_device_properties = AsyncMock(return_value={
        "id": "device-a", "type": "battery", "properties": updated_properties,
    })
    catalog = {"battery": {"type": "battery", "concepts": []}}
    conn = connection()

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
    ):
        websocket_device_save(hass, conn, {
            "id": 70, "type": COMMAND_DEVICE_SAVE, "entry_id": entry.entry_id,
            "device_id": "device-a", "mappings": {}, "properties": {
                "capacityKwh": 20,
                "battery.socMinimum": 10,
                "battery.socMaximum": 90,
                "battery.capacityKwhEstimated": 999,
                "battery.socMinimumObserved": 1,
                "battery.socMaximumObserved": 100,
            },
        })
        await hass.async_block_till_done()

    api.update_device_properties.assert_awaited_once_with("site-a", "device-a", {
        "capacityKwh": 20,
        "battery.socMinimum": 10,
        "battery.socMaximum": 90,
    })
    conn.send_result.assert_called_once_with(70, {"properties": updated_properties})


async def test_battery_suggestions_are_noop_and_configured_values_can_be_cleared(hass):
    """Suggestion-only Save is inert; null removes only manual configuration."""
    entry = make_entry(hass)
    learned = {
        "battery.capacityKwhEstimated": 15.8,
        "battery.socMinimumObserved": 15,
        "battery.socMaximumObserved": 97,
    }
    configured = {
        **learned,
        "capacityKwh": 20,
        "battery.socMinimum": 10,
        "battery.socMaximum": 90,
    }
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(side_effect=[
        {"id": "device-a", "type": "battery", "properties": learned},
        {"id": "device-a", "type": "battery", "properties": configured},
    ])
    api.list_mappings = AsyncMock(return_value=[])
    api.update_device_properties = AsyncMock(return_value={
        "id": "device-a", "type": "battery", "properties": learned,
    })
    catalog = {"battery": {"type": "battery", "concepts": []}}

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
    ):
        for msg_id in (71, 72):
            websocket_device_save(hass, connection(), {
                "id": msg_id, "type": COMMAND_DEVICE_SAVE,
                "entry_id": entry.entry_id, "device_id": "device-a", "mappings": {},
                "properties": {
                    "capacityKwh": None,
                    "battery.socMinimum": None,
                    "battery.socMaximum": None,
                },
            })
            await hass.async_block_till_done()

    api.update_device_properties.assert_awaited_once_with("site-a", "device-a", {
        "capacityKwh": None,
        "battery.socMinimum": None,
        "battery.socMaximum": None,
    })


async def test_solar_installed_capacity_uses_existing_patch_and_keeps_estimate_read_only(hass):
    """Solar configuration writes only installedKWp and supports explicit clearing."""
    entry = make_entry(hass)
    learned = {"solar.installedKwpEstimated": 14.038032}
    configured = {**learned, "installedKWp": 16.75}
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(side_effect=[
        {"id": "solar-a", "type": "solar", "properties": learned},
        {"id": "solar-a", "type": "solar", "properties": configured},
    ])
    api.list_mappings = AsyncMock(return_value=[])
    api.update_device_properties = AsyncMock(side_effect=[
        {"id": "solar-a", "type": "solar", "properties": configured},
        {"id": "solar-a", "type": "solar", "properties": learned},
    ])
    catalog = {"solar": {"type": "solar", "concepts": []}}
    connections = [connection(), connection()]

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
    ):
        for msg_id, properties, conn in (
            (73, {"installedKWp": 16.75, "solar.installedKwpEstimated": 999}, connections[0]),
            (74, {"installedKWp": None, "solar.installedKwpEstimated": 999}, connections[1]),
        ):
            websocket_device_save(hass, conn, {
                "id": msg_id, "type": COMMAND_DEVICE_SAVE,
                "entry_id": entry.entry_id, "device_id": "solar-a",
                "mappings": {}, "properties": properties,
            })
            await hass.async_block_till_done()

    assert [call.args for call in api.update_device_properties.await_args_list] == [
        ("site-a", "solar-a", {"installedKWp": 16.75}),
        ("site-a", "solar-a", {"installedKWp": None}),
    ]
    connections[0].send_result.assert_called_once_with(73, {"properties": configured})
    connections[1].send_result.assert_called_once_with(74, {"properties": learned})


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
    power_proposal = input_proposal("battery.power", "sensor.suggested_power")
    conn = connection()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api._panel_translations", AsyncMock(return_value={"device_type_battery": "Battery"})),
        patch(
            "custom_components.fluks.panel_api.match_entities",
            return_value={"battery.power": power_proposal},
        ) as matcher,
    ):
        websocket_device_detail(
            hass,
            conn,
            {"id": 8, "type": COMMAND_DEVICE_DETAIL, "entry_id": entry.entry_id, "device_id": "device-a"},
        )
        await hass.async_block_till_done()

    result = conn.send_result.call_args.args[1]
    assert result["mappings"]["battery.soc"]["configuration"]["entityId"] == "sensor.existing_soc"
    assert result["proposals"] == {"battery.power": power_proposal}
    assert [item["concept"] for item in matcher.call_args.args[1]] == ["battery.power"]


async def test_explicitly_cleared_suggestion_stays_unmapped(hass):
    """Deleting a suggested Mapping suppresses only that future suggestion."""
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(
        return_value={"id": "device-a", "type": "battery", "properties": {}}
    )
    existing_power = {
        "id": "power-map",
        "concept": "battery.power",
        "direction": "input",
        "configuration": {"version": 1, "entityId": "sensor.suggested_power"},
    }
    api.list_mappings = AsyncMock(side_effect=[[existing_power], [], []])
    api.delete_mapping = AsyncMock()
    api.create_mapping = AsyncMock()
    api.update_mapping = AsyncMock()
    api.update_device_properties = AsyncMock()
    catalog = {
        "battery": {
            "type": "battery",
            "concepts": [
                {"concept": "battery.power", "datatype": "number", "cadence": "realtime", "usages": ["fact"], "source": "mapping"},
                {"concept": "battery.soc", "datatype": "number", "cadence": "realtime", "usages": ["fact"], "source": "mapping"},
            ],
        }
    }
    refresh = AsyncMock()
    soc_proposal = input_proposal("battery.soc", "sensor.suggested_soc")
    matcher = MagicMock(return_value={"battery.soc": soc_proposal})

    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api._panel_translations", AsyncMock(return_value={"device_type_battery": "Battery"})),
        patch("custom_components.fluks.panel_api.match_entities", matcher),
        patch("custom_components.fluks.panel_api.async_refresh_observations", refresh),
    ):
        websocket_device_save(
            hass,
            connection(),
            {
                "id": 90,
                "type": COMMAND_DEVICE_SAVE,
                "entry_id": entry.entry_id,
                "device_id": "device-a",
                "mappings": {
                    "battery.power": {"version": 1, "entityId": ""}
                },
                "properties": {},
            },
        )
        await hass.async_block_till_done()

        detail_connection = connection()
        websocket_device_detail(
            hass,
            detail_connection,
            {
                "id": 91,
                "type": COMMAND_DEVICE_DETAIL,
                "entry_id": entry.entry_id,
                "device_id": "device-a",
            },
        )
        await hass.async_block_till_done()

        websocket_device_save(
            hass,
            connection(),
            {
                "id": 92,
                "type": COMMAND_DEVICE_SAVE,
                "entry_id": entry.entry_id,
                "device_id": "device-a",
                "mappings": {},
                "properties": {},
            },
        )
        await hass.async_block_till_done()

    api.delete_mapping.assert_awaited_once_with("site-a", "power-map")
    api.create_mapping.assert_not_awaited()
    api.update_mapping.assert_not_awaited()
    result = detail_connection.send_result.call_args.args[1]
    assert result["mappings"] == {}
    assert result["proposals"] == {"battery.soc": soc_proposal}
    assert [item["concept"] for item in matcher.call_args.args[1]] == ["battery.soc"]
    assert entry.options[CONF_DEVICE_CONTEXTS]["device-a"][
        CONF_CLEARED_MAPPING_CONCEPTS
    ] == ["battery.power"]


async def test_space_heater_uses_catalog_driven_fact_and_control_flows(hass):
    """A newly catalogued type uses the existing generic Device detail contract."""
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(
        return_value={"id": "heater-device", "type": "spaceHeater", "properties": {}}
    )
    api.list_mappings = AsyncMock(
        return_value=[
            {
                "id": "power-in",
                "concept": "spaceHeater.power",
                "direction": "input",
                "configuration": {"version": 1, "entityId": "sensor.heater_power"},
            },
            {
                "id": "target-out",
                "concept": "spaceHeater.targetTemperature",
                "direction": "output",
                "configuration": output_configuration(),
            },
        ]
    )
    concepts = [
        {"concept": "spaceHeater.power", "datatype": "number", "unit": "W", "cadence": "realtime", "usages": ["fact"], "source": "mapping"},
        {"concept": "spaceHeater.energy", "datatype": "number", "unit": "kWh", "cadence": "interval", "usages": ["fact"], "source": "mapping"},
        {"concept": "spaceHeater.temperature", "datatype": "number", "unit": "°C", "cadence": "realtime", "usages": ["fact"], "source": "mapping"},
        {"concept": "spaceHeater.targetTemperature", "datatype": "number", "unit": "°C", "cadence": "realtime", "usages": ["control"]},
        {"concept": "spaceHeater.state", "datatype": "boolean", "cadence": "realtime", "usages": ["fact", "control"], "source": "mapping"},
    ]
    catalog = {"spaceHeater": {"type": "spaceHeater", "concepts": concepts}}
    translations = {
        "device_type_spaceHeater": "Space heater",
        **{f"concept_{item['concept']}": item["concept"].split(".", 1)[1] for item in concepts},
    }
    conn = connection()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api._panel_translations", AsyncMock(return_value=translations)),
    ):
        websocket_device_detail(
            hass,
            conn,
            {"id": 82, "type": COMMAND_DEVICE_DETAIL, "entry_id": entry.entry_id, "device_id": "heater-device"},
        )
        await hass.async_block_till_done()

    result = conn.send_result.call_args.args[1]
    assert result["type"] == "spaceHeater"
    assert [item["concept"] for item in result["concepts"]] == [
        "spaceHeater.power",
        "spaceHeater.energy",
        "spaceHeater.temperature",
        "spaceHeater.state",
    ]
    assert [item["concept"] for item in result["controls"]] == [
        "spaceHeater.targetTemperature",
        "spaceHeater.state",
    ]
    assert result["mappings"]["spaceHeater.power"]["configuration"] == {
        "version": 1,
        "entityId": "sensor.heater_power",
    }
    assert result["output_mappings"]["spaceHeater.targetTemperature"][0]["id"] == "target-out"


def test_space_heater_rated_power_uses_existing_device_property_allowlist():
    assert "ratedPowerW" in _editable_property_keys("spaceHeater")
    assert "ratedPowerW" not in _editable_property_keys("battery")


async def test_legacy_water_heater_temperature_mapping_is_migrated():
    """Existing backend mapping records move to the renamed canonical concept."""
    legacy = {
        "id": "legacy-temperature",
        "concept": "waterHeater.targetTemperature",
        "direction": "output",
        "mode": "target",
        "configuration": {"version": 1, "actions": []},
    }
    replacement = {
        "id": "new-temperature",
        "concept": "waterHeater.temperature",
        "direction": "output",
        "mode": "target",
        "configuration": legacy["configuration"],
    }
    api = MagicMock(spec=FluksApiClient)
    api.create_mapping = AsyncMock(return_value=replacement)
    api.delete_mapping = AsyncMock()
    api.list_mappings = AsyncMock(return_value=[replacement])

    result = await _migrate_legacy_mappings(
        api, "site-a", "device-a", "integration-a", [legacy],
        "waterHeater.targetTemperature", "waterHeater.temperature",
    )

    api.create_mapping.assert_awaited_once_with("site-a", {
        "integrationId": "integration-a",
        "deviceId": "device-a",
        "concept": "waterHeater.temperature",
        "direction": "output",
        "mode": "target",
        "configuration": legacy["configuration"],
    })
    api.delete_mapping.assert_awaited_once_with("site-a", "legacy-temperature")
    assert result == [replacement]


async def test_site_detail_uses_catalog_and_existing_mapping_directions(hass):
    """Site is presented separately but uses the shared canonical Device detail contract."""
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(
        return_value={"id": "site-device", "type": "site", "properties": {}}
    )
    api.list_mappings = AsyncMock(
        return_value=[
            {"id": "power-in", "concept": "site.power", "direction": "input", "configuration": {"entityId": "sensor.grid_power"}},
            {"id": "power-out", "concept": "site.power", "direction": "output", "mode": "balance", "valueCondition": "gtZero", "configuration": output_configuration()},
            {"id": "import-in", "concept": "site.importEnergy", "direction": "input", "configuration": {"entityId": "sensor.grid_import"}},
        ]
    )
    catalog = {
        "site": {
            "type": "site",
            "concepts": [
                {"concept": "site.power", "datatype": "number", "unit": "W", "cadence": "realtime", "usages": ["fact", "control"], "source": "mapping"},
                {"concept": "site.energy", "datatype": "number", "unit": "kWh", "cadence": "interval", "usages": ["fact"], "source": "mapping"},
                {"concept": "site.importEnergy", "datatype": "number", "unit": "kWh", "cadence": "interval", "usages": ["fact"], "source": "mapping"},
                {"concept": "site.exportEnergy", "datatype": "number", "unit": "kWh", "cadence": "interval", "usages": ["fact"], "source": "mapping"},
                {"concept": "site.outdoorTemperature", "datatype": "number", "unit": "°C", "cadence": "realtime", "usages": ["fact"], "source": "mapping"},
            ],
        }
    }
    conn = connection()
    translations = {
        "device_type_site": "Site",
        "concept_site.power": "Power",
        "concept_site.energy": "Energy",
        "concept_site.importEnergy": "Import energy",
        "concept_site.exportEnergy": "Export energy",
        "concept_site.outdoorTemperature": "Outdoor temperature",
    }
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api._panel_translations", AsyncMock(return_value=translations)),
    ):
        websocket_device_detail(
            hass,
            conn,
            {"id": 81, "type": COMMAND_DEVICE_DETAIL, "entry_id": entry.entry_id, "device_id": "site-device"},
        )
        await hass.async_block_till_done()

    result = conn.send_result.call_args.args[1]
    assert result["type"] == "site"
    assert result["name"] == "Home"
    assert [item["concept"] for item in result["concepts"]] == [
        "site.power", "site.energy", "site.importEnergy", "site.exportEnergy", "site.outdoorTemperature"
    ]
    assert result["concepts"][-1]["label"] == "Outdoor temperature"
    assert all(item["concept"] != "site.temperature" for item in result["concepts"])
    assert [item["concept"] for item in result["controls"]] == ["site.power"]
    assert result["mappings"]["site.power"]["configuration"]["entityId"] == "sensor.grid_power"
    assert result["output_mappings"]["site.power"][0]["id"] == "power-out"
    assert result["output_mappings"]["site.power"][0]["valueCondition"] == "gtZero"
    assert result["proposals"] == {}
    api.get_device.assert_awaited_once_with("site-a", "site-device")
    api.list_mappings.assert_awaited_once_with("site-a", device_id="site-device")


async def test_site_input_mapping_uses_shared_incremental_device_save(hass):
    entry = make_entry(hass)
    hass.states.async_set(
        "sensor.grid_power", "1200", {"unit_of_measurement": "W"}
    )
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(
        return_value={"id": "site-device", "type": "site", "properties": {}}
    )
    api.list_mappings = AsyncMock(return_value=[])
    api.create_mapping = AsyncMock()
    api.update_device_properties = AsyncMock()
    catalog = {
        "site": {
            "type": "site",
            "concepts": [
                {"concept": "site.power", "datatype": "number", "unit": "W", "cadence": "realtime", "usages": ["fact", "control"], "source": "mapping"}
            ],
        }
    }
    conn = connection()
    refresh = AsyncMock()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch(
            "custom_components.fluks.panel_api.async_refresh_observations",
            refresh,
        ),
    ):
        websocket_device_save(
            hass,
            conn,
            {
                "id": 82,
                "type": COMMAND_DEVICE_SAVE,
                "entry_id": entry.entry_id,
                "device_id": "site-device",
                "mappings": {
                    "site.power": {
                        "version": 1,
                        "entityId": "sensor.grid_power",
                        "transforms": [{"type": "invert"}],
                    }
                },
                "properties": {},
            },
        )
        await hass.async_block_till_done()

    payload = api.create_mapping.await_args.args[1]
    assert payload["deviceId"] == "site-device"
    assert payload["concept"] == "site.power"
    assert payload["direction"] == "input"
    assert payload["configuration"]["entityId"] == "sensor.grid_power"
    assert payload["configuration"]["transforms"] == [{"type": "invert"}]
    refresh.assert_awaited_once_with(hass, entry.entry_id)
    api.update_device_properties.assert_not_awaited()


async def test_site_outdoor_temperature_attribute_mapping_uses_catalog_contract(hass):
    """The new Site fact accepts the same state/attribute Mapping contract as other facts."""
    entry = make_entry(hass)
    hass.states.async_set(
        "sensor.weather", "ok", {"outdoor_temperature": 68, "outdoor_temperature_unit": "°F"}
    )
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(
        return_value={"id": "site-device", "type": "site", "properties": {}}
    )
    api.list_mappings = AsyncMock(return_value=[])
    api.create_mapping = AsyncMock()
    api.update_device_properties = AsyncMock()
    catalog = {
        "site": {
            "type": "site",
            "concepts": [
                {"concept": "site.power", "datatype": "number", "unit": "W", "usages": ["fact", "control"], "source": "mapping"},
                {"concept": "site.outdoorTemperature", "datatype": "number", "unit": "°C", "usages": ["fact"], "source": "mapping"},
            ],
        }
    }
    conn = connection()
    refresh = AsyncMock()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api.async_refresh_observations", refresh),
    ):
        websocket_device_save(
            hass,
            conn,
            {
                "id": 83,
                "type": COMMAND_DEVICE_SAVE,
                "entry_id": entry.entry_id,
                "device_id": "site-device",
                "mappings": {
                    "site.outdoorTemperature": {
                        "version": 1,
                        "entityId": "sensor.weather",
                        "attribute": "outdoor_temperature",
                        "unit": "°F",
                        "transforms": [
                            {"type": "offset", "amount": -32},
                            {"type": "scale", "factor": 5 / 9},
                        ],
                    }
                },
                "properties": {},
            },
        )
        await hass.async_block_till_done()

    payload = api.create_mapping.await_args.args[1]
    assert payload["concept"] == "site.outdoorTemperature"
    assert payload["configuration"] == {
        "version": 1,
        "entityId": "sensor.weather",
        "attribute": "outdoor_temperature",
        "unit": "°F",
        "transforms": [
            {"type": "offset", "amount": -32},
            {"type": "scale", "factor": 5 / 9},
        ],
    }
    refresh.assert_awaited_once_with(hass, entry.entry_id)


def output_configuration(value=50):
    return {
        "version": 1,
        "actions": [
            {
                "type": "serviceCall",
                "service": "number.set_value",
                "target": {"entityId": "number.goodwe_target"},
                "data": {"value": {"kind": "literal", "value": value}},
            }
        ],
    }


def control_catalog():
    return {
        "battery": {
            "type": "battery",
            "concepts": [
                {
                    "concept": "battery.power",
                    "datatype": "number",
                    "unit": "W",
                    "usages": ["fact", "control"],
                    "source": "mapping",
                    "mappingModes": [None, "target", "limit", "balance", "release", "charge", "discharge"],
                }
            ],
        }
    }


async def run_control_save(hass, entry, api, configuration, *, msg_id=20):
    conn = connection()
    message = {
        "id": msg_id,
        "type": COMMAND_CONTROL_SAVE,
        "entry_id": entry.entry_id,
        "device_id": "device-a",
        "concept": "battery.power",
    }
    message["behaviors"] = [] if configuration is None else [
        {"mode": None, "configuration": configuration}
    ]
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch(
            "custom_components.fluks.panel_api._catalog",
            AsyncMock(return_value=control_catalog()),
        ),
        patch(
            "custom_components.fluks.panel_api.async_validate_control_configuration",
            AsyncMock(return_value=True),
        ),
    ):
        websocket_control_save(hass, conn, message)
        await hass.async_block_till_done()
    return conn


async def test_control_save_creates_documented_output_mapping(hass):
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={"id": "device-a", "type": "battery"})
    api.list_mappings = AsyncMock(return_value=[])
    api.create_mapping = AsyncMock()

    configuration = output_configuration()
    conn = await run_control_save(hass, entry, api, configuration)

    payload = api.create_mapping.await_args.args[1]
    assert payload == {
        "integrationId": "integration-a",
        "deviceId": "device-a",
        "concept": "battery.power",
        "direction": "output",
        "mode": None,
        "configuration": configuration,
    }
    conn.send_result.assert_called_once_with(20, {"changed": True})


async def test_control_save_reconciles_separate_mode_mapping_records(hass):
    entry = make_entry(hass)
    default = output_configuration(40)
    target = output_configuration(60)
    release = output_configuration(0)
    charge = output_configuration(80)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={"id": "device-a", "type": "battery"})
    api.list_mappings = AsyncMock(return_value=[
        {"id": "default-map", "concept": "battery.power", "direction": "output", "mode": None, "configuration": default},
        {"id": "limit-map", "concept": "battery.power", "direction": "output", "mode": "limit", "configuration": output_configuration(50)},
    ])
    api.create_mapping = AsyncMock()
    api.update_mapping = AsyncMock()
    api.delete_mapping = AsyncMock()
    conn = connection()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=control_catalog())),
        patch("custom_components.fluks.panel_api.async_validate_control_configuration", AsyncMock(return_value=True)),
    ):
        websocket_control_save(hass, conn, {
            "id": 24, "type": COMMAND_CONTROL_SAVE, "entry_id": entry.entry_id,
            "device_id": "device-a", "concept": "battery.power",
            "behaviors": [
                {"mode": None, "configuration": default},
                {"mode": "target", "configuration": target},
                {"mode": "release", "configuration": release},
                {"mode": "charge", "configuration": charge},
            ],
        })
        await hass.async_block_till_done()

    api.update_mapping.assert_not_awaited()
    api.delete_mapping.assert_awaited_once_with("site-a", "limit-map")
    assert [call.args[1]["mode"] for call in api.create_mapping.await_args_list] == ["target", "release", "charge"]
    conn.send_result.assert_called_once_with(24, {"changed": True})


async def test_site_control_uses_shared_output_mapping_save(hass):
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={"id": "site-device", "type": "site"})
    api.list_mappings = AsyncMock(return_value=[])
    api.create_mapping = AsyncMock()
    configuration = output_configuration()
    catalog = {
        "site": {
            "type": "site",
            "concepts": [
                {"concept": "site.power", "datatype": "number", "unit": "W", "usages": ["fact", "control"], "source": "mapping"},
                {"concept": "site.energy", "datatype": "number", "unit": "kWh", "usages": ["fact"], "source": "mapping"},
            ],
        }
    }
    conn = connection()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api.async_validate_control_configuration", AsyncMock(return_value=True)),
    ):
        websocket_control_save(
            hass,
            conn,
            {
                "id": 83,
                "type": COMMAND_CONTROL_SAVE,
                "entry_id": entry.entry_id,
                "device_id": "site-device",
                "concept": "site.power",
                "behaviors": [{"mode": None, "configuration": configuration}],
            },
        )
        await hass.async_block_till_done()

    payload = api.create_mapping.await_args.args[1]
    assert payload == {
        "integrationId": "integration-a",
        "deviceId": "site-device",
        "concept": "site.power",
        "direction": "output",
        "mode": None,
        "configuration": configuration,
    }


async def test_site_balance_value_conditions_persist_as_normal_mappings(hass):
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={"id": "site-device", "type": "site"})
    api.list_mappings = AsyncMock(return_value=[])
    api.create_mapping = AsyncMock()
    configuration = output_configuration()
    catalog = {"site": {"type": "site", "concepts": [
        {"concept": "site.power", "datatype": "number", "unit": "W",
         "usages": ["control"], "mappingModes": [None, "balance", "release"]},
    ]}}
    conn = connection()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api.async_validate_control_configuration", AsyncMock(return_value=True)),
    ):
        websocket_control_save(hass, conn, {
            "id": 84, "type": COMMAND_CONTROL_SAVE, "entry_id": entry.entry_id,
            "device_id": "site-device", "concept": "site.power",
            "behaviors": [
                {"mode": "balance", "valueCondition": "gtZero", "configuration": configuration},
                {"mode": "balance", "valueCondition": "ltZero", "configuration": configuration},
                {"mode": "balance", "valueCondition": "eqZero", "configuration": configuration},
            ],
        })
        await hass.async_block_till_done()

    assert [call.args[1]["valueCondition"] for call in api.create_mapping.await_args_list] == [
        "gtZero", "ltZero", "eqZero"
    ]


async def test_site_balance_value_condition_updates_through_mapping_patch(hass):
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={"id": "site-device", "type": "site"})
    api.list_mappings = AsyncMock(return_value=[{
        "id": "balance-map", "concept": "site.power", "direction": "output",
        "mode": "balance", "valueCondition": "gtZero", "configuration": output_configuration(),
    }])
    api.update_mapping = AsyncMock()
    api.delete_mapping = AsyncMock()
    configuration = output_configuration(75)
    catalog = {"site": {"type": "site", "concepts": [
        {"concept": "site.power", "datatype": "number", "unit": "W",
         "usages": ["control"], "mappingModes": [None, "balance", "release"]},
    ]}}
    conn = connection()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch("custom_components.fluks.panel_api._catalog", AsyncMock(return_value=catalog)),
        patch("custom_components.fluks.panel_api.async_validate_control_configuration", AsyncMock(return_value=True)),
    ):
        websocket_control_save(hass, conn, {
            "id": 85, "type": COMMAND_CONTROL_SAVE, "entry_id": entry.entry_id,
            "device_id": "site-device", "concept": "site.power",
            "behaviors": [{"mode": "balance", "valueCondition": "ltZero", "configuration": configuration}],
        })
        await hass.async_block_till_done()

    api.update_mapping.assert_awaited_once_with(
        "site-a", "balance-map", configuration, value_condition="ltZero"
    )


async def test_control_save_is_noop_for_machine_equal_configuration(hass):
    entry = make_entry(hass)
    configuration = output_configuration()
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={"id": "device-a", "type": "battery"})
    api.list_mappings = AsyncMock(
        return_value=[
            {
                "id": "output-map",
                "concept": "battery.power",
                "direction": "output",
                "configuration": configuration,
            }
        ]
    )
    api.create_mapping = AsyncMock()
    api.update_mapping = AsyncMock()
    api.delete_mapping = AsyncMock()

    conn = await run_control_save(hass, entry, api, configuration)

    conn.send_result.assert_called_once_with(20, {"changed": False})
    api.create_mapping.assert_not_awaited()
    api.update_mapping.assert_not_awaited()
    api.delete_mapping.assert_not_awaited()


async def test_control_save_patches_changed_and_deletes_cleared_mapping(hass):
    entry = make_entry(hass)
    existing = output_configuration(50)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={"id": "device-a", "type": "battery"})
    api.list_mappings = AsyncMock(
        return_value=[
            {
                "id": "output-map",
                "concept": "battery.power",
                "direction": "output",
                "configuration": existing,
            }
        ]
    )
    api.update_mapping = AsyncMock()
    api.delete_mapping = AsyncMock()

    changed = output_configuration(70)
    await run_control_save(hass, entry, api, changed, msg_id=21)
    api.update_mapping.assert_awaited_once_with("site-a", "output-map", changed)

    api.update_mapping.reset_mock()
    await run_control_save(hass, entry, api, None, msg_id=22)
    api.delete_mapping.assert_awaited_once_with("site-a", "output-map")
    api.update_mapping.assert_not_awaited()


async def test_control_save_rejects_non_control_concept(hass):
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={"id": "device-a", "type": "battery"})
    api.list_mappings = AsyncMock(return_value=[])
    conn = connection()
    with (
        patch("custom_components.fluks.panel_api._api", return_value=api),
        patch(
            "custom_components.fluks.panel_api._catalog",
            AsyncMock(
                return_value={
                    "battery": {
                        "type": "battery",
                        "concepts": [
                            {"concept": "battery.soc", "usages": ["fact"]}
                        ],
                    }
                }
            ),
        ),
    ):
        websocket_control_save(
            hass,
            conn,
            {
                "id": 23,
                "type": COMMAND_CONTROL_SAVE,
                "entry_id": entry.entry_id,
                "device_id": "device-a",
                "concept": "battery.soc",
                "behaviors": [{"mode": None, "configuration": {"version": 1, "actions": []}}],
            },
        )
        await hass.async_block_till_done()

    conn.send_result.assert_not_called()
    conn.send_error.assert_called_once()


async def test_control_save_returns_structured_error_for_invalid_configuration(hass):
    entry = make_entry(hass)
    api = MagicMock(spec=FluksApiClient)
    api.get_device = AsyncMock(return_value={"id": "device-a", "type": "battery"})
    api.list_mappings = AsyncMock(return_value=[])
    conn = await run_control_save(
        hass, entry, api, {"version": 1, "actions": []}, msg_id=24
    )
    conn.send_result.assert_not_called()
    conn.send_error.assert_called_once_with(
        24, "invalid_mapping", "The fluks operation could not be completed"
    )


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
