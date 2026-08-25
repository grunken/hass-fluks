"""Tests for the opt-in Home Assistant control-editor panel mechanism."""

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.fluks.const import DOMAIN
from custom_components.fluks.control_editor_panel import (
    PANEL_ELEMENT,
    PANEL_ICONS_URL,
    PANEL_STATIC_URL,
    PANEL_URL_PATH,
    _frontend_revision,
    async_register_control_editor_panel,
    async_unregister_control_editor_panel,
)


async def test_panel_registration_is_hidden_admin_only_and_domain_scoped(hass):
    """The official config-panel route is hidden and receives ConfigEntry IDs."""
    http = MagicMock()
    http.async_register_static_paths = AsyncMock()
    hass.http = http
    revision_job = AsyncMock(return_value="content-digest")
    with (
        patch.object(hass, "async_add_executor_job", revision_job),
        patch(
            "custom_components.fluks.control_editor_panel.panel_custom.async_register_panel",
            new=AsyncMock(),
        ) as register,
    ):
        await async_register_control_editor_panel(hass)

    revision_job.assert_awaited_once_with(_frontend_revision)
    http.async_register_static_paths.assert_awaited_once()
    paths = http.async_register_static_paths.await_args.args[0]
    assert {item.url_path for item in paths} == {PANEL_STATIC_URL, PANEL_ICONS_URL}
    register.assert_awaited_once_with(
        hass=hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        module_url=f"{PANEL_STATIC_URL}/control-editor-panel.js?rev=content-digest",
        sidebar_title=None,
        sidebar_icon=None,
        embed_iframe=False,
        require_admin=True,
        config_panel_domain=DOMAIN,
    )


async def test_panel_unregister_uses_home_assistant_lifecycle(hass):
    """Panel removal never manipulates frontend registries directly."""
    with patch(
        "custom_components.fluks.control_editor_panel.frontend.async_remove_panel"
    ) as remove:
        async_unregister_control_editor_panel(hass)
    remove.assert_called_once_with(hass, PANEL_URL_PATH)


def test_panel_frontend_uses_hass_without_credentials_or_direct_backend_calls():
    """Context is ConfigEntry-scoped while secrets remain in Python."""
    source = (
        __import__("pathlib").Path(
            "custom_components/fluks/frontend/control-editor-panel.js"
        ).read_text()
    )
    assert "config_entry" in source
    assert "this._hass.states" in source
    assert "integrationKey" not in source
    assert "human JWT" not in source
    assert "energy-api.grunken.dk" not in source
    assert "fetch(" not in source
    assert ".callService(" not in source
    assert "history.back()" in source
    assert "context_missing" in source
    assert "entryId !== this._entryId" in source
    assert "this._pendingControl = undefined" in source
    assert "MODULE_REVISION" in source
    assert "control-action-editor.js${MODULE_REVISION" in source


def test_frontend_revision_changes_when_parent_module_changes(monkeypatch):
    """The revision is derived from the production parent module contents."""
    from custom_components.fluks import control_editor_panel

    baseline = _frontend_revision()
    original = control_editor_panel.Path.read_bytes

    def changed(path):
        data = original(path)
        return data + (b"changed" if path.name == "control-editor-panel.js" else b"")

    monkeypatch.setattr(control_editor_panel.Path, "read_bytes", changed)
    assert _frontend_revision() != baseline


def test_frontend_revision_changes_when_child_module_changes(monkeypatch):
    """The imported child editor can never retain an older browser module key."""
    from custom_components.fluks import control_editor_panel

    baseline = _frontend_revision()
    original = control_editor_panel.Path.read_bytes

    def changed(path):
        data = original(path)
        return data + (b"changed" if path.name == "control-action-editor.js" else b"")

    monkeypatch.setattr(control_editor_panel.Path, "read_bytes", changed)
    assert _frontend_revision() != baseline


def test_panel_is_activated_by_production_setup():
    """The approved panel is now the production Configure destination."""
    setup_source = __import__("pathlib").Path(
        "custom_components/fluks/__init__.py"
    ).read_text()
    assert "async_register_control_editor_panel" in setup_source
    assert "async_register_panel_commands" in setup_source


def test_configure_has_one_production_destination():
    """ConfigEntry Configure is panel-owned; no duplicate Options Flow remains."""
    from pathlib import Path

    from custom_components.fluks.config_flow import FluksConfigFlow

    assert "async_get_options_flow" not in FluksConfigFlow.__dict__
    assert not Path("custom_components/fluks/options_flow.py").exists()
    assert not Path("custom_components/fluks/icons.json").exists()


def test_panel_contains_administration_and_persisted_controls_without_execution():
    """The production surface persists configuration but never executes actions."""
    source = __import__("pathlib").Path(
        "custom_components/fluks/frontend/control-editor-panel.js"
    ).read_text()
    for command in (
        "fluks/config/context",
        "fluks/config/device",
        "fluks/config/add_review",
        "fluks/config/add_save",
        "fluks/config/device_save",
        "fluks/config/control_save",
        "fluks/config/control_capabilities",
        "fluks/config/device_delete",
        "fluks/config/site_delete",
    ):
        assert command in source
    assert "fluks-control-action-editor" in source
    assert "control-saved" in source
    assert "e.detail.configuration" in source
    assert "output_mappings" in source
    assert "@media(max-width:700px)" in source
    assert "history.pushState" in source
    assert "iframe" not in source.lower()
    assert "WebSocket(" not in source


def test_production_panel_visual_structure_uses_icons_search_and_context_menus():
    """The panel follows the approved compact mocks without changing identities."""
    source = __import__("pathlib").Path(
        "custom_components/fluks/frontend/control-editor-panel.js"
    ).read_text()
    assert 'const TAGLINE = "Your Energy. Decides together."' in source
    assert 'const DEVICE_ICON_BASE = "/fluks-device-icons"' in source
    assert 'replace(/([a-z0-9])([A-Z])/g, "$1_$2")' in source
    assert 'class="type-grid"' in source
    assert 'class="context-menu" id="site-actions"' in source
    assert 'id="device-actions-menu"' in source
    assert 'type="search"' in source
    assert "search_devices" in source
    assert "search_entities" in source
    assert 'id="ha-device"' not in source
    assert "_renderAddReview" not in source
    assert "_renderAddType" not in source
    assert "_selectAddHaDevice" in source
    assert "fluks/config/add_save" in source


def test_all_canonical_icon_types_resolve_to_supplied_assets():
    """Camel-case backend types map generically to approved filenames."""
    from pathlib import Path
    import re

    types = (
        "appliance",
        "battery",
        "electricVehicle",
        "generator",
        "heatPump",
        "solar",
        "waterHeater",
    )
    for device_type in types:
        filename = re.sub(r"(?<!^)(?=[A-Z])", "_", device_type).lower() + ".png"
        assert Path("custom_components/fluks/icons", filename).is_file()


def test_destructive_views_use_named_resources_and_resource_specific_auth_copy():
    """Confirmation and authentication explain the exact destructive operation."""
    import json
    from pathlib import Path

    source = Path(
        "custom_components/fluks/frontend/control-editor-panel.js"
    ).read_text()
    en = json.loads(Path("custom_components/fluks/translations/en.json").read_text())[
        "panel"
    ]
    da = json.loads(Path("custom_components/fluks/translations/da.json").read_text())[
        "panel"
    ]

    assert '{ deviceName: this._detail.label }' in source
    assert '{ siteName: this._context.site.name }' in source
    assert '_credentials("delete_device_auth_title", "delete_device_auth_body"' in source
    assert '_credentials("delete_site_auth_title", "delete_site_auth_body"' in source
    assert en["delete_device_title"] == "Delete {deviceName}?"
    assert en["delete_site_title"] == "Delete {siteName}?"
    assert "owns this device's Site" in en["delete_device_auth_body"]
    assert "owns this site" in en["delete_site_auth_body"]
    assert da["delete_device_title"] == "Slet {deviceName}?"
    assert da["delete_site_title"] == "Slet {siteName}?"
    assert "ejer enhedens site" in da["delete_device_auth_body"]
    assert "ejer dette site" in da["delete_site_auth_body"]


def test_searchable_selectors_truncate_only_presentation_and_format_numeric_state():
    """Long identities retain exact values while the visual rows stay bounded."""
    from pathlib import Path

    source = Path(
        "custom_components/fluks/frontend/control-editor-panel.js"
    ).read_text()

    assert "data-picker-value=\"${esc(id || \"\")}\"" in source
    assert "data-value=\"${esc(item.id)}\"" in source
    assert "text-overflow:ellipsis" in source
    assert "white-space:nowrap" in source
    assert ".picker-value{width:100%;height:62px" in source
    assert ".picker-row{width:100%;height:62px" in source
    assert "max-width:28%" in source
    assert "new Intl.NumberFormat" in source
    assert "maximumFractionDigits: 3" in source
    assert 'title="${esc(item.secondary || item.id)}"' in source

    for entity_id in (
        "sensor.inverter_goodwe_1_battery_0_state_of_charge",
        "sensor.inverter_goodwe_1_battery_charge_today",
        "sensor.inverter_goodwe_1_battery_discharge_today",
    ):
        # IDs are never shortened before being stored in data attributes/hidden inputs.
        assert len(entity_id) > 40
