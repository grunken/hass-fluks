"""Tests for shared Device identity and presentation behavior."""

from custom_components.fluks.device import (
    concept_label,
    device_display_name,
    device_identity,
    device_type_name,
    stable_device_id,
)


def test_stable_device_identity_is_deterministic_per_ha_device_type_pair():
    battery = stable_device_id("integration", "battery", "ha-goodwe")
    assert battery == stable_device_id("integration", "battery", "ha-goodwe")
    assert battery != stable_device_id("integration", "solar", "ha-goodwe")
    assert battery != stable_device_id("integration", "battery", "ha-pylontech")


def test_device_labels_always_include_localized_type_and_best_identity():
    translations = {
        "device_type_battery": "Batteri",
        "device_type_fallback": "Energienhed",
    }
    named = {"type": "battery", "properties": {"displayName": "GoodWe"}}
    metadata = {
        "type": "battery",
        "properties": {"vendor": "Pylontech", "model": "Force H2"},
    }
    sparse = {"type": "battery", "properties": {}}

    assert device_type_name(named, translations) == "Batteri"
    assert device_identity(named) == "GoodWe"
    assert device_display_name(named, translations) == "Batteri · GoodWe"
    assert device_display_name(metadata, translations) == "Batteri · Pylontech Force H2"
    assert device_display_name(sparse, translations) == "Batteri"


def test_concept_label_is_translation_backed_with_generic_future_fallback():
    translations = {"concept_battery.soc": "State of charge"}
    assert concept_label({"concept": "battery.soc"}, translations) == "State of charge"
    assert concept_label({"concept": "future.targetTemperature"}, translations) == "Target temperature"
