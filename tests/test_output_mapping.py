"""Tests for the documented Home Assistant output Mapping v1 shape."""

import pytest

from custom_components.fluks.output_mapping import (
    OutputMappingValidationError,
    apply_output_transforms,
    potential_control_conflicts,
    validate_output_configuration,
)


def fixed_mapping(concept, value, *, entity="number.shared", mode=None):
    write = action({"kind": "literal", "value": value})
    write["target"]["entityId"] = entity
    return {
        "concept": concept, "direction": "output", "mode": mode,
        "configuration": {"version": 1, "actions": [write]},
    }


@pytest.mark.parametrize("left,right,conflicts", [
    ("general", "eco", True), ("general", "general", False),
    (0, 0.0, False), (False, False, False), (None, None, False),
    (False, 0, True), (True, 1, True), ("0", 0, True), (None, 0, True),
])
def test_fixed_write_conflicts_compare_typed_values(left, right, conflicts):
    mappings = [fixed_mapping("site.power", left), fixed_mapping("battery.power", right)]
    assert potential_control_conflicts(mappings) == (
        [{"concepts": ["battery.power", "site.power"], "entities": [
            {"entityId": "number.shared", "values": {"battery.power": right, "site.power": left}},
        ]}] if conflicts else []
    )
    if conflicts:
        values = potential_control_conflicts(mappings)[0]["entities"][0]["values"]
        assert type(values["site.power"]) is type(left)
        assert type(values["battery.power"]) is type(right)


def test_conflicts_are_generic_bidirectional_unique_and_do_not_mutate_mappings():
    from copy import deepcopy

    mappings = [
        fixed_mapping("heatPump.temperature", "heat"),
        fixed_mapping("waterHeater.temperature", "off"),
        fixed_mapping("electricVehicle.power", "on"),
        fixed_mapping("heatPump.temperature", "heat", mode="release"),
        fixed_mapping("heatPump.temperature", "heat", entity="number.shared_two"),
        fixed_mapping("waterHeater.temperature", "off", entity="number.shared_two"),
    ]
    mappings[0]["configuration"]["actions"] *= 2
    original = deepcopy(mappings)
    expected = [
        {"concepts": ["electricVehicle.power", "heatPump.temperature"], "entities": [
            {"entityId": "number.shared", "values": {"electricVehicle.power": "on", "heatPump.temperature": "heat"}},
        ]},
        {"concepts": ["electricVehicle.power", "waterHeater.temperature"], "entities": [
            {"entityId": "number.shared", "values": {"electricVehicle.power": "on", "waterHeater.temperature": "off"}},
        ]},
        {"concepts": ["heatPump.temperature", "waterHeater.temperature"], "entities": [
            {"entityId": entity, "values": {"heatPump.temperature": "heat", "waterHeater.temperature": "off"}}
            for entity in ("number.shared", "number.shared_two")
        ]},
    ]
    assert potential_control_conflicts(mappings) == expected
    assert potential_control_conflicts(list(reversed(mappings))) == expected
    assert mappings == original


def test_other_entities_and_behaviors_of_the_same_control_are_not_conflicts():
    assert potential_control_conflicts([
        fixed_mapping("site.power", "general"),
        fixed_mapping("site.power", "eco", mode="release"),
        fixed_mapping("battery.power", "off", entity="number.other"),
    ]) == []


def test_conflicts_include_literal_fields_in_mixed_actions():
    site = fixed_mapping("site.power", "general")
    battery = fixed_mapping("battery.power", "unused")
    battery["configuration"]["actions"][0]["data"] = {
        "value": {"kind": "requestedValue"},
        "option": {"kind": "literal", "value": "eco"},
    }
    assert potential_control_conflicts([site, battery]) == [
        {"concepts": ["battery.power", "site.power"], "entities": [
            {"entityId": "number.shared", "values": {"battery.power": "eco", "site.power": "general"}},
        ]},
    ]


def test_conflicts_preserve_distinct_value_combinations_across_behaviors():
    mappings = [
        fixed_mapping("site.power", "general"),
        fixed_mapping("battery.power", "eco_charge", mode="charge"),
        fixed_mapping("battery.power", "eco_discharge", mode="discharge"),
        fixed_mapping("battery.power", "eco_charge", mode="hold"),
        fixed_mapping("battery.power", "general", mode="release"),
    ]
    expected = [{"concepts": ["battery.power", "site.power"], "entities": [
        {"entityId": "number.shared", "values": {"battery.power": value, "site.power": "general"}}
        for value in ("eco_charge", "eco_discharge")
    ]}]
    assert potential_control_conflicts(mappings) == expected
    assert potential_control_conflicts(list(reversed(mappings))) == expected


def test_conflicting_value_records_keep_false_and_zero_distinct():
    result = potential_control_conflicts([
        fixed_mapping("site.power", False),
        fixed_mapping("site.power", 0, mode="release"),
        fixed_mapping("battery.power", "eco"),
    ])
    assert len(result) == 1
    assert len(result[0]["entities"]) == 2
    values = [entity["values"]["site.power"] for entity in result[0]["entities"]]
    assert values == [False, 0]
    assert [type(value) for value in values] == [bool, int]


def test_conflicts_ignore_inputs_requested_values_and_valueless_actions():
    mappings = [fixed_mapping("site.power", "general")]
    input_mapping = fixed_mapping("battery.power", "eco")
    input_mapping["direction"] = "input"
    mappings.append(input_mapping)
    for concept, binding in [
        ("battery.power", {"kind": "requestedValue"}),
        ("electricVehicle.power", {"kind": "requestedValue", "transforms": [
            {"type": "valueMap", "values": [{"from": 0, "to": "eco"}]},
        ]}),
    ]:
        mapped = fixed_mapping(concept, "unused")
        mapped["configuration"]["actions"][0]["data"]["value"] = binding
        mappings.append(mapped)
    valueless = fixed_mapping("heatPump.temperature", "unused")
    valueless["configuration"]["actions"][0]["data"] = {}
    mappings.append(valueless)
    mappings.append({"concept": "waterHeater.temperature", "direction": "output", "configuration": {}})
    assert potential_control_conflicts(mappings) == []


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
