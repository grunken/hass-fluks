"""Tests for fluks milestone 1 translations."""

from __future__ import annotations

import ast
import json
from pathlib import Path

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
    english = load_translation("en")["panel"]
    danish = load_translation("da")["panel"]
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
    assert danish["concept_site.exportEnergy"] == "Eksporteret energi"
    assert english["concept_waterHeater.temperature"] == "Water temperature"
    assert danish["concept_waterHeater.temperature"] == "Vandtemperatur"
    assert "concept_waterHeater.targetTemperature" not in english
    assert "concept_waterHeater.targetTemperature" not in danish
    assert english["concept_spaceHeater.targetTemperature"] == "Target temperature"
    assert danish["concept_spaceHeater.targetTemperature"] == "Ønsket temperatur"
    assert english["concept_heatPump.temperature"] == "Temperature"
    assert danish["concept_heatPump.temperature"] == "Temperatur"
    assert danish["concept_heatPump.tankTemperature"] == "Beholdertemperatur"
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
