"""Tests for fluks translation coverage and Home Assistant locale selection."""

from __future__ import annotations

import ast
import json
import re
from string import Formatter

import pytest
from pathlib import Path

from custom_components.fluks.const import DOMAIN
from custom_components.fluks.panel_api import _load_panel_translations

TRANSLATIONS = Path("custom_components") / DOMAIN / "translations"
LANGUAGES = ("en", "da", "nb", "sv", "de")

PANEL_TRANSLATIONS = Path("custom_components") / DOMAIN / "panel_translations"


def load_translation(language: str) -> dict:
    """Load a custom integration translation file."""
    return json.loads((TRANSLATIONS / f"{language}.json").read_text())


def load_panel_translation(language: str) -> dict:
    """Load the custom panel strings, kept outside HA's translation schema."""
    return json.loads((PANEL_TRANSLATIONS / f"{language}.json").read_text())


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
    """Supported languages contain identical native translation structures."""
    english = load_translation("en")
    danish = load_translation("da")

    for language in LANGUAGES:
        assert key_structure(load_translation(language)) == key_structure(english)
    assert "panel" not in english
    assert "panel" not in danish
    assert english["config"]["step"]["user"]["title"] == (
        "Your energy. Decides together."
    )
    assert danish["config"]["step"]["user"]["title"] == (
        "Your energy. Decides together."
    )


def test_panel_translation_loader_reads_the_separate_resource():
    """The runtime panel continues loading its strings after HA schema split."""
    assert _load_panel_translations("en")["unknown_device"] == "Unknown device"
    assert _load_panel_translations("da")["unknown_device"] == "Ukendt enhed"


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
        "reauth_confirm",
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
        "reauth_successful",
    }

    assert expected_steps <= english["step"].keys()
    assert expected_errors <= english["error"].keys()
    assert expected_aborts <= english["abort"].keys()
    assert (
        expected_steps | expected_errors | (expected_aborts - {"already_configured"})
        <= source_literals
    )


def test_panel_device_and_concept_presentation_is_translated():
    """The production panel owns localized Device and concept presentation."""
    english = load_panel_translation("en")
    danish = load_panel_translation("da")
    assert key_structure(english) == key_structure(danish)
    assert english["tagline"] == "Your energy. Decides together."
    canonical_types = {
        "solar",
        "generator",
        "battery",
        "electricVehicle",
        "heatPump",
        "spaceHeater",
        "waterHeater",
        "appliance",
        "site",
    }
    assert {key.removeprefix("device_type_") for key in english if key.startswith("device_type_") and key != "device_type_fallback"} == canonical_types
    assert {key.removeprefix("device_type_") for key in danish if key.startswith("device_type_") and key != "device_type_fallback"} == canonical_types
    for device_type in canonical_types:
        assert english[f"device_type_{device_type}"]
        assert danish[f"device_type_{device_type}"]
    assert english["concept_battery.soc"] == "State of charge"
    assert danish["concept_battery.soc"] == "Ladeniveau"
    assert english["concept_site.importEnergy"] == "Import energy"
    assert english["concept_electricVehicle.position"] == "Position"
    assert english["concept_electricVehicle.odoMeter"] == "Odometer"
    assert english["concept_electricVehicle.targetSocMaximum"] == "Maximum SOC"
    assert danish["concept_electricVehicle.position"] == "Position"
    assert danish["concept_electricVehicle.odoMeter"] == "Kilometertæller"
    assert danish["concept_electricVehicle.targetSocMaximum"] == "Maksimum-SOC"
    assert danish["concept_site.exportEnergy"] == "Eksporteret energi"
    assert english["concept_site.outdoorTemperature"] == "Outdoor temperature"
    assert danish["concept_site.outdoorTemperature"] == "Udendørstemperatur"
    assert english["concept_waterHeater.temperature"] == "Water temperature"
    assert danish["concept_waterHeater.temperature"] == "Vandtemperatur"
    assert "concept_waterHeater.targetTemperature" not in english
    assert "concept_waterHeater.targetTemperature" not in danish
    assert english["concept_spaceHeater.targetTemperature"] == "Target temperature"
    assert danish["concept_spaceHeater.targetTemperature"] == "Ønsket temperatur"
    assert english["concept_heatPump.temperature"] == "Temperature"
    assert danish["concept_heatPump.temperature"] == "Temperatur"
    assert danish["concept_heatPump.tankTemperature"] == "Beholdertemperatur"
    compass_directions = (
        "north",
        "northeast",
        "east",
        "southeast",
        "south",
        "southwest",
        "west",
        "northwest",
    )
    assert [english[f"compass_{direction}"] for direction in compass_directions] == [
        "North",
        "Northeast",
        "East",
        "Southeast",
        "South",
        "Southwest",
        "West",
        "Northwest",
    ]
    assert danish["compass_southeast"] == "Sydøst"
    expected_heat_pump_concepts = {
        "power",
        "energy",
        "bufferEnergy",
        "tankEnergy",
        "temperature",
        "tankTemperature",
        "state",
    }
    for translations in (english, danish):
        assert {
            key.removeprefix("concept_heatPump.")
            for key in translations
            if key.startswith("concept_heatPump.")
        } == expected_heat_pump_concepts
    assert "options" not in load_translation("en")
    assert "options" not in load_translation("da")


@pytest.mark.parametrize("language", LANGUAGES)
def test_all_messages_and_placeholders_are_complete(language):
    """Every locale covers both existing namespaces without losing substitutions."""
    def leaves(value, prefix=()):
        if isinstance(value, dict):
            return {path: text for key, child in value.items() for path, text in leaves(child, (*prefix, key)).items()}
        return {prefix: value}

    for load in (load_translation, load_panel_translation):
        english = leaves(load("en"))
        localized = leaves(load(language))
        assert localized.keys() == english.keys()
        for key, text in localized.items():
            assert isinstance(text, str) and text.strip(), (language, key)
            placeholders = lambda value: {field for _, field, _, _ in Formatter().parse(value) if field}
            assert placeholders(text) == placeholders(english[key]), (language, key)
    assert load_translation(language)["config"]["step"]["user"]["title"] == "Your energy. Decides together."
    assert load_panel_translation(language)["title"] == "fluks"
    assert load_panel_translation(language)["tagline"] == "Your energy. Decides together."


@pytest.mark.parametrize("language", LANGUAGES)
async def test_ha_translation_lookup_and_panel_language_switch(hass, language):
    """The real HA translation loader selects config and Logbook messages per locale."""
    from homeassistant.helpers.translation import async_get_translations
    from custom_components.fluks.panel_api import _panel_translations

    native = load_translation(language)
    for category in ("config", "common"):
        strings = await async_get_translations(hass, language, category, {DOMAIN})
        if category == "config":
            assert strings["component.fluks.config.step.login.title"] == native["config"]["step"]["login"]["title"]
        else:
            assert strings["component.fluks.common.decision_action_executed"] == native["common"]["decision_action_executed"]
    hass.config.language = "en"
    assert await _panel_translations(hass, language) == load_panel_translation(language)
    hass.config.language = language
    assert await _panel_translations(hass) == load_panel_translation(language)


@pytest.mark.parametrize("language,locale", [("nb-NO", "nb"), ("sv-SE", "sv"), ("de-DE", "de"), ("da-DK", "da"), ("en-GB", "en"), ("fr", "en")])
def test_panel_locale_selection_and_fallback(language, locale):
    assert _load_panel_translations(language) == load_panel_translation(locale)


def test_all_literal_frontend_translation_keys_exist():
    keys = set(load_panel_translation("en"))
    for path in (Path("custom_components") / DOMAIN / "frontend").glob("*.js"):
        assert set(re.findall(r'_t\("([a-z_]+)"', path.read_text())) <= keys
