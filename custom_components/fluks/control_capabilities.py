"""Normalize safe Home Assistant action capabilities for fluks Controls."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.service import async_get_all_descriptions

SUPPORTED_SELECTORS = {"boolean", "number", "select", "state", "text"}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []


def _target(description: dict[str, Any], action_domain: str) -> dict[str, Any] | None:
    target = description.get("target")
    if not isinstance(target, dict):
        return None
    entities = target.get("entity")
    entity_filters = entities if isinstance(entities, list) else [entities]
    if not entity_filters or any(not isinstance(item, dict) for item in entity_filters):
        return None
    domains = sorted({domain for item in entity_filters for domain in (_string_list(item.get("domain")) or [action_domain])})
    if not domains:
        return None
    result: dict[str, Any] = {"domains": domains}
    device_classes = sorted({device_class for item in entity_filters for device_class in _string_list(item.get("device_class"))})
    if device_classes:
        result["device_classes"] = device_classes
    feature_groups = [item.get("supported_features") for item in entity_filters if isinstance(item.get("supported_features"), list)]
    if feature_groups:
        result["supported_features"] = [candidate for group in feature_groups for candidate in group]
    return result


def _selector(field: dict[str, Any]) -> dict[str, Any] | None:
    selector = field.get("selector")
    if not isinstance(selector, dict) or len(selector) != 1:
        return None
    selector_type, config = next(iter(selector.items()))
    if selector_type not in SUPPORTED_SELECTORS or not isinstance(config, dict):
        return None
    # Only JSON-safe, deterministic selector details used by the editor.
    normalized = {
        key: value
        for key, value in config.items()
        if key in {"attribute", "min", "max", "step", "mode", "multiline", "options"}
        and isinstance(value, (str, int, float, bool, list))
    }
    if selector_type == "select" and "options" in normalized:
        options = normalized["options"]
        if not isinstance(options, list):
            return None
        values = []
        labels = {}
        for option in options:
            if isinstance(option, (str, int, float, bool)):
                values.append(option)
            elif (
                isinstance(option, dict)
                and isinstance(option.get("value"), (str, int, float, bool))
                and isinstance(option.get("label"), str)
            ):
                values.append(option["value"])
                labels[str(option["value"])] = option["label"]
            else:
                return None
        normalized["options"] = values
        if labels:
            normalized["option_labels"] = labels
    return {"type": selector_type, **normalized}


def _fields(description: dict[str, Any]) -> list[dict[str, Any]] | None:
    source = description.get("fields", {})
    if not isinstance(source, dict):
        return None
    result: list[dict[str, Any]] = []
    for field_id, field in source.items():
        if not isinstance(field_id, str) or not isinstance(field, dict):
            return None
        # Sections affect HA presentation only; flatten their actual service data fields.
        if isinstance(field.get("fields"), dict):
            nested = _fields({"fields": field["fields"]})
            if nested is None:
                return None
            result.extend(nested)
            continue
        selector = _selector(field)
        if selector is None:
            return None
        item: dict[str, Any] = {
            "id": field_id,
            "name": str(field.get("name") or field_id.replace("_", " ").capitalize()),
            "description": str(field.get("description") or ""),
            "required": bool(field.get("required", False)),
            "selector": selector,
        }
        if "default" in field and isinstance(field["default"], (str, int, float, bool)):
            item["default"] = field["default"]
        field_filter = field.get("filter")
        if isinstance(field_filter, dict):
            item["filter"] = field_filter
        result.append(item)
    return result


def _features_match(required: Any, supported: int) -> bool:
    """Match HA's OR list, whose nested lists represent AND requirements."""
    if not isinstance(required, list) or not required:
        return True
    for candidate in required:
        values = candidate if isinstance(candidate, list) else [candidate]
        if values and all(isinstance(value, int) and supported & value == value for value in values):
            return True
    return False


def _entity_matches(state: State, target: dict[str, Any]) -> bool:
    domain = state.entity_id.split(".", 1)[0]
    if domain not in target["domains"]:
        return False
    if target.get("device_classes") and state.attributes.get("device_class") not in target["device_classes"]:
        return False
    return _features_match(
        target.get("supported_features"), int(state.attributes.get("supported_features", 0))
    )


def _field_applies(field: dict[str, Any], state: State) -> bool:
    field_filter = field.get("filter")
    if not isinstance(field_filter, dict):
        return True
    if "supported_features" in field_filter:
        return _features_match(
            field_filter["supported_features"],
            int(state.attributes.get("supported_features", 0)),
        )
    attribute = field_filter.get("attribute")
    if isinstance(attribute, dict) and len(attribute) == 1:
        name, accepted = next(iter(attribute.items()))
    else:
        name, accepted = None, None
    if isinstance(name, str) and isinstance(accepted, list):
        actual = state.attributes.get(name)
        return bool(set(actual if isinstance(actual, list) else [actual]) & set(accepted))
    return False


def _live_constraints(state: State, field: dict[str, Any]) -> dict[str, Any]:
    selector_type = field["selector"]["type"]
    attrs = state.attributes
    result: dict[str, Any] = {}
    if selector_type == "select" and isinstance(attrs.get("options"), list):
        result["options"] = [
            item for item in attrs["options"] if isinstance(item, (str, int, float, bool))
        ]
    if selector_type == "number":
        domain = state.entity_id.split(".", 1)[0]
        names = (
            ("min_temp", "max_temp", "target_temp_step")
            if domain in {"climate", "water_heater"}
            else ("min", "max", "step")
        )
        for output, name in zip(("min", "max", "step"), names, strict=True):
            if isinstance(attrs.get(name), (int, float)):
                result[output] = attrs[name]
    return result


def _present_field(state: State, field: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve HA's entity-dependent selectors into deterministic scalar inputs."""
    presented = {key: value for key, value in field.items() if key != "filter"}
    selector_type = field["selector"]["type"]
    domain = state.entity_id.split(".", 1)[0]
    if selector_type == "text" and domain == "number" and any(
        isinstance(state.attributes.get(key), (int, float))
        for key in ("min", "max", "step")
    ):
        presented["selector"] = {"type": "number"}
    elif selector_type == "state":
        options = state.attributes.get("options")
        if not isinstance(options, list) or not options:
            return None
        presented["selector"] = {"type": "select"}
    constraints = _live_constraints(state, presented)
    if constraints:
        presented["constraints"] = constraints
    return presented


async def async_control_capabilities(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return metadata-driven actions that fluks can represent deterministically."""
    descriptions = await async_get_all_descriptions(hass)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    actions: list[dict[str, Any]] = []
    for domain, services in descriptions.items():
        if not isinstance(services, dict):
            continue
        for service, description in services.items():
            if not isinstance(description, dict):
                continue
            target = _target(description, domain)
            fields = _fields(description)
            if target is None or fields is None:
                continue
            entities = []
            for state in hass.states.async_all():
                if not _entity_matches(state, target):
                    continue
                applicable = []
                unsupported_required = False
                for field in fields:
                    if not _field_applies(field, state):
                        continue
                    presented = _present_field(state, field)
                    if presented is None:
                        unsupported_required = unsupported_required or field["required"]
                        continue
                    applicable.append(presented)
                if unsupported_required:
                    continue
                registry_entry = entity_registry.async_get(state.entity_id)
                device_entry = (
                    device_registry.async_get(registry_entry.device_id)
                    if registry_entry is not None and registry_entry.device_id
                    else None
                )
                metadata = " · ".join(
                    str(value)
                    for value in (
                        (
                            device_entry.name_by_user or device_entry.name
                            if device_entry is not None
                            else None
                        ),
                        registry_entry.platform if registry_entry is not None else None,
                        device_entry.manufacturer if device_entry is not None else None,
                        device_entry.model if device_entry is not None else None,
                    )
                    if value
                )
                entities.append(
                    {
                        "entity_id": state.entity_id,
                        "name": str(state.attributes.get("friendly_name") or state.name),
                        "metadata": metadata,
                        "fields": applicable,
                    }
                )
            if not entities:
                continue
            actions.append(
                {
                    "service": f"{domain}.{service}",
                    "name": str(description.get("name") or service.replace("_", " ").capitalize()),
                    "description": str(description.get("description") or ""),
                    "target": target,
                    "entities": sorted(entities, key=lambda item: item["name"].casefold()),
                }
            )
    return sorted(actions, key=lambda item: (item["name"].casefold(), item["service"]))


async def async_validate_control_configuration(
    hass: HomeAssistant, configuration: dict[str, Any]
) -> bool:
    """Validate one normalized output Mapping against current HA capabilities."""
    capabilities = {
        item["service"]: item for item in await async_control_capabilities(hass)
    }
    for action in configuration["actions"]:
        capability = capabilities.get(action["service"])
        if capability is None:
            return False
        entity = next(
            (
                item
                for item in capability["entities"]
                if item["entity_id"] == action["target"]["entityId"]
            ),
            None,
        )
        if entity is None:
            return False
        fields = {item["id"]: item for item in entity["fields"]}
        if any(key not in fields for key in action["data"]):
            return False
        if any(item["required"] and key not in action["data"] for key, item in fields.items()):
            return False
        for key, binding in action["data"].items():
            if binding["kind"] != "literal":
                continue
            value = binding["value"]
            field = fields[key]
            selector_type = field["selector"]["type"]
            constraints = {**field["selector"], **field.get("constraints", {})}
            if selector_type == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    return False
                if "min" in constraints and value < constraints["min"]:
                    return False
                if "max" in constraints and value > constraints["max"]:
                    return False
            elif selector_type == "boolean" and not isinstance(value, bool):
                return False
            elif selector_type in {"text", "select"} and not isinstance(value, str):
                return False
            if selector_type == "select" and constraints.get("options") and value not in constraints["options"]:
                return False
    return True
