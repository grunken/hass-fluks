"""Tests for deterministic, evidence-bearing Home Assistant fact matching."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.fluks.matcher import (
    EntityCandidate,
    candidate_diagnostic,
    collect_candidates,
    input_configuration,
    match_entities,
    normalize_input_configuration,
    _tokens,
    unit_conversion,
)


def _concept(name, unit=None, **values):
    return {
        "concept": name,
        "datatype": "number",
        "unit": unit,
        "cadence": "realtime",
        "usages": ["fact"],
        **values,
    }


def _candidate(
    entity_id,
    *,
    value="10",
    unit="W",
    device_id="selected",
    attribute=None,
    friendly_name=None,
    original_name=None,
    device_class="power",
    state_class="measurement",
    domain="sensor",
):
    name = entity_id.split(".", 1)[-1].replace("_", " ")
    return EntityCandidate(
        entity_id=entity_id,
        attribute=attribute,
        device_id=device_id,
        config_entry_ids=frozenset(),
        domain=domain,
        friendly_name=friendly_name or name,
        original_name=original_name or name,
        value=value,
        unit=unit,
        device_class=device_class,
        state_class=state_class,
    )


def _registry_entry(entity_id, device_id, *, original_name=None):
    return SimpleNamespace(
        entity_id=entity_id,
        disabled=False,
        name=None,
        original_name=original_name or entity_id.split(".", 1)[-1],
        original_device_class=None,
        device_id=device_id,
        config_entry_id="integration",
        config_entry_ids=frozenset({"integration"}),
    )


def _match(hass, concepts, candidates, existing=None):
    with patch(
        "custom_components.fluks.matcher.collect_candidates",
        return_value=candidates,
    ):
        return match_entities(hass, concepts, "selected", existing)


def _apply(value, transforms):
    result = value
    for transform in transforms:
        if transform["type"] == "scale":
            result *= transform["factor"]
        elif transform["type"] == "offset":
            result += transform["amount"]
        elif transform["type"] == "invert":
            result *= -1
    return result


def test_exact_unit_match_is_auto_and_needs_no_conversion(hass):
    proposals = _match(
        hass,
        [_concept("battery.power", "W")],
        [_candidate("sensor.battery_power")],
    )

    proposal = proposals["battery.power"]
    assert proposal["classification"] == "auto"
    assert proposal["configuration"] == {
        "version": 1,
        "entityId": "sensor.battery_power",
        "unit": "W",
    }
    assert any(item["code"] == "exact_unit" for item in proposal["evidence"])


def test_supported_linear_unit_conversions_are_deterministic():
    assert unit_conversion("kW", "W") == (
        "convertible",
        [{"type": "scale", "factor": 1000}],
    )
    assert unit_conversion("W", "kW") == (
        "convertible",
        [{"type": "scale", "factor": 0.001}],
    )
    assert unit_conversion("kWh", "Wh") == (
        "convertible",
        [{"type": "scale", "factor": 1000}],
    )
    assert unit_conversion("Wh", "kWh") == (
        "convertible",
        [{"type": "scale", "factor": 0.001}],
    )
    assert unit_conversion("mW", "W") == (
        "convertible",
        [{"type": "scale", "factor": 0.001}],
    )
    assert unit_conversion("MW", "W") == (
        "convertible",
        [{"type": "scale", "factor": 1_000_000}],
    )


def test_kw_to_w_proposal_contains_complete_conversion_once(hass):
    hass.states.async_set(
        "sensor.battery_power",
        "1.25",
        {"unit_of_measurement": "kW", "device_class": "power"},
    )
    proposal = _match(
        hass,
        [_concept("battery.power", "W")],
        [_candidate("sensor.battery_power", value="1.25", unit="kW")],
    )["battery.power"]

    expected = {
        "version": 1,
        "entityId": "sensor.battery_power",
        "unit": "kW",
        "transforms": [{"type": "scale", "factor": 1000}],
    }
    assert proposal["classification"] == "auto"
    assert proposal["configuration"] == expected
    assert (
        normalize_input_configuration(hass, _concept("battery.power", "W"), expected)
        == expected
    )
    assert _apply(1.25, expected["transforms"]) == 1250

    hass.states.async_remove("sensor.battery_power")
    assert (
        normalize_input_configuration(hass, _concept("battery.power", "W"), expected)
        == expected
    )


def test_temperature_attribute_conversion_is_declared_and_applied_once(hass):
    hass.states.async_set(
        "climate.buffer",
        "heat",
        {"current_temperature": 68, "temperature_unit": "°F"},
    )
    configuration = input_configuration(
        hass,
        _concept("heatPump.temperature", "°C"),
        "climate.buffer",
        attribute="current_temperature",
    )

    assert configuration == {
        "version": 1,
        "entityId": "climate.buffer",
        "attribute": "current_temperature",
        "unit": "°F",
        "transforms": [
            {"type": "offset", "amount": -32},
            {"type": "scale", "factor": 5 / 9},
        ],
    }
    assert (
        normalize_input_configuration(
            hass, _concept("heatPump.temperature", "°C"), configuration
        )
        == configuration
    )
    assert _apply(68, configuration["transforms"]) == 20


def test_incompatible_units_and_device_classes_are_hard_rejected(hass):
    concepts = [_concept("battery.power", "W")]
    proposals = _match(
        hass,
        concepts,
        [
            _candidate(
                "sensor.battery_temperature", unit="°C", device_class="temperature"
            )
        ],
    )
    assert proposals["battery.power"]["classification"] == "unsupported"
    assert proposals["battery.power"]["configuration"] is None

    hass.states.async_set(
        "sensor.battery_temperature",
        "21",
        {"unit_of_measurement": "°C"},
    )
    with pytest.raises(ValueError, match="Incompatible input unit"):
        input_configuration(
            hass,
            concepts[0],
            "sensor.battery_temperature",
        )


def test_missing_unit_is_useful_but_never_auto_selected(hass):
    proposal = _match(
        hass,
        [_concept("battery.power", "W")],
        [_candidate("sensor.battery_power", unit=None)],
    )["battery.power"]

    assert proposal["classification"] == "suggest"
    assert proposal["configuration"] == {
        "version": 1,
        "entityId": "sensor.battery_power",
    }
    assert any(item["code"] == "missing_unit" for item in proposal["evidence"])


def test_attribute_candidate_can_beat_a_weaker_state_candidate(hass):
    proposal = _match(
        hass,
        [_concept("heatPump.temperature", "°C")],
        [
            _candidate(
                "sensor.controller_reading",
                value="20",
                unit=None,
                device_class=None,
                state_class=None,
            ),
            _candidate(
                "climate.heat_pump",
                attribute="current_temperature",
                value=21,
                unit="°C",
                device_class=None,
                state_class=None,
                domain="climate",
            ),
        ],
    )["heatPump.temperature"]

    assert proposal["classification"] == "auto"
    assert proposal["source"] == {
        "entityId": "climate.heat_pump",
        "attribute": "current_temperature",
    }
    assert proposal["alternatives"][0]["source"] == {
        "entityId": "sensor.controller_reading"
    }


def test_candidate_collection_only_includes_safe_scalar_attributes(hass):
    hass.states.async_set(
        "climate.buffer",
        "heat",
        {
            "friendly_name": "Buffer",
            "current_temperature": 45,
            "current_temperature_unit": "°C",
            "min_temp": 5,
            "options": ["heat", "off"],
            "nested": {"temperature": 45},
        },
    )
    entry = SimpleNamespace(
        entity_id="climate.buffer",
        disabled=False,
        name=None,
        original_name="Buffer controller",
        original_device_class=None,
        device_id="selected",
        config_entry_id="integration",
        config_entry_ids=frozenset({"integration"}),
    )
    with patch(
        "custom_components.fluks.matcher.er.async_get",
        return_value=SimpleNamespace(entities={entry.entity_id: entry}),
    ):
        candidates = collect_candidates(hass, "selected")

    attributes = {item.attribute for item in candidates}
    assert attributes == {None, "current_temperature"}
    attribute = next(item for item in candidates if item.attribute)
    assert attribute.unit == "°C"
    assert attribute.original_name == "Buffer controller"


def test_selected_heat_pump_device_is_the_candidate_boundary(hass):
    """An excellent Battery source cannot enter Heat Pump matching."""
    hass.states.async_set(
        "climate.naervarme",
        "heat",
        {
            "friendly_name": "Nærvarme",
            "water_temperature": 48,
            "outdoor_temperature": 7,
            "temperature_unit": "°C",
        },
    )
    hass.states.async_set(
        "sensor.heat_pump_power",
        "850",
        {
            "friendly_name": "Heat pump power",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )
    hass.states.async_set(
        "sensor.goodwe_battery_0_power",
        "1200",
        {
            "friendly_name": "Inverter Goodwe #1 Battery 0 Power",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )
    entries = {
        item.entity_id: item
        for item in [
            _registry_entry("climate.naervarme", "heat-pump"),
            _registry_entry("sensor.heat_pump_power", "heat-pump"),
            _registry_entry("sensor.goodwe_battery_0_power", "battery"),
        ]
    }
    registry = SimpleNamespace(entities=entries)
    concepts = [
        _concept("heatPump.power", "W"),
        _concept("heatPump.waterTemperature", "°C"),
    ]

    with patch("custom_components.fluks.matcher.er.async_get", return_value=registry):
        candidates = collect_candidates(hass, "heat-pump")
        proposals = match_entities(hass, concepts, "heat-pump")

    sources = {candidate.source_key for candidate in candidates}
    assert "sensor.goodwe_battery_0_power" not in sources
    assert "climate.naervarme#water_temperature" in sources
    assert "climate.naervarme#outdoor_temperature" in sources
    water_candidate = next(
        candidate
        for candidate in candidates
        if candidate.attribute == "water_temperature"
    )
    outdoor_candidate = next(
        candidate
        for candidate in candidates
        if candidate.attribute == "outdoor_temperature"
    )
    water_diagnostic = candidate_diagnostic(concepts[1], water_candidate, "heat-pump")
    outdoor_diagnostic = candidate_diagnostic(
        concepts[1], outdoor_candidate, "heat-pump"
    )
    assert water_diagnostic["accepted"] and water_diagnostic["score"] >= 14
    assert outdoor_diagnostic["accepted"]
    assert outdoor_diagnostic["score"] < water_diagnostic["score"]
    outdoor_semantic = next(
        item for item in outdoor_diagnostic["evidence"] if item["family"] == "semantics"
    )
    assert outdoor_semantic["code"] == "partial_token_coverage"
    assert outdoor_semantic["details"]["coverage"] == 0.5
    assert proposals["heatPump.power"]["source"] == {
        "entityId": "sensor.heat_pump_power"
    }
    assert all(
        alternative["source"]["entityId"] != "sensor.goodwe_battery_0_power"
        for alternative in proposals["heatPump.power"]["alternatives"]
    )
    assert proposals["heatPump.waterTemperature"]["source"] == {
        "entityId": "climate.naervarme",
        "attribute": "water_temperature",
    }
    assert proposals["heatPump.waterTemperature"]["classification"] == "auto"
    outdoor = next(
        alternative
        for alternative in proposals["heatPump.waterTemperature"]["alternatives"]
        if alternative["source"].get("attribute") == "outdoor_temperature"
    )
    assert outdoor["score"] < proposals["heatPump.waterTemperature"]["score"]


def test_outdoor_temperature_has_partial_coverage_for_tank_temperature(hass):
    concept = _concept("heatPump.waterTemperature", "°C")
    candidate = _candidate(
        "sensor.naervarme_outdoor_temperature",
        value="7",
        unit="°C",
        device_class="temperature",
        friendly_name="Nærvarme Outdoor temperature",
    )

    diagnostic = candidate_diagnostic(concept, candidate, "selected")
    proposal = _match(hass, [concept], [candidate])[concept["concept"]]

    assert diagnostic["accepted"]
    semantic = next(
        evidence for evidence in diagnostic["evidence"] if evidence["family"] == "semantics"
    )
    assert semantic["code"] == "partial_token_coverage"
    assert semantic["details"]["coverage"] == 0.5
    assert {evidence["code"] for evidence in diagnostic["evidence"]} >= {
        "numeric_value",
        "selected_device",
        "exact_unit",
        "exact_device_class",
        "partial_token_coverage",
    }
    assert proposal["classification"] == "suggest"
    assert proposal["configuration"] is not None


@pytest.mark.parametrize(
    "attribute",
    [
        "water_temperature",
        "Water Temperature",
        "waterTemperature",
        "water temp",
        "tank water temp",
    ],
)
def test_water_temperature_naming_variants_match(attribute, hass):
    concept = _concept("heatPump.waterTemperature", "°C")
    proposal = _match(
        hass,
        [concept],
        [
            _candidate(
                "climate.heat_pump",
                attribute=attribute,
                value=48,
                unit="°C",
                device_class=None,
                state_class=None,
                domain="climate",
            )
        ],
    )[concept["concept"]]

    assert proposal["classification"] == "auto"
    assert proposal["source"]["attribute"] == attribute
    semantic = next(
        evidence
        for evidence in proposal["evidence"]
        if evidence["family"] == "semantics"
    )
    assert semantic["code"] == "token_coverage"


def test_fact_token_normalization_covers_common_name_shapes():
    assert _tokens("tankTemperature") == ("tank", "temperature")
    assert _tokens("tank_temperature") == ("tank", "temperature")
    assert _tokens("tank.temperature") == ("tank", "temperature")
    assert _tokens("tank-temperature") == ("tank", "temperature")
    assert _tokens("state_of_charge") == ("soc",)
    assert _tokens("stateOfCharge") == ("soc",)


def test_water_heater_temperature_uses_domain_as_supporting_evidence(hass):
    proposal = _match(
        hass,
        [_concept("waterHeater.temperature", "°C")],
        [
            _candidate(
                "water_heater.naervarme_tank",
                attribute="current_temperature",
                value=48,
                unit="°C",
                device_class=None,
                state_class=None,
                domain="water_heater",
            )
        ],
    )["waterHeater.temperature"]

    assert proposal["classification"] == "auto"
    assert any(
        item["code"] == "canonical_device_domain" for item in proposal["evidence"]
    )


def test_partial_fact_token_coverage_cannot_beat_full_role_match(hass):
    proposals = _match(
        hass,
        [_concept("heatPump.tankTemperature", "°C")],
        [
            _candidate(
                "water_heater.naervarme_tank",
                attribute="current_temperature",
                value=48,
                unit="°C",
                device_class=None,
                state_class=None,
                domain="water_heater",
            ),
            _candidate(
                "sensor.outdoor_temperature",
                value=7,
                unit="°C",
                device_class="temperature",
                state_class="measurement",
                domain="sensor",
            ),
        ],
    )
    proposal = proposals["heatPump.tankTemperature"]

    assert proposal["source"] == {
        "entityId": "water_heater.naervarme_tank",
        "attribute": "current_temperature",
    }
    assert proposal["classification"] == "auto"
    outdoor = next(
        item for item in proposal["alternatives"] if item["source"]["entityId"] == "sensor.outdoor_temperature"
    )
    assert outdoor["score"] < proposal["score"]


def test_poor_power_name_still_matches_structured_signal(hass):
    proposal = _match(
        hass,
        [_concept("battery.power", "W")],
        [_candidate("sensor.meter_1", value=1.25, unit="kW", device_class="power")],
    )["battery.power"]

    assert proposal["classification"] == "auto"
    assert proposal["configuration"]["transforms"] == [
        {"type": "scale", "factor": 1000}
    ]


def test_energy_structured_evidence_includes_cumulative_state_class(hass):
    proposal = _match(
        hass,
        [_concept("battery.chargeEnergy", "kWh", cadence="interval")],
        [
            _candidate(
                "sensor.energy_total",
                value=14,
                unit="kWh",
                device_class="energy",
                state_class="total_increasing",
            )
        ],
    )["battery.chargeEnergy"]

    assert proposal["classification"] == "suggest"
    assert {item["code"] for item in proposal["evidence"]} >= {
        "exact_unit",
        "exact_device_class",
        "cumulative_energy",
    }


def test_levenshtein_similarity_supports_small_role_variations(hass):
    concept = _concept("heatPump.waterTemperature", "°C")
    candidate = _candidate(
        "climate.heat_pump",
        attribute="waterr_temperature",
        value=48,
        unit="°C",
        device_class=None,
        state_class=None,
        domain="climate",
    )
    diagnostic = candidate_diagnostic(concept, candidate, "selected")

    semantic = next(
        evidence
        for evidence in diagnostic["evidence"]
        if evidence["family"] == "semantics"
    )
    assert diagnostic["accepted"]
    assert semantic["code"] == "fuzzy_token_coverage"
    assert semantic["details"]["fuzzy_matches"][0]["target"] == "water"


def test_equally_strong_same_device_water_attributes_remain_unresolved(hass):
    concept = _concept("heatPump.waterTemperature", "°C")
    proposal = _match(
        hass,
        [concept],
        [
            _candidate(
                "climate.heat_pump",
                attribute=attribute,
                value=value,
                unit="°C",
                device_class=None,
                state_class=None,
                domain="climate",
            )
            for attribute, value in (
                ("water_temperature", 48),
                ("tank_water_temperature", 47),
            )
        ],
    )[concept["concept"]]

    assert proposal["runner_up_gap"] == 0
    assert proposal["classification"] == "unresolved"


def test_transient_unavailable_state_does_not_erase_strong_metadata(hass):
    proposal = _match(
        hass,
        [_concept("battery.power", "W")],
        [_candidate("sensor.battery_power", value="unavailable")],
    )["battery.power"]

    assert proposal["classification"] == "auto"
    assert any(item["code"] == "transient_unavailable" for item in proposal["evidence"])


def test_runner_up_tie_is_deterministic_but_never_confident(hass):
    proposal = _match(
        hass,
        [_concept("battery.power", "W")],
        [
            _candidate("sensor.battery_power_a", friendly_name="Battery power"),
            _candidate("sensor.battery_power_b", friendly_name="Battery power"),
        ],
    )["battery.power"]

    assert proposal["source"] == {"entityId": "sensor.battery_power_a"}
    assert proposal["runner_up_gap"] == 0
    assert proposal["classification"] == "unresolved"


def test_small_runner_up_margin_prevents_auto_selection(hass):
    proposal = _match(
        hass,
        [_concept("battery.power", "W")],
        [
            _candidate(
                "sensor.a_power_reading",
                friendly_name="Power",
                original_name="Reading",
            ),
            _candidate(
                "sensor.battery_power_meter",
                friendly_name="Battery power meter",
                state_class=None,
            ),
        ],
    )["battery.power"]

    assert proposal["score"] >= 14
    assert 0 < proposal["runner_up_gap"] < 4
    assert proposal["classification"] == "unresolved"


def test_friendly_original_and_entity_names_form_one_capped_family(hass):
    proposal = _match(
        hass,
        [_concept("battery.power", "W")],
        [
            _candidate(
                "sensor.battery_power",
                friendly_name="Battery power",
                original_name="Battery power",
            )
        ],
    )["battery.power"]
    semantic = [item for item in proposal["evidence"] if item["family"] == "semantics"]

    assert len(semantic) == 1
    assert semantic[0]["weight"] <= 6
    assert set(semantic[0]["details"]["sources"]) == {
        "object_id",
    }
    assert semantic[0]["details"]["repetition_bonus"] == 0


def test_repeated_target_tokens_add_one_capped_independent_bonus(hass):
    concept = _concept("heatPump.tankTemperature", "°C")
    repeated = _candidate(
        "water_heater.naervarme_tank",
        attribute="tank_accumulated",
        value=48,
        unit="°C",
        device_class=None,
        state_class=None,
        domain="water_heater",
        friendly_name="Tank accumulated",
        original_name="Tank accumulated",
    )
    single = _candidate(
        "water_heater.naervarme_tank",
        attribute="reading",
        value=48,
        unit="°C",
        device_class=None,
        state_class=None,
        domain="water_heater",
        friendly_name="Reading",
        original_name="Reading",
    )
    repeated_diagnostic = candidate_diagnostic(concept, repeated, "selected")
    single_diagnostic = candidate_diagnostic(concept, single, "selected")
    repeated_semantic = next(
        item for item in repeated_diagnostic["evidence"] if item["family"] == "semantics"
    )
    single_semantic = next(
        item for item in single_diagnostic["evidence"] if item["family"] == "semantics"
    )

    assert repeated_diagnostic["score"] == single_diagnostic["score"] + 1
    assert repeated_semantic["details"]["occurrences"]["tank"] == 2
    assert repeated_semantic["details"]["repetition_bonus"] == 1
    assert single_semantic["details"]["occurrences"]["tank"] == 1
    assert single_semantic["details"]["repetition_bonus"] == 0

    over_repeated = _candidate(
        "water_heater.naervarme_tank_tank",
        attribute="tank_accumulated",
        value=48,
        unit="°C",
        device_class=None,
        state_class=None,
        domain="water_heater",
        friendly_name="Tank accumulated",
        original_name="Tank accumulated",
    )
    over_repeated_semantic = next(
        item
        for item in candidate_diagnostic(concept, over_repeated, "selected")["evidence"]
        if item["family"] == "semantics"
    )
    assert over_repeated_semantic["details"]["occurrences"]["tank"] == 2
    assert over_repeated_semantic["details"]["repetition_bonus"] == 1


def test_selected_device_relationship_is_a_hard_boundary(hass):
    concept = [_concept("battery.power", "W")]
    selected = _match(hass, concept, [_candidate("sensor.battery_power")])[
        "battery.power"
    ]
    unrelated = _match(
        hass,
        concept,
        [_candidate("sensor.battery_power", device_id="other")],
    )["battery.power"]

    assert selected["classification"] == "auto"
    assert any(item["code"] == "selected_device" for item in selected["evidence"])
    assert unrelated["classification"] == "unsupported"
    assert unrelated["configuration"] is None
    assert (
        candidate_diagnostic(
            concept[0],
            _candidate("sensor.battery_power", device_id="other"),
            "selected",
        )["rejection_reason"]
        == "outside_selected_device"
    )


def test_unverified_signed_orientation_is_suggested_not_auto_selected(hass):
    proposal = _match(
        hass,
        [
            _concept(
                "battery.power",
                "W",
                signConvention="positive_consumes_negative_supplies",
            )
        ],
        [_candidate("sensor.battery_power")],
    )["battery.power"]

    assert proposal["classification"] == "suggest"
    assert any(item["code"] == "not_evaluated" for item in proposal["evidence"])
    assert "transforms" not in proposal["configuration"]


def test_global_assignment_prevents_incompatible_exact_source_reuse(hass):
    proposals = _match(
        hass,
        [_concept("battery.power", "W"), _concept("electricVehicle.power", "W")],
        [_candidate("sensor.power", friendly_name="Power")],
    )
    selected = [
        proposal["source"]
        for proposal in proposals.values()
        if proposal["source"] is not None
    ]

    assert selected == [{"entityId": "sensor.power"}]
    assert sum(item["classification"] == "auto" for item in proposals.values()) == 0


def test_existing_mapping_source_is_reserved_from_new_assignment(hass):
    proposals = _match(
        hass,
        [_concept("battery.chargeEnergy", "kWh", cadence="interval")],
        [
            _candidate(
                "sensor.battery_energy",
                value="14",
                unit="kWh",
                device_class="energy",
                state_class="total_increasing",
            ),
            _candidate(
                "sensor.battery_charge_energy",
                value="5",
                unit="kWh",
                device_class="energy",
                state_class="total_increasing",
            ),
        ],
        existing={
            "battery.energy": {
                "version": 1,
                "entityId": "sensor.battery_energy",
                "unit": "kWh",
            }
        },
    )

    assert proposals["battery.chargeEnergy"]["source"] == {
        "entityId": "sensor.battery_charge_energy"
    }


def test_distinct_attributes_of_one_entity_remain_legitimate_sources(hass):
    proposals = _match(
        hass,
        [
            _concept("heatPump.temperature", "°C"),
            _concept("heatPump.tankTemperature", "°C"),
        ],
        [
            _candidate(
                "climate.heat_pump",
                attribute="current_temperature",
                value=21,
                unit="°C",
                device_class=None,
                state_class=None,
                domain="climate",
            ),
            _candidate(
                "climate.heat_pump",
                attribute="tank_temperature",
                value=48,
                unit="°C",
                device_class=None,
                state_class=None,
                domain="climate",
            ),
        ],
    )

    assert proposals["heatPump.temperature"]["source"] == {
        "entityId": "climate.heat_pump",
        "attribute": "current_temperature",
    }
    assert proposals["heatPump.tankTemperature"]["source"] == {
        "entityId": "climate.heat_pump",
        "attribute": "tank_temperature",
    }


async def test_standard_on_off_uses_documented_value_map(hass):
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
        "transforms": [{"type": "valueMap", "values": {"on": True, "off": False}}],
    }


def test_native_boolean_fact_can_meet_auto_threshold(hass):
    concept = {
        "concept": "electricVehicle.connected",
        "datatype": "boolean",
        "cadence": "realtime",
        "usages": ["fact"],
    }
    proposal = _match(
        hass,
        [concept],
        [
            _candidate(
                "binary_sensor.vehicle_connected",
                value="on",
                unit=None,
                device_class=None,
                state_class=None,
                domain="binary_sensor",
            )
        ],
    )["electricVehicle.connected"]

    assert proposal["classification"] == "auto"
    assert proposal["configuration"]["transforms"] == [
        {"type": "valueMap", "values": {"on": True, "off": False}}
    ]


async def test_existing_version_one_mapping_can_preserve_legacy_unit_behavior(hass):
    hass.states.async_set(
        "climate.buffer",
        "heat",
        {"current_temperature": 68, "temperature_unit": "°F"},
    )
    legacy = {
        "version": 1,
        "entityId": "climate.buffer",
        "attribute": "current_temperature",
        "transforms": [{"type": "offset", "amount": -1}],
    }

    assert (
        normalize_input_configuration(
            hass,
            _concept("heatPump.temperature", "°C"),
            legacy,
            preserve_legacy_unit_behavior=True,
        )
        == legacy
    )
