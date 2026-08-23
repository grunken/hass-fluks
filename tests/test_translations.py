"""Tests for fluks milestone 1 translations."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from homeassistant.helpers.icon import async_get_icons

from custom_components.fluks.const import DOMAIN

TRANSLATIONS = Path("custom_components") / DOMAIN / "translations"


def load_translation(language: str) -> dict:
    """Load a custom integration translation file."""
    return json.loads((TRANSLATIONS / f"{language}.json").read_text())


def key_structure(value, prefix=()):
    """Return every nested translation key path."""
    paths = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = (*prefix, key)
            paths.add(path)
            paths.update(key_structure(child, path))
    return paths


def string_literals(path: Path) -> set[str]:
    """Return string literals from a Python source file."""
    tree = ast.parse(path.read_text())
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_translation_files_are_valid_and_have_key_parity():
    """English and Danish contain identical translation structures."""
    english = load_translation("en")
    danish = load_translation("da")

    assert key_structure(english) == key_structure(danish)
    assert english["config"]["step"]["user"]["title"] == (
        "Your Energy. Decides together."
    )
    assert danish["config"]["step"]["user"]["title"] == (
        "Your Energy. Decides together."
    )


def test_config_flow_translation_keys_exist():
    """Every step, error, and abort key used by the flow is translated."""
    english = load_translation("en")["config"]
    source_literals = string_literals(
        Path("custom_components") / DOMAIN / "config_flow.py"
    )

    expected_steps = {
        "user",
        "login",
        "register",
        "verify",
        "site_choice",
        "site",
        "create_site",
        "register_integration",
    }
    expected_errors = {
        "cannot_connect",
        "invalid_auth",
        "invalid_input",
        "invalid_verification_code",
        "invalid_verification_code_format",
        "unauthorized",
        "unknown",
    }
    expected_aborts = {
        "already_configured",
        "missing_registration",
        "unauthorized",
        "unknown",
    }

    assert expected_steps <= english["step"].keys()
    assert expected_errors <= english["error"].keys()
    assert expected_aborts <= english["abort"].keys()
    assert (
        expected_steps | expected_errors | (expected_aborts - {"already_configured"})
        <= source_literals
    )


def test_options_flow_presentation_is_translated_in_english_and_danish():
    """Current backend types and review fields have matching friendly text."""
    english = load_translation("en")["options"]
    danish = load_translation("da")["options"]
    physical_types = {
        "solar",
        "generator",
        "battery",
        "electricVehicle",
        "heatPump",
        "waterHeater",
        "appliance",
    }
    menu_en = english["step"]["device_type"]
    menu_da = danish["step"]["device_type"]
    assert physical_types == menu_en["menu_options"].keys()
    assert physical_types == menu_en["menu_option_descriptions"].keys()
    assert physical_types == menu_da["menu_options"].keys()
    assert physical_types == menu_da["menu_option_descriptions"].keys()
    assert all(value and "." not in value for value in menu_en["menu_options"].values())
    for device_type in physical_types:
        ha_step = f"ha_device_{device_type}"
        review_step = f"review_{device_type}"
        assert ha_step in english["step"] and ha_step in danish["step"]
        assert review_step in english["step"] and review_step in danish["step"]
        assert "Home Assistant device" in english["step"][ha_step]["data"].values()
        assert "change or remove" in english["step"][review_step][
            "description"
        ].lower()

    assert "Battery" in english["step"]["ha_device_battery"]["title"]
    assert "Battery measurements" in english["step"]["ha_device_battery"][
        "description"
    ]
    assert "Solar" in english["step"]["ha_device_solar"]["title"]
    assert "Solar measurements" in english["step"]["ha_device_solar"][
        "description"
    ]


def test_options_flow_section_icons_use_native_icons_json_structure():
    """Review section icons use only HA's native options-flow metadata."""
    icons = json.loads(
        (Path("custom_components") / DOMAIN / "icons.json").read_text()
    )
    english_steps = load_translation("en")["options"]["step"]
    steps = icons["options"]["step"]

    assert steps
    for step_id, step_metadata in steps.items():
        assert step_id in english_steps
        assert set(step_metadata) == {"sections"}
        translated_sections = english_steps[step_id]["sections"]
        for section_id, icon in step_metadata["sections"].items():
            assert section_id in translated_sections
            assert icon.startswith("mdi:")
            assert "/" not in icon and ".png" not in icon


async def test_options_flow_section_icons_load_through_home_assistant(hass):
    """HA's native icon loader resolves the Options Flow section metadata."""
    icons = json.loads(
        (Path("custom_components") / DOMAIN / "icons.json").read_text()
    )

    resources = await async_get_icons(hass, "options", {DOMAIN})

    assert resources == {DOMAIN: icons["options"]}
    assert resources[DOMAIN]["step"]["review_battery"]["sections"] == {
        "measurements": "mdi:gauge",
        "energy": "mdi:lightning-bolt",
    }
    assert resources[DOMAIN]["step"]["review_solar"]["sections"][
        "installation"
    ] == "mdi:solar-panel"
