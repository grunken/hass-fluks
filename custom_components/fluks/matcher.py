"""Deterministic Home Assistant fact matching for canonical concepts."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

AUTO_SCORE_THRESHOLD = 14
SUGGESTION_SCORE_THRESHOLD = 8
AUTO_MARGIN = 4
AUTO_MIN_EVIDENCE_FAMILIES = 3

INVALID_STATES = {"", "unknown", "unavailable", "none", "null"}
SCALAR_TYPES = (str, int, float, bool)
IRRELEVANT_STATE_DOMAINS = {
    "automation",
    "button",
    "camera",
    "event",
    "image",
    "scene",
    "script",
    "update",
}
ATTRIBUTE_METADATA = {
    "attribution",
    "device_class",
    "entity_picture",
    "friendly_name",
    "icon",
    "last_reset",
    "state_class",
    "supported_features",
    "temperature_unit",
    "unit_of_measurement",
    "min",
    "max",
    "step",
    "options",
    "min_temp",
    "max_temp",
    "target_temp_step",
}

_LINEAR_UNITS: dict[str, tuple[str, float, str]] = {
    "mW": ("power", 0.001, "mW"),
    "W": ("power", 1, "W"),
    "kW": ("power", 1_000, "kW"),
    "MW": ("power", 1_000_000, "MW"),
    "mWh": ("energy", 0.001, "mWh"),
    "Wh": ("energy", 1, "Wh"),
    "kWh": ("energy", 1_000, "kWh"),
    "MWh": ("energy", 1_000_000, "MWh"),
    "GWh": ("energy", 1_000_000_000, "GWh"),
    "J": ("energy", 1 / 3_600, "J"),
    "kJ": ("energy", 1_000 / 3_600, "kJ"),
    "MJ": ("energy", 1_000_000 / 3_600, "MJ"),
    "GJ": ("energy", 1_000_000_000 / 3_600, "GJ"),
    "%": ("percentage", 1, "%"),
    "m": ("distance", 1, "m"),
    "km": ("distance", 1_000, "km"),
    "mi": ("distance", 1_609.344, "mi"),
}
_TEMPERATURE_UNITS = {
    "°c": "°C",
    "c": "°C",
    "℃": "°C",
    "°f": "°F",
    "f": "°F",
    "℉": "°F",
    "k": "K",
}
_KNOWN_DEVICE_CLASSES = {"battery", "distance", "energy", "power", "temperature"}
_TOKEN_ALIASES = {
    "temp": "temperature",
}


@dataclass(frozen=True)
class EntityCandidate:
    """One scalar Home Assistant entity-state or entity-attribute source."""

    entity_id: str
    attribute: str | None
    device_id: str | None
    config_entry_ids: frozenset[str]
    domain: str
    friendly_name: str
    original_name: str
    value: Any
    unit: str | None
    device_class: str | None
    state_class: str | None

    @property
    def source_key(self) -> str:
        return (
            self.entity_id
            if self.attribute is None
            else f"{self.entity_id}#{self.attribute}"
        )

    @property
    def source(self) -> dict[str, str]:
        result = {"entityId": self.entity_id}
        if self.attribute is not None:
            result["attribute"] = self.attribute
        return result


@dataclass(frozen=True)
class ScoredCandidate:
    """A compatible candidate plus the evidence that produced its score."""

    candidate: EntityCandidate
    configuration: dict[str, Any]
    score: int
    evidence: tuple[dict[str, Any], ...]
    unit_complete: bool

    @property
    def positive_families(self) -> set[str]:
        return {
            str(item["family"]) for item in self.evidence if int(item["weight"]) > 0
        }


@dataclass(frozen=True)
class PreparedMatches:
    """The existing compatible candidate set before semantic selection."""

    definitions: dict[str, dict[str, Any]]
    ranked: dict[str, list[ScoredCandidate]]


def _tokens(value: str) -> tuple[str, ...]:
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", value).lower()
    expanded = re.sub(r"\bstate[\s_-]+of[\s_-]+charge\b", "soc", expanded)
    return tuple(
        _TOKEN_ALIASES.get(token, token) for token in re.findall(r"[a-z0-9]+", expanded)
    )


def _words(value: str) -> set[str]:
    return set(_tokens(value))


def _levenshtein_similarity(left: str, right: str) -> float:
    """Return deterministic normalized edit similarity in the range 0..1."""
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return 1 - previous[-1] / max(len(left), len(right))


def _numeric(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _transient(value: Any) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().lower() in INVALID_STATES
    )


def _normalized_unit(unit: str | None) -> tuple[str, str, float] | None:
    if not isinstance(unit, str) or not unit.strip():
        return None
    normalized = unit.replace(" ", "")
    temperature = _TEMPERATURE_UNITS.get(normalized.lower())
    if temperature is not None:
        return "temperature", temperature, 1
    definition = _LINEAR_UNITS.get(normalized)
    if definition is None:
        return "other", normalized, 1
    family, factor, spelling = definition
    return family, spelling, factor


def _unit_family(unit: str | None) -> str | None:
    definition = _normalized_unit(unit)
    return definition[0] if definition is not None else None


def _clean_number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _apply_numeric_transforms(value: float, transforms: list[dict[str, Any]]) -> float:
    """Apply the generated numeric unit transforms for compatibility checks."""
    result = value
    for transform in transforms:
        if transform["type"] == "scale":
            result *= float(transform["factor"])
        elif transform["type"] == "offset":
            result += float(transform["amount"])
    return result


def _temperature_conversion(source: str, target: str) -> list[dict[str, Any]]:
    if source == target:
        return []
    conversions: dict[tuple[str, str], list[dict[str, Any]]] = {
        ("°F", "°C"): [
            {"type": "offset", "amount": -32},
            {"type": "scale", "factor": 5 / 9},
        ],
        ("°C", "°F"): [
            {"type": "scale", "factor": 9 / 5},
            {"type": "offset", "amount": 32},
        ],
        ("K", "°C"): [{"type": "offset", "amount": -273.15}],
        ("°C", "K"): [{"type": "offset", "amount": 273.15}],
        ("°F", "K"): [
            {"type": "offset", "amount": -32},
            {"type": "scale", "factor": 5 / 9},
            {"type": "offset", "amount": 273.15},
        ],
        ("K", "°F"): [
            {"type": "offset", "amount": -273.15},
            {"type": "scale", "factor": 9 / 5},
            {"type": "offset", "amount": 32},
        ],
    }
    return [dict(item) for item in conversions[(source, target)]]


def unit_conversion(
    source_unit: str | None, target_unit: str | None
) -> tuple[str, list[dict[str, Any]]]:
    """Classify and build one deterministic source-to-canonical conversion."""
    if target_unit is None:
        return "exact", []
    source = _normalized_unit(source_unit)
    target = _normalized_unit(target_unit)
    if source is None:
        return "unknown", []
    if target is None or source[0] != target[0]:
        return "incompatible", []
    if source[0] == "other":
        return ("exact", []) if source[1] == target[1] else ("incompatible", [])
    if source[1] == target[1]:
        return "exact", []
    if source[0] == "temperature":
        return "convertible", _temperature_conversion(source[1], target[1])
    factor = source[2] / target[2]
    return "convertible", [{"type": "scale", "factor": _clean_number(factor)}]


def _entry_config_ids(entry: Any) -> frozenset[str]:
    values: set[str] = set()
    value = getattr(entry, "config_entry_id", None)
    if isinstance(value, str):
        values.add(value)
    multiple = getattr(entry, "config_entry_ids", None)
    if isinstance(multiple, (set, frozenset, list, tuple)):
        values.update(item for item in multiple if isinstance(item, str))
    return frozenset(values)


def _attribute_unit(hass: HomeAssistant, state: State, attribute: str) -> str | None:
    attributes = state.attributes
    explicit = attributes.get(f"{attribute}_unit")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    words = _words(attribute)
    if not ({"temperature", "temp"} & words or attribute == "ambient"):
        return None
    declared = attributes.get("temperature_unit")
    if isinstance(declared, str) and declared.strip():
        return declared
    domain = state.entity_id.split(".", 1)[0]
    if domain in {"climate", "water_heater"}:
        return str(hass.config.units.temperature_unit)
    return None


def collect_candidates(
    hass: HomeAssistant, selected_device_id: str | Collection[str] | None
) -> list[EntityCandidate]:
    """Collect scalar sources owned by the explicitly selected HA Device."""
    selected_device_ids = (
        {selected_device_id}
        if isinstance(selected_device_id, str)
        else set(selected_device_id or ())
    )
    registry = er.async_get(hass)
    candidates: list[EntityCandidate] = []
    for entry in registry.entities.values():
        if entry.disabled or entry.device_id not in selected_device_ids:
            continue
        state: State | None = hass.states.get(entry.entity_id)
        attributes = state.attributes if state is not None else {}
        friendly_name = str(
            attributes.get("friendly_name") or entry.name or entry.entity_id
        )
        original_name = str(entry.original_name or entry.name or entry.entity_id)
        common = {
            "entity_id": entry.entity_id,
            "device_id": entry.device_id,
            "config_entry_ids": _entry_config_ids(entry),
            "domain": entry.entity_id.split(".", 1)[0],
            "friendly_name": friendly_name,
            "original_name": original_name,
        }
        candidates.append(
            EntityCandidate(
                **common,
                attribute=None,
                value=state.state if state is not None else None,
                unit=attributes.get("unit_of_measurement"),
                device_class=attributes.get("device_class")
                or entry.original_device_class,
                state_class=attributes.get("state_class"),
            )
        )
        if state is None:
            continue
        for attribute, value in attributes.items():
            if (
                attribute in ATTRIBUTE_METADATA
                or attribute.endswith("_unit")
                or not isinstance(value, SCALAR_TYPES)
            ):
                continue
            candidates.append(
                EntityCandidate(
                    **common,
                    attribute=str(attribute),
                    value=value,
                    unit=_attribute_unit(hass, state, str(attribute)),
                    device_class=None,
                    state_class=None,
                )
            )
    return candidates


def _evidence(family: str, code: str, weight: int, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"family": family, "code": code, "weight": weight}
    if details:
        item["details"] = details
    return item


def _datatype_evidence(
    concept: dict[str, Any], candidate: EntityCandidate
) -> dict[str, Any] | None:
    datatype = concept.get("datatype")
    value = candidate.value
    if datatype == "number":
        if _numeric(value):
            return _evidence("datatype", "numeric_value", 4)
        if _transient(value) and (
            candidate.unit is not None
            or candidate.device_class in _KNOWN_DEVICE_CLASSES
            or candidate.state_class is not None
            or candidate.domain in {"number", "input_number"}
        ):
            return _evidence("datatype", "numeric_metadata", 2)
        return None
    if datatype == "boolean":
        if isinstance(value, bool) or value in {"on", "off", "true", "false"}:
            return _evidence("datatype", "boolean_value", 4)
        if _transient(value) and candidate.domain in {
            "binary_sensor",
            "input_boolean",
            "switch",
        }:
            return _evidence("datatype", "boolean_domain", 2)
        return None
    if datatype == "string":
        if isinstance(value, str) and not _transient(value):
            return _evidence("datatype", "string_value", 4)
        if _transient(value) and candidate.domain in {"input_text", "select", "text"}:
            return _evidence("datatype", "string_domain", 2)
    return None


def _semantic_assessment(
    concept: dict[str, Any], candidate: EntityCandidate
) -> tuple[dict[str, Any] | None, str | None]:
    """Match the canonical fact tokens against one normalized source context."""
    fact = str(concept.get("concept", "")).split(".")[-1]
    target_tokens = tuple(dict.fromkeys(_tokens(fact)))
    sources = {
        "domain": _tokens(candidate.domain),
        "object_id": _tokens(candidate.entity_id.split(".", 1)[-1]),
        "friendly_name": _tokens(candidate.friendly_name),
        "original_name": _tokens(candidate.original_name),
    }
    if candidate.attribute is not None:
        sources["attribute"] = _tokens(candidate.attribute)
    seen_metadata: set[tuple[str, ...]] = set()
    for name in ("object_id", "friendly_name", "original_name"):
        tokens = sources[name]
        if tokens in seen_metadata:
            del sources[name]
        else:
            seen_metadata.add(tokens)
    combined = set().union(*sources.values()) if sources else set()
    occurrences = {
        target: min(
            2,
            sum(tokens.count(target) for tokens in sources.values()),
        )
        for target in target_tokens
    }
    exact = set(target_tokens) & combined
    used = set(exact)
    fuzzy_matches: list[tuple[str, str, float]] = []
    for target in target_tokens:
        if target in exact:
            continue
        best = max(
            (
                (_levenshtein_similarity(target, token), token)
                for token in combined - used
            ),
            default=(0.0, ""),
        )
        if best[0] >= 0.82:
            fuzzy_matches.append((target, best[1], best[0]))
            used.add(best[1])
    matched_count = len(exact) + len(fuzzy_matches)
    if not matched_count:
        return None, None
    coverage = matched_count / len(target_tokens) if target_tokens else 0.0
    repetition_bonus = (
        min(2, sum(max(0, count - 1) for count in occurrences.values()))
        if len(exact) == len(target_tokens)
        else 0
    )
    if len(exact) == len(target_tokens):
        weight = 4 + repetition_bonus
        code = "token_coverage"
    elif coverage == 1:
        weight = 2 + repetition_bonus
        code = "fuzzy_token_coverage"
    else:
        weight = -4 + repetition_bonus
        code = "partial_token_coverage"
    matched_tokens = exact | {target for target, _, _ in fuzzy_matches}
    source_names = sorted(
        name
        for name, words in sources.items()
        if set(words) & exact
        or any(source_token in words for _, source_token, _ in fuzzy_matches)
    )
    return (
        _evidence(
            "semantics",
            code,
            weight,
            target_tokens=list(target_tokens),
            matched_tokens=sorted(matched_tokens),
            coverage=round(coverage, 3),
            occurrences=occurrences,
            repetition_bonus=repetition_bonus,
            fuzzy_matches=[
                {"target": target, "source": source, "similarity": round(similarity, 3)}
                for target, source, similarity in fuzzy_matches
            ],
            sources=source_names,
        ),
        None,
    )


def _configuration_for_candidate(
    concept: dict[str, Any], candidate: EntityCandidate
) -> tuple[dict[str, Any], bool] | None:
    status, conversion = unit_conversion(candidate.unit, concept.get("unit"))
    if status == "incompatible":
        return None
    configuration: dict[str, Any] = {"version": 1, **candidate.source}
    if candidate.unit is not None and concept.get("unit") is not None:
        configuration["unit"] = candidate.unit
    transforms = list(conversion)
    if concept.get("datatype") == "boolean" and (
        candidate.value in {"on", "off", "true", "false"}
        or candidate.domain in {"binary_sensor", "input_boolean", "switch"}
    ):
        values = (
            {"true": True, "false": False}
            if candidate.value in {"true", "false"}
            and candidate.domain not in {"binary_sensor", "input_boolean", "switch"}
            else {"on": True, "off": False}
        )
        transforms.append({"type": "valueMap", "values": values})
    if transforms:
        configuration["transforms"] = transforms
    if concept.get("cadence") == "interval":
        configuration["source"] = {
            "kind": (
                "cumulative"
                if candidate.state_class in {"total", "total_increasing"}
                else "delta"
            )
        }
    return configuration, status != "unknown"


def _evaluate_candidate(
    concept: dict[str, Any],
    candidate: EntityCandidate,
    selected_device_id: str | None,
    candidate_device_ids: set[str] | None = None,
) -> tuple[ScoredCandidate | None, str | None, tuple[dict[str, Any], ...]]:
    allowed_device_ids = candidate_device_ids or (
        {selected_device_id} if selected_device_id is not None else set()
    )
    if candidate.device_id not in allowed_device_ids:
        return None, "outside_selected_device", ()
    if candidate.domain in IRRELEVANT_STATE_DOMAINS:
        return None, "irrelevant_domain", ()
    datatype = _datatype_evidence(concept, candidate)
    if datatype is None:
        return None, "incompatible_datatype", ()
    evidence: list[dict[str, Any]] = [datatype]
    configured = _configuration_for_candidate(concept, candidate)
    if configured is None:
        return None, "incompatible_unit", tuple(evidence)
    configuration, unit_complete = configured

    if candidate.attribute is None:
        if concept.get("datatype") == "boolean" and candidate.domain in {
            "binary_sensor",
            "input_boolean",
            "switch",
        }:
            evidence.append(_evidence("source_type", "native_boolean_entity", 2))
        elif concept.get("datatype") == "string" and candidate.domain in {
            "input_text",
            "select",
            "text",
        }:
            evidence.append(_evidence("source_type", "native_string_entity", 2))

    if candidate.device_id == selected_device_id:
        evidence.append(_evidence("relationship", "selected_device", 4))
    else:
        evidence.append(_evidence("relationship", "related_device", 2))

    canonical_device = str(concept.get("concept", "")).split(".", 1)[0]
    domain_overlap = sorted(
        _words(canonical_device) & _words(candidate.domain)
    )
    if domain_overlap:
        evidence.append(
            _evidence(
                "domain",
                "canonical_device_domain",
                2,
                canonical_device=canonical_device,
                domain=candidate.domain,
                overlap=domain_overlap,
            )
        )

    expected_unit = concept.get("unit")
    unit_status, conversion = unit_conversion(candidate.unit, expected_unit)
    if expected_unit is not None:
        if unit_status == "exact":
            evidence.append(
                _evidence(
                    "unit",
                    "exact_unit",
                    4,
                    source=candidate.unit,
                    canonical=expected_unit,
                )
            )
        elif unit_status == "convertible":
            evidence.append(
                _evidence(
                    "unit",
                    "deterministic_conversion",
                    4,
                    source=candidate.unit,
                    canonical=expected_unit,
                    transforms=conversion,
                )
            )
        else:
            evidence.append(_evidence("unit", "missing_unit", 0))

    expected_class = {
        "power": "power",
        "energy": "energy",
        "percentage": "battery",
        "temperature": "temperature",
        "distance": "distance",
    }.get(_unit_family(expected_unit))
    if concept.get("cadence") == "realtime" and candidate.state_class in {
        "total",
        "total_increasing",
    }:
        return None, "incompatible_state_class", tuple(evidence)
    actual_class = (candidate.device_class or "").lower()
    if expected_class and actual_class:
        if actual_class in _KNOWN_DEVICE_CLASSES and actual_class != expected_class:
            return None, "incompatible_device_class", tuple(evidence)
        if actual_class == expected_class:
            evidence.append(_evidence("device_class", "exact_device_class", 2))

    if (
        candidate.state_class in {"total", "total_increasing"}
        and concept.get("cadence") == "interval"
    ):
        evidence.append(_evidence("state_class", "cumulative_energy", 1))
    elif (
        candidate.state_class == "measurement" and concept.get("cadence") == "realtime"
    ):
        evidence.append(_evidence("state_class", "realtime_measurement", 1))

    if concept.get("datatype") == "number" and _numeric(candidate.value):
        number = _apply_numeric_transforms(float(candidate.value), conversion)
        minimum = concept.get("min")
        maximum = concept.get("max")
        if isinstance(minimum, (int, float)) and number < minimum:
            return None, "below_canonical_minimum", tuple(evidence)
        if isinstance(maximum, (int, float)) and number > maximum:
            return None, "above_canonical_maximum", tuple(evidence)
        if isinstance(minimum, (int, float)) or isinstance(maximum, (int, float)):
            evidence.append(_evidence("canonical_range", "value_in_range", 2))

    semantic, semantic_rejection = _semantic_assessment(concept, candidate)
    if semantic is not None:
        evidence.append(semantic)
    if semantic_rejection is not None:
        return None, semantic_rejection, tuple(evidence)
    if _transient(candidate.value):
        evidence.append(_evidence("availability", "transient_unavailable", 0))
    if concept.get("signConvention"):
        evidence.append(_evidence("orientation", "not_evaluated", 0))

    return (
        ScoredCandidate(
            candidate=candidate,
            configuration=configuration,
            score=sum(int(item["weight"]) for item in evidence),
            evidence=tuple(evidence),
            unit_complete=unit_complete,
        ),
        None,
        tuple(evidence),
    )


def score_candidate(
    concept: dict[str, Any], candidate: EntityCandidate, selected_device_id: str
) -> int | None:
    """Return the evidence score, or None when a hard gate rejects the source."""
    result, _, _ = _evaluate_candidate(concept, candidate, selected_device_id)
    return result.score if result is not None else None


def candidate_diagnostic(
    concept: dict[str, Any], candidate: EntityCandidate, selected_device_id: str
) -> dict[str, Any]:
    """Return structured onboarding diagnostics for one candidate assessment."""
    result, rejection, evidence = _evaluate_candidate(
        concept, candidate, selected_device_id
    )
    return {
        "source": candidate.source,
        "source_type": "attribute" if candidate.attribute is not None else "state",
        "owning_device": candidate.device_id,
        "accepted": result is not None,
        "rejection_reason": rejection,
        "evidence": [dict(item) for item in (result.evidence if result else evidence)],
        "score": result.score if result else None,
    }


def _maximum_assignment(
    concepts: list[str],
    ranked: dict[str, list[ScoredCandidate]],
    banned: tuple[str, str] | None = None,
) -> tuple[dict[str, ScoredCandidate], int]:
    """Find the deterministic maximum-score one-source-per-concept assignment."""
    sources = sorted(
        {
            item.candidate.source_key
            for concept in concepts
            for item in ranked[concept]
            if item.score > 0 and (concept, item.candidate.source_key) != banned
        }
    )
    if not concepts:
        return {}, 0
    columns = sources + [f"\0unmatched:{index}" for index in range(len(concepts))]
    by_concept = {
        concept: {item.candidate.source_key: item for item in ranked[concept]}
        for concept in concepts
    }
    forbidden = -1_000_000
    weights = [
        [
            (
                by_concept[concept][source].score
                if source in by_concept[concept]
                and by_concept[concept][source].score > 0
                and (concept, source) != banned
                else 0 if source.startswith("\0unmatched:") else forbidden
            )
            for source in columns
        ]
        for concept in concepts
    ]

    row_count, column_count = len(concepts), len(columns)
    u = [0] * (row_count + 1)
    v = [0] * (column_count + 1)
    p = [0] * (column_count + 1)
    way = [0] * (column_count + 1)
    for row in range(1, row_count + 1):
        p[0] = row
        column0 = 0
        minimum = [math.inf] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[column0] = True
            row0 = p[column0]
            delta = math.inf
            column1 = 0
            for column in range(1, column_count + 1):
                if used[column]:
                    continue
                current = -weights[row0 - 1][column - 1] - u[row0] - v[column]
                if current < minimum[column]:
                    minimum[column] = current
                    way[column] = column0
                if minimum[column] < delta:
                    delta = minimum[column]
                    column1 = column
            for column in range(column_count + 1):
                if used[column]:
                    u[p[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta
            column0 = column1
            if p[column0] == 0:
                break
        while True:
            column1 = way[column0]
            p[column0] = p[column1]
            column0 = column1
            if column0 == 0:
                break

    assignment: dict[str, ScoredCandidate] = {}
    total = 0
    for column in range(1, column_count + 1):
        if not p[column]:
            continue
        concept = concepts[p[column] - 1]
        candidate = by_concept[concept].get(columns[column - 1])
        if candidate is not None and candidate.score > 0:
            assignment[concept] = candidate
            total += candidate.score
    return assignment, total


def _candidate_payload(candidate: ScoredCandidate) -> dict[str, Any]:
    return {
        "source": candidate.candidate.source,
        "configuration": candidate.configuration,
        "score": candidate.score,
        "evidence": [dict(item) for item in candidate.evidence],
    }


def _configured_source_key(configuration: dict[str, Any]) -> str | None:
    entity_id = configuration.get("entityId")
    if not isinstance(entity_id, str) or not entity_id:
        return None
    attribute = configuration.get("attribute")
    return entity_id if not isinstance(attribute, str) else f"{entity_id}#{attribute}"


def prepare_matches(
    hass: HomeAssistant,
    concepts: list[dict[str, Any]],
    selected_device_id: str | None,
    existing_configurations: dict[str, dict[str, Any]] | None = None,
    additional_device_ids: Collection[str] | None = None,
) -> PreparedMatches:
    """Collect and filter candidates exactly as the existing matcher does."""
    candidate_device_ids = {
        device_id
        for device_id in (additional_device_ids or ())
        if isinstance(device_id, str) and device_id
    }
    if selected_device_id:
        candidate_device_ids.add(selected_device_id)
    candidates = collect_candidates(hass, candidate_device_ids)
    definitions = {
        str(concept["concept"]): concept
        for concept in concepts
        if "fact" in concept.get("usages", [])
    }
    reserved_sources = {
        source
        for configuration in (existing_configurations or {}).values()
        if (source := _configured_source_key(configuration)) is not None
    }
    ranked: dict[str, list[ScoredCandidate]] = {}
    for name, concept in definitions.items():
        values: list[ScoredCandidate] = []
        diagnostics: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate.source_key in reserved_sources:
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    diagnostics.append(
                        {
                            "source": candidate.source,
                            "source_type": (
                                "attribute"
                                if candidate.attribute is not None
                                else "state"
                            ),
                            "owning_device": candidate.device_id,
                            "accepted": False,
                            "rejection_reason": "reserved_by_existing_mapping",
                            "evidence": [],
                            "score": None,
                        }
                    )
                continue
            result, rejection, rejection_evidence = _evaluate_candidate(
                concept, candidate, selected_device_id, candidate_device_ids
            )
            if _LOGGER.isEnabledFor(logging.DEBUG):
                diagnostics.append(
                    {
                        "source": candidate.source,
                        "source_type": (
                            "attribute" if candidate.attribute is not None else "state"
                        ),
                        "owning_device": candidate.device_id,
                        "accepted": result is not None,
                        "rejection_reason": rejection,
                        "evidence": (
                            [dict(item) for item in result.evidence]
                            if result
                            else [dict(item) for item in rejection_evidence]
                        ),
                        "score": result.score if result else None,
                    }
                )
            if result is not None:
                values.append(result)
        if diagnostics:
            _LOGGER.debug(
                "fluks matcher candidates for %s on HA Device %s: %s",
                name,
                selected_device_id,
                diagnostics,
            )
        ranked[name] = sorted(
            values, key=lambda item: (-item.score, item.candidate.source_key)
        )

    return PreparedMatches(definitions=definitions, ranked=ranked)


def suggestion_candidates(
    prepared: PreparedMatches, concept: str
) -> list[dict[str, Any]]:
    """Return the prepared candidate set in the backend suggestion schema."""
    definition = prepared.definitions[concept]
    datatype = definition.get("datatype")
    expected_dimension = _unit_family(definition.get("unit"))
    device_class_dimensions = {
        "battery": "percentage",
        "distance": "distance",
        "energy": "energy",
        "power": "power",
        "temperature": "temperature",
    }
    result: list[dict[str, Any]] = []
    for item in prepared.ranked[concept]:
        candidate = item.candidate
        unit_dimension = _unit_family(candidate.unit)
        device_class_dimension = device_class_dimensions.get(
            (candidate.device_class or "").lower()
        )
        if expected_dimension is not None and expected_dimension not in {
            unit_dimension,
            device_class_dimension,
        }:
            continue
        payload = {
            "entityId": candidate.entity_id,
            "attribute": candidate.attribute,
            "deviceId": candidate.device_id,
            "name": candidate.friendly_name,
            "originalName": candidate.original_name,
            "sourceType": "attribute" if candidate.attribute is not None else "state",
            "datatype": datatype,
            "domain": candidate.domain,
            "deviceClass": candidate.device_class,
            "unit": candidate.unit,
            "stateClass": candidate.state_class,
            "currentValue": candidate.value,
        }
        result.append(
            {key: value for key, value in payload.items() if value is not None}
        )
    return result


def match_entities(
    hass: HomeAssistant,
    concepts: list[dict[str, Any]],
    selected_device_id: str | None,
    existing_configurations: dict[str, dict[str, Any]] | None = None,
    *,
    prepared: PreparedMatches | None = None,
    selected_sources: dict[str, dict[str, str]] | None = None,
    additional_device_ids: Collection[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return one evidence-bearing proposal for every fact concept."""
    matches = prepared or prepare_matches(
        hass,
        concepts,
        selected_device_id,
        existing_configurations,
        additional_device_ids,
    )
    definitions = matches.definitions
    ranked = matches.ranked
    concept_names = list(definitions)

    if selected_sources is None:
        assignment, total = _maximum_assignment(concept_names, ranked)
    else:
        assignment = {}
        for name, source in selected_sources.items():
            source_key = _configured_source_key(source)
            if name not in ranked or source_key is None:
                continue
            selected = next(
                (
                    item
                    for item in ranked[name]
                    if item.candidate.source_key == source_key
                ),
                None,
            )
            if selected is not None:
                assignment[name] = selected
        total = sum(item.score for item in assignment.values())
    proposals: dict[str, dict[str, Any]] = {}
    for name in concept_names:
        selected = assignment.get(name)
        if selected is None:
            proposals[name] = {
                "concept": name,
                "source": None,
                "configuration": None,
                "score": 0,
                "evidence": [],
                "runner_up_gap": None,
                "local_runner_up_gap": None,
                "classification": "unsupported" if not ranked[name] else "unresolved",
                "alternatives": [_candidate_payload(item) for item in ranked[name][:3]],
            }
            _LOGGER.debug(
                "fluks matcher proposal for %s on HA Device %s: classification=%s",
                name,
                selected_device_id,
                proposals[name]["classification"],
            )
            continue
        if selected_sources is None:
            _, alternative_total = _maximum_assignment(
                concept_names, ranked, banned=(name, selected.candidate.source_key)
            )
            margin = total - alternative_total
        else:
            margin = None
        local_alternatives = [
            item
            for item in ranked[name]
            if item.candidate.source_key != selected.candidate.source_key
        ]
        local_margin = (
            selected.score - local_alternatives[0].score
            if local_alternatives
            else None
        )
        relationship = any(
            item["family"] == "relationship" and item["code"] == "selected_device"
            for item in selected.evidence
        )
        semantic_evidence = next(
            (item for item in selected.evidence if item["family"] == "semantics"),
            None,
        )
        token_coverage = (
            float((semantic_evidence or {}).get("details", {}).get("coverage", 0))
            if semantic_evidence
            else 0.0
        )
        target_token_count = len(
            tuple(
                dict.fromkeys(
                    _tokens(name.rsplit(".", 1)[-1])
                )
            )
        )
        semantic_complete = target_token_count <= 1 or token_coverage >= 1
        exact_semantics = semantic_evidence is None or semantic_evidence["code"] == (
            "token_coverage"
        )
        orientation_complete = not definitions[name].get("signConvention")
        if selected_sources is not None:
            classification = "suggest"
        elif (
            selected.score >= AUTO_SCORE_THRESHOLD
            and len(selected.positive_families) >= AUTO_MIN_EVIDENCE_FAMILIES
            and margin >= AUTO_MARGIN
            and selected.unit_complete
            and relationship
            and semantic_complete
            and exact_semantics
            and (local_margin is None or local_margin >= AUTO_MARGIN)
            and orientation_complete
        ):
            classification = "auto"
        elif selected.score >= SUGGESTION_SCORE_THRESHOLD and margin >= AUTO_MARGIN:
            classification = "suggest"
        else:
            classification = "unresolved"
        alternatives = [
            item
            for item in ranked[name]
            if item.candidate.source_key != selected.candidate.source_key
        ]
        proposals[name] = {
            "concept": name,
            **_candidate_payload(selected),
            "runner_up_gap": margin,
            "local_runner_up_gap": local_margin,
            "classification": classification,
            "alternatives": [_candidate_payload(item) for item in alternatives[:3]],
        }
        _LOGGER.debug(
            "fluks matcher proposal for %s on HA Device %s: source=%s score=%s "
            "runner_up_gap=%s classification=%s evidence=%s",
            name,
            selected_device_id,
            selected.candidate.source,
            selected.score,
            margin,
            classification,
            selected.evidence,
        )
    return proposals


def _normalize_transforms(submitted: Any) -> list[dict[str, Any]] | None:
    if submitted is None:
        return None
    if not isinstance(submitted, list) or not 1 <= len(submitted) <= 16:
        raise ValueError("Invalid input transforms")
    normalized: list[dict[str, Any]] = []
    for transform in submitted:
        if not isinstance(transform, dict):
            raise ValueError("Invalid input transform")  # noqa: TRY004
        kind = transform.get("type")
        if kind == "invert" and set(transform) == {"type"}:
            normalized.append({"type": "invert"})
        elif kind in {"scale", "offset"}:
            parameter = "factor" if kind == "scale" else "amount"
            value = transform.get(parameter)
            if (
                set(transform) != {"type", parameter}
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("Invalid numeric input transform")
            normalized.append({"type": kind, parameter: value})
        elif kind == "valueMap":
            values = transform.get("values")
            if (
                set(transform) != {"type", "values"}
                or not isinstance(values, dict)
                or not values
            ):
                raise ValueError("Invalid input value map")
            normalized.append({"type": "valueMap", "values": dict(values)})
        else:
            raise ValueError("Unsupported input transform")
    return normalized


def _source_details(
    hass: HomeAssistant, entity_id: str, attribute: str | None
) -> tuple[Any, str | None, str | None, str]:
    state = hass.states.get(entity_id)
    domain = entity_id.split(".", 1)[0]
    if state is None:
        return None, None, None, domain
    if attribute is None:
        return (
            state.state,
            state.attributes.get("unit_of_measurement"),
            state.attributes.get("state_class"),
            domain,
        )
    return (
        state.attributes.get(attribute),
        _attribute_unit(hass, state, attribute),
        None,
        domain,
    )


def input_configuration(
    hass: HomeAssistant,
    concept: dict[str, Any],
    entity_id: str,
    transforms: list[dict[str, Any]] | None = None,
    attribute: str | None = None,
    *,
    preserve_legacy_unit_behavior: bool = False,
    declared_source_unit: str | None = None,
) -> dict[str, Any]:
    """Build a complete documented Home Assistant input configuration."""
    value, source_unit, state_class, domain = _source_details(
        hass, entity_id, attribute
    )
    source_unit = source_unit or declared_source_unit
    configuration: dict[str, Any] = {"version": 1, "entityId": entity_id}
    if attribute is not None:
        configuration["attribute"] = attribute

    normalized = list(transforms or [])
    if not preserve_legacy_unit_behavior and concept.get("unit") is not None:
        status, generated = unit_conversion(source_unit, concept.get("unit"))
        if status == "incompatible":
            raise ValueError("Incompatible input unit")
        if source_unit is not None:
            configuration["unit"] = source_unit
        if generated and normalized[: len(generated)] != generated:
            normalized = generated + normalized

    if (
        concept.get("datatype") == "boolean"
        and not normalized
        and (
            value in {"on", "off", "true", "false"}
            or domain in {"binary_sensor", "input_boolean", "switch"}
        )
    ):
        values = (
            {"true": True, "false": False}
            if value in {"true", "false"}
            and domain not in {"binary_sensor", "input_boolean", "switch"}
            else {"on": True, "off": False}
        )
        normalized = [{"type": "valueMap", "values": values}]
    if normalized:
        configuration["transforms"] = normalized
    if concept.get("cadence") == "interval":
        configuration["source"] = {
            "kind": (
                "cumulative"
                if state_class in {"total", "total_increasing"}
                else "delta"
            )
        }
    return configuration


def normalize_input_configuration(
    hass: HomeAssistant,
    concept: dict[str, Any],
    submitted: Any,
    *,
    preserve_legacy_unit_behavior: bool = False,
) -> dict[str, Any]:
    """Validate editable input configuration without changing its v1 contract."""
    if not isinstance(submitted, dict) or submitted.get("version", 1) != 1:
        raise ValueError("Invalid input Mapping")
    if set(submitted) - {
        "version",
        "entityId",
        "attribute",
        "unit",
        "transforms",
        "source",
    }:
        raise ValueError("Unexpected input Mapping field")
    entity_id = submitted.get("entityId")
    if not isinstance(entity_id, str) or "." not in entity_id:
        raise ValueError("Invalid input entity")
    attribute = submitted.get("attribute")
    if attribute is not None and (
        not isinstance(attribute, str) or not attribute.strip()
    ):
        raise ValueError("Invalid input attribute")
    declared_unit = submitted.get("unit")
    if declared_unit is not None and (
        not isinstance(declared_unit, str) or not declared_unit.strip()
    ):
        raise ValueError("Invalid input unit")
    transforms = _normalize_transforms(submitted.get("transforms"))
    configuration = input_configuration(
        hass,
        concept,
        entity_id,
        transforms,
        attribute,
        preserve_legacy_unit_behavior=preserve_legacy_unit_behavior,
        declared_source_unit=declared_unit,
    )
    actual_unit = _source_details(hass, entity_id, attribute)[1]
    if (
        declared_unit is not None
        and actual_unit is not None
        and _normalized_unit(declared_unit) != _normalized_unit(actual_unit)
    ):
        raise ValueError("Input unit no longer matches Home Assistant")
    return configuration
