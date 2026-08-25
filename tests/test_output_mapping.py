"""Tests for the documented Home Assistant output Mapping v1 shape."""

import pytest

from custom_components.fluks.output_mapping import (
    OutputMappingValidationError,
    validate_output_configuration,
)


def action(binding, *, service="number.set_value", parameter="value"):
    return {
        "type": "serviceCall",
        "service": service,
        "target": {"entityId": "number.goodwe_charge_target"},
        "data": {parameter: binding},
    }


def test_ordered_actions_and_transforms_round_trip_exactly():
    configuration = {
        "version": 1,
        "actions": [
            {
                "type": "serviceCall",
                "service": "select.select_option",
                "target": {"entityId": "select.goodwe_operation_mode"},
                "data": {"option": {"kind": "literal", "value": "eco_charge"}},
            },
            action(
                {
                    "kind": "requestedValue",
                    "transforms": [
                        {"type": "powerToCurrent", "phases": 3, "voltage": 230},
                        {"type": "nearest", "values": [5, 10, 15]},
                        {
                            "type": "valueMap",
                            "values": [
                                {"from": 5, "to": "low"},
                                {"from": 10, "to": "high"},
                            ],
                        },
                    ],
                }
            ),
        ],
    }
    assert validate_output_configuration(configuration) == configuration


def test_transform_configuration_does_not_change_the_canonical_control_value():
    canonical_control = {
        "concept": "electricVehicle.power",
        "datatype": "number",
        "unit": "W",
        "value": 6500,
    }
    configuration = {
        "version": 1,
        "actions": [
            action(
                {
                    "kind": "requestedValue",
                    "transforms": [
                        {"type": "powerToCurrent", "phases": 3, "voltage": 230}
                    ],
                }
            )
        ],
    }

    assert validate_output_configuration(configuration) == configuration
    assert canonical_control == {
        "concept": "electricVehicle.power",
        "datatype": "number",
        "unit": "W",
        "value": 6500,
    }


@pytest.mark.parametrize(
    "transform",
    [
        {"type": "powerToCurrent", "phases": 0, "voltage": 230},
        {"type": "powerToCurrent", "phases": 3, "voltage": 0},
        {"type": "nearest", "values": []},
        {
            "type": "valueMap",
            "values": [{"from": 1, "to": "on"}, {"from": 1, "to": "off"}],
        },
        {
            "type": "valueMap",
            "values": [{"from": 1, "to": "on"}, {"from": 2, "to": False}],
        },
        {"type": "unknown"},
    ],
)
def test_invalid_transform_parameters_are_rejected(transform):
    with pytest.raises(OutputMappingValidationError):
        validate_output_configuration(
            {
                "version": 1,
                "actions": [action({"kind": "requestedValue", "transforms": [transform]})],
            }
        )


def test_literal_values_remain_typed():
    for value in ("eco_charge", 50, True, None):
        configuration = {"version": 1, "actions": [action({"kind": "literal", "value": value})]}
        saved = validate_output_configuration(configuration)["actions"][0]["data"]["value"]["value"]
        assert saved == value
        assert type(saved) is type(value)


def test_target_only_service_action_round_trips():
    configuration = {
        "version": 1,
        "actions": [{
            "type": "serviceCall",
            "service": "switch.turn_on",
            "target": {"entityId": "switch.pump"},
            "data": {},
        }],
    }
    assert validate_output_configuration(configuration) == configuration


@pytest.mark.parametrize(
    "configuration",
    [
        {"version": 1, "actions": []},
        {"version": 2, "actions": [action({"kind": "requestedValue"})]},
        {"version": 1, "actions": [{"type": "serviceCall"}]},
        {"version": 1, "actions": [action({"source": "control"})]},
    ],
)
def test_incomplete_or_legacy_shapes_are_rejected(configuration):
    with pytest.raises(OutputMappingValidationError):
        validate_output_configuration(configuration)
