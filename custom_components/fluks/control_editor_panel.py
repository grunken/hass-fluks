"""Registration for the embedded fluks integration configuration panel."""

from __future__ import annotations

import hashlib
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PANEL_URL_PATH = "fluks-control-editor"
PANEL_STATIC_URL = "/fluks-control-editor-static"
PANEL_ICONS_URL = "/fluks-device-icons"
PANEL_ELEMENT = "fluks-control-editor-panel"
PANEL_DIRECTORY = Path(__file__).parent / "frontend"
PANEL_ICONS_DIRECTORY = Path(__file__).parent / "icons"


def _frontend_revision() -> str:
    """Tie the panel and its imported editor to the same browser module revision."""
    digest = hashlib.sha256()
    for filename in ("control-editor-panel.js", "control-action-editor.js"):
        digest.update((PANEL_DIRECTORY / filename).read_bytes())
    return digest.hexdigest()[:12]


async def async_register_control_editor_panel(hass: HomeAssistant) -> None:
    """Register the hidden, admin-only production configuration panel."""
    revision = await hass.async_add_executor_job(_frontend_revision)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                PANEL_STATIC_URL,
                str(PANEL_DIRECTORY),
                cache_headers=False,
            ),
            StaticPathConfig(
                PANEL_ICONS_URL,
                str(PANEL_ICONS_DIRECTORY),
                cache_headers=True,
            ),
        ]
    )
    await panel_custom.async_register_panel(
        hass=hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        module_url=f"{PANEL_STATIC_URL}/control-editor-panel.js?rev={revision}",
        sidebar_title=None,
        sidebar_icon=None,
        embed_iframe=False,
        require_admin=True,
        config_panel_domain=DOMAIN,
    )


def async_unregister_control_editor_panel(hass: HomeAssistant) -> None:
    """Remove the production panel through Home Assistant's panel lifecycle."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
