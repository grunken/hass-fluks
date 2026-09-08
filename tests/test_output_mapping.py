"""Tests for the documented Home Assistant output Mapping v1 shape."""

import pytest

from custom_components.fluks.output_mapping import (
    OutputMappingValidationError,
    apply_output_transforms,
    validate_output_configuration,
)


def test_requested_value_transforms_execute_in_persisted_order():
    assert apply_output_transforms(6500, [
        {"type": "powerToCurrent", "phases": 3, "voltage": 230},
        {"type": "nearest", "values": [6, 8, 10, 13, 16]},
        {"type": "valueMap", "values": [{"from": 10, "to": "ten"}]},
    ]) == "ten"


def test_reference_round_and_clamp_transforms_are_generic_and_ordered():
    transforms = [
        {"type": "difference", "reference": {"entityId": "climate.buffer", "attribute": "temperature"}},
        {"type": "round", "decimals": 0},
        {"type": "clamp", "min": -5, "max": 5},
    ]
    assert apply_output_transforms(
        38.2,
        transforms,
        reference_resolver=lambda reference: 35.2,
    ) == 3
    assert validate_output_configuration({
        "version": 1,
        "actions": [action({"kind": "requestedValue", "transforms": transforms})],
    })["actions"][0]["data"]["value"]["transforms"] == transforms


def test_missing_reference_fails_safely():
    with pytest.raises(OutputMappingValidationError):
        apply_output_transforms(
            38.2,
            [{"type": "difference", "reference": {"entityId": "climate.buffer"}}],
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
        {"type": "round", "decimals": -1},
        {"type": "clamp", "min": 2, "max": 1},
        {"type": "difference", "reference": {"entityId": "not-an-entity"}},
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
