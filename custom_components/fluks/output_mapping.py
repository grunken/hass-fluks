"""Validation for documented Home Assistant output Mapping configuration v1."""

from __future__ import annotations

from copy import deepcopy
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

SERVICE_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
ENTITY_PATTERN = SERVICE_PATTERN
PRIMITIVE_TYPES = (str, int, float, bool)
NUMERIC_TYPES = (int, float)


class OutputMappingValidationError(ValueError):
    """The output Mapping configuration is not structurally valid."""


def apply_output_transforms(
    value: Any,
    transforms: list[dict[str, Any]],
    *,
    reference_resolver: Callable[[dict[str, Any]], Any] | None = None,
) -> Any:
    """Apply a validated requested-value transform sequence in order."""
    current = value
    for transform in transforms:
        transform_type = transform["type"]
        if transform_type == "invert":
            current = -current
        elif transform_type == "scale":
            current *= transform["factor"]
        elif transform_type == "offset":
            current += transform["amount"]
        elif transform_type == "difference":
            if reference_resolver is None:
                raise OutputMappingValidationError(
                    "difference requires a Home Assistant reference"
                )
            reference = reference_resolver(transform["reference"])
            if not _is_number(reference):
                raise OutputMappingValidationError(
                    "difference reference is not numeric"
                )
            current -= reference
        elif transform_type == "round":
            quantum = Decimal(1).scaleb(-transform["decimals"])
            rounded = Decimal(str(current)).quantize(quantum, rounding=ROUND_HALF_UP)
            current = int(rounded) if rounded == rounded.to_integral_value() else float(rounded)
        elif transform_type == "clamp":
            current = min(max(current, transform["min"]), transform["max"])
        elif transform_type == "powerToCurrent":
            current /= transform["phases"] * transform["voltage"]
        elif transform_type == "nearest":
            current = min(transform["values"], key=lambda item: (abs(item - current), item))
        elif transform_type == "valueMap":
            match = next(
                (item for item in transform["values"] if _datatype(item["from"]) == _datatype(current) and item["from"] == current),
                None,
            )
            if match is None:
                raise OutputMappingValidationError("valueMap has no matching input")
            current = match["to"]
    return current


def _is_number(value: Any) -> bool:
    return isinstance(value, NUMERIC_TYPES) and not isinstance(value, bool)


def _datatype(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if _is_number(value):
        return "number"
    return "string"


def _validate_transform(transform: Any) -> dict[str, Any]:
    if not isinstance(transform, dict) or not isinstance(transform.get("type"), str):
        raise OutputMappingValidationError("Invalid requested-value transform")
    transform_type = transform["type"]
    allowed_keys: set[str]
    if transform_type == "invert":
        allowed_keys = {"type"}
    elif transform_type == "scale":
        allowed_keys = {"type", "factor"}
        if not _is_number(transform.get("factor")):
            raise OutputMappingValidationError("scale requires a numeric factor")
    elif transform_type == "offset":
        allowed_keys = {"type", "amount"}
        if not _is_number(transform.get("amount")):
            raise OutputMappingValidationError("offset requires a numeric amount")
    elif transform_type == "difference":
        allowed_keys = {"type", "reference"}
        reference = transform.get("reference")
        if (
            not isinstance(reference, dict)
            or set(reference) - {"entityId", "attribute"}
            or not isinstance(reference.get("entityId"), str)
            or not ENTITY_PATTERN.fullmatch(reference["entityId"])
            or (
                "attribute" in reference
                and (
                    not isinstance(reference["attribute"], str)
                    or not reference["attribute"]
                )
            )
        ):
            raise OutputMappingValidationError(
                "difference requires a Home Assistant entity reference"
            )
    elif transform_type == "round":
        allowed_keys = {"type", "decimals"}
        decimals = transform.get("decimals")
        if not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 12:
            raise OutputMappingValidationError(
                "round requires a non-negative decimal count"
            )
    elif transform_type == "clamp":
        allowed_keys = {"type", "min", "max"}
        if (
            not _is_number(transform.get("min"))
            or not _is_number(transform.get("max"))
            or transform["min"] > transform["max"]
        ):
            raise OutputMappingValidationError(
                "clamp requires an ordered numeric range"
            )
    elif transform_type == "powerToCurrent":
        allowed_keys = {"type", "phases", "voltage"}
        phases = transform.get("phases")
        if not isinstance(phases, int) or isinstance(phases, bool) or phases < 1:
            raise OutputMappingValidationError("powerToCurrent requires positive phases")
        if not _is_number(transform.get("voltage")) or transform["voltage"] <= 0:
            raise OutputMappingValidationError("powerToCurrent requires positive voltage")
    elif transform_type == "nearest":
        allowed_keys = {"type", "values"}
        values = transform.get("values")
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 64
            or any(not _is_number(item) for item in values)
        ):
            raise OutputMappingValidationError("nearest requires numeric values")
    elif transform_type == "valueMap":
        allowed_keys = {"type", "values"}
        values = transform.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= 64:
            raise OutputMappingValidationError("valueMap requires typed entries")
        seen: set[tuple[str, Any]] = set()
        output_type: str | None = None
        for item in values:
            if not isinstance(item, dict) or set(item) != {"from", "to"}:
                raise OutputMappingValidationError("valueMap entries require from and to")
            source = item["from"]
            target = item["to"]
            if not isinstance(source, PRIMITIVE_TYPES) or target is not None and not isinstance(target, PRIMITIVE_TYPES):
                raise OutputMappingValidationError("valueMap values must be typed primitives")
            identity = (_datatype(source), source)
            if identity in seen:
                raise OutputMappingValidationError("valueMap inputs must be unique")
            seen.add(identity)
            current_type = _datatype(target)
            if output_type is None:
                output_type = current_type
            elif current_type is not output_type:
                raise OutputMappingValidationError("valueMap outputs must share one datatype")
    else:
        raise OutputMappingValidationError("Unsupported requested-value transform")
    if set(transform) != allowed_keys:
        raise OutputMappingValidationError("Unexpected transform fields")
    return deepcopy(transform)


def _validate_value_source(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict) or source.get("kind") not in {"literal", "requestedValue"}:
        raise OutputMappingValidationError("Invalid action value source")
    if source["kind"] == "literal":
        if set(source) != {"kind", "value"}:
            raise OutputMappingValidationError("Literal values require kind and value")
        value = source["value"]
        if value is not None and not isinstance(value, PRIMITIVE_TYPES):
            raise OutputMappingValidationError("Literal values must be typed primitives")
        return deepcopy(source)
    if set(source) - {"kind", "transforms"}:
        raise OutputMappingValidationError("Unexpected requested-value fields")
    transforms = source.get("transforms")
    result: dict[str, Any] = {"kind": "requestedValue"}
    if transforms is not None:
        if not isinstance(transforms, list) or not 1 <= len(transforms) <= 16:
            raise OutputMappingValidationError("Transforms must be a non-empty ordered list")
        result["transforms"] = [_validate_transform(item) for item in transforms]
    return result


def validate_output_configuration(configuration: Any) -> dict[str, Any]:
    """Return one normalized, machine-comparable output configuration."""
    if not isinstance(configuration, dict) or set(configuration) != {"version", "actions"}:
        raise OutputMappingValidationError("Output configuration requires version and actions")
    if configuration["version"] != 1:
        raise OutputMappingValidationError("Only output Mapping version 1 is supported")
    actions = configuration["actions"]
    if not isinstance(actions, list) or not 1 <= len(actions) <= 32:
        raise OutputMappingValidationError("Output Mapping requires 1 to 32 actions")
    normalized = []
    for action in actions:
        if not isinstance(action, dict) or set(action) - {"type", "service", "target", "data"}:
            raise OutputMappingValidationError("Invalid service action fields")
        if action.get("type") != "serviceCall" or not SERVICE_PATTERN.fullmatch(str(action.get("service", ""))):
            raise OutputMappingValidationError("Invalid Home Assistant service")
        target = action.get("target")
        if not isinstance(target, dict) or set(target) != {"entityId"} or not ENTITY_PATTERN.fullmatch(str(target.get("entityId", ""))):
            raise OutputMappingValidationError("Invalid Home Assistant target entity")
        data = action.get("data", {})
        if not isinstance(data, dict) or any(not isinstance(key, str) or not key for key in data):
            raise OutputMappingValidationError("Service action data must be an object")
        normalized.append(
            {
                "type": "serviceCall",
                "service": action["service"],
                "target": {"entityId": target["entityId"]},
                "data": {key: _validate_value_source(value) for key, value in data.items()},
            }
        )
    return {"version": 1, "actions": normalized}
