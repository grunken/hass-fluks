"""Tests for deterministic Milestone 2 entity suggestions."""

from custom_components.fluks.matcher import (
    EntityCandidate,
    SUGGESTION_THRESHOLD,
    input_configuration,
    normalize_input_configuration,
    score_candidate,
)


def test_same_device_unit_datatype_and_classes_raise_confidence():
    """Strong HA metadata produces a substantially stronger score."""
    concept = {
        "concept": "battery.power",
        "datatype": "number",
        "unit": "W",
    }
    strong = EntityCandidate(
        "sensor.battery_power",
        "goodwe",
        "Battery Power",
        "-842",
        "W",
        "power",
        "measurement",
    )
    weak = EntityCandidate(
        "sensor.outdoor_temperature",
        "weather",
        "Outdoor temperature",
        "12",
        "°C",
        "temperature",
        "measurement",
    )

    assert score_candidate(concept, strong, "goodwe") >= 100
    assert score_candidate(concept, strong, "goodwe") > score_candidate(
        concept, weak, "goodwe"
    )


def test_soc_names_and_percentage_are_preferred():
    """SOC semantics, percentage units, and battery class are recognized."""
    concept = {
        "concept": "battery.soc",
        "datatype": "number",
        "unit": "%",
        "min": 0,
        "max": 100,
    }
    candidate = EntityCandidate(
        "sensor.goodwe_state_of_charge",
        "goodwe",
        "Battery SOC",
        "73",
        "%",
        "battery",
        "measurement",
    )
    assert score_candidate(concept, candidate, "goodwe") >= 100


def test_weak_same_device_number_stays_below_suggestion_threshold():
    """Device membership and numeric state alone are not enough to preselect."""
    candidate = EntityCandidate(
        "sensor.goodwe_unknown",
        "goodwe",
        "Unknown",
        "12",
        None,
        None,
        None,
    )
    concept = {"concept": "battery.power", "datatype": "number", "unit": "W"}
    assert score_candidate(concept, candidate, "goodwe") < SUGGESTION_THRESHOLD


async def test_standard_on_off_uses_documented_value_map(hass):
    """Obvious textual boolean states use the backend's valueMap shape."""
    hass.states.async_set("switch.water_heater", "on")
    configuration = input_configuration(
        hass,
        {
            "concept": "waterHeater.state",
            "datatype": "boolean",
            "cadence": "realtime",
            "usages": ["fact"],
        },
        "switch.water_heater",
    )
    assert configuration == {
        "version": 1,
        "entityId": "switch.water_heater",
        "transforms": [
            {"type": "valueMap", "values": {"on": True, "off": False}}
        ],
    }


async def test_input_configuration_preserves_optional_attribute(hass):
    """Attribute sources use the existing input Mapping shape and transforms."""
    hass.states.async_set(
        "climate.buffer",
        "heat",
        {"temperature": 50, "current_temperature": 56},
    )
    concept = {
        "concept": "heatPump.bufferTemperature",
        "datatype": "number",
        "unit": "°C",
        "cadence": "realtime",
    }

    assert normalize_input_configuration(
        hass,
        concept,
        {
            "version": 1,
            "entityId": "climate.buffer",
            "attribute": "current_temperature",
            "transforms": [{"type": "offset", "amount": -1}],
        },
    ) == {
        "version": 1,
        "entityId": "climate.buffer",
        "attribute": "current_temperature",
        "transforms": [{"type": "offset", "amount": -1}],
    }
    assert normalize_input_configuration(
        hass, concept, {"version": 1, "entityId": "climate.buffer"}
    ) == {"version": 1, "entityId": "climate.buffer"}
