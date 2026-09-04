"""Deterministic Home Assistant entity suggestions for canonical concepts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

SUGGESTION_THRESHOLD = 90


@dataclass(frozen=True)
class EntityCandidate:
    """The Home Assistant metadata used for deterministic matching."""

    entity_id: str
    device_id: str | None
    name: str
    state: str | None
    unit: str | None
    device_class: str | None
    state_class: str | None


def _words(value: str) -> set[str]:
    """Split identifiers and names into comparable lowercase words."""
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    return set(re.findall(r"[a-z0-9]+", expanded.lower()))


def _numeric(value: str | None) -> bool:
    if value is None:
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _unit_family(unit: str | None) -> str | None:
    if unit is None:
        return None
    normalized = unit.replace(" ", "").lower()
    if normalized in {"w", "kw", "mw"}:
        return "power"
    if normalized in {"wh", "kwh", "mwh"}:
        return "energy"
    if normalized in {"%"}:
        return "percentage"
    if normalized in {"°c", "c", "°f", "f"}:
        return "temperature"
    if normalized in {"m", "km", "mi"}:
        return "distance"
    return normalized


def score_candidate(
    concept: dict[str, Any], candidate: EntityCandidate, selected_device_id: str
) -> int:
    """Score one candidate using backend metadata and HA metadata."""
    score = 60 if candidate.device_id == selected_device_id else 0
    datatype = concept.get("datatype")
    state = candidate.state
    if datatype == "number":
        score += 20 if _numeric(state) else -60
    elif datatype == "boolean":
        score += 20 if state in {"on", "off", "true", "false"} else -20
    elif datatype == "string" and state is not None:
        score += 10

    expected_unit = _unit_family(concept.get("unit"))
    actual_unit = _unit_family(candidate.unit)
    if expected_unit and actual_unit:
        score += 25 if expected_unit == actual_unit else -35

    suffix = str(concept.get("concept", "")).split(".")[-1]
    semantic_words = _words(suffix)
    name_words = _words(f"{candidate.entity_id} {candidate.name}")
    score += 12 * len(semantic_words & name_words)

    aliases = {
        "soc": {"soc", "charge", "level", "percentage", "battery"},
        "power": {"power", "watt"},
        "energy": {"energy", "total"},
        "chargeEnergy": {"charge", "charged", "energy", "total"},
        "dischargeEnergy": {"discharge", "discharged", "energy", "total"},
        "targetTemperature": {"target", "setpoint", "temperature"},
        "waterTargetTemperature": {"water", "target", "setpoint", "temperature"},
        "temperature": {"temperature", "temp"},
        "connected": {"connected", "plugged"},
        "state": {"state", "status", "running"},
    }
    score += 5 * len(aliases.get(suffix, set()) & name_words)

    device_class = (candidate.device_class or "").lower()
    expected_class = {
        "power": "power",
        "energy": "energy",
        "percentage": "battery",
        "temperature": "temperature",
        "distance": "distance",
    }.get(expected_unit)
    if expected_class and device_class:
        score += 15 if device_class == expected_class else -10

    if expected_unit == "energy" and candidate.state_class in {
        "total",
        "total_increasing",
    }:
        score += 10
    return score


def collect_candidates(hass: HomeAssistant) -> list[EntityCandidate]:
    """Collect registry and current-state metadata without exposing it remotely."""
    registry = er.async_get(hass)
    candidates: list[EntityCandidate] = []
    for entry in registry.entities.values():
        if entry.disabled:
            continue
        state: State | None = hass.states.get(entry.entity_id)
        attributes = state.attributes if state is not None else {}
        candidates.append(
            EntityCandidate(
                entity_id=entry.entity_id,
                device_id=entry.device_id,
                name=str(
                    attributes.get("friendly_name")
                    or entry.name
                    or entry.original_name
                    or entry.entity_id
                ),
                state=state.state if state is not None else None,
                unit=attributes.get("unit_of_measurement"),
                device_class=(
                    attributes.get("device_class") or entry.original_device_class
                ),
                state_class=attributes.get("state_class"),
            )
        )
    return candidates


def suggest_entities(
    hass: HomeAssistant,
    concepts: list[dict[str, Any]],
    selected_device_id: str,
) -> dict[str, str]:
    """Suggest only high-confidence entity matches for fact concepts."""
    candidates = collect_candidates(hass)
    suggestions: dict[str, str] = {}
    for concept in concepts:
        if "fact" not in concept.get("usages", []):
            continue
        ranked = sorted(
            (
                (score_candidate(concept, candidate, selected_device_id), candidate)
                for candidate in candidates
            ),
            key=lambda item: (-item[0], item[1].entity_id),
        )
        if ranked and ranked[0][0] >= SUGGESTION_THRESHOLD:
            suggestions[str(concept["concept"])] = ranked[0][1].entity_id
    return suggestions


def input_configuration(
    hass: HomeAssistant,
    concept: dict[str, Any],
    entity_id: str,
    transforms: list[dict[str, Any]] | None = None,
    attribute: str | None = None,
) -> dict[str, Any]:
    """Build exactly the documented Home Assistant input configuration."""
    configuration: dict[str, Any] = {"version": 1, "entityId": entity_id}
    if attribute is not None:
        configuration["attribute"] = attribute
    state = hass.states.get(entity_id)
    source_value = (
        state.attributes.get(attribute)
        if state is not None and attribute is not None
        else state.state if state is not None else None
    )
    if concept.get("datatype") == "boolean" and (
        isinstance(source_value, str) and source_value in {"on", "off"}
        or entity_id.split(".", 1)[0] in {"binary_sensor", "switch", "input_boolean"}
    ):
        configuration["transforms"] = [
            {"type": "valueMap", "values": {"on": True, "off": False}}
        ]
    if transforms is not None:
        configuration["transforms"] = transforms
    if concept.get("cadence") == "interval":
        state_class = state.attributes.get("state_class") if state else None
        configuration["source"] = {
            "kind": (
                "cumulative"
                if state_class in {"total", "total_increasing"}
                else "delta"
            )
        }
    return configuration


def normalize_input_configuration(
    hass: HomeAssistant, concept: dict[str, Any], submitted: Any
) -> dict[str, Any]:
    """Validate editable input configuration without changing its v1 contract."""
    if not isinstance(submitted, dict) or submitted.get("version", 1) != 1:
        raise ValueError("Invalid input Mapping")
    entity_id = submitted.get("entityId")
    if not isinstance(entity_id, str) or "." not in entity_id:
        raise ValueError("Invalid input entity")
    attribute = submitted.get("attribute")
    if attribute is not None and (
        not isinstance(attribute, str) or not attribute.strip()
    ):
        raise ValueError("Invalid input attribute")
    transforms = submitted.get("transforms")
    if transforms is not None:
        if not isinstance(transforms, list) or not 1 <= len(transforms) <= 16:
            raise ValueError("Invalid input transforms")
        normalized: list[dict[str, Any]] = []
        for transform in transforms:
            if not isinstance(transform, dict):
                raise ValueError("Invalid input transform")
            kind = transform.get("type")
            if kind == "invert" and set(transform) == {"type"}:
                normalized.append({"type": "invert"})
            elif kind in {"scale", "offset"}:
                parameter = "factor" if kind == "scale" else "amount"
                value = transform.get(parameter)
                if set(transform) != {"type", parameter} or isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("Invalid numeric input transform")
                normalized.append({"type": kind, parameter: value})
            elif kind == "valueMap":
                values = transform.get("values")
                if set(transform) != {"type", "values"} or not isinstance(values, dict) or not values:
                    raise ValueError("Invalid input value map")
                normalized.append({"type": "valueMap", "values": dict(values)})
            else:
                raise ValueError("Unsupported input transform")
        transforms = normalized
    return input_configuration(hass, concept, entity_id, transforms, attribute)
