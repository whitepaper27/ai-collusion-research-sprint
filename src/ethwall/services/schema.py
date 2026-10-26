"""A small stdlib validator for the JSON-schema subset used by contract.tool_specs(arm).

Supported keywords: type (name or list), enum, required, additionalProperties (False),
properties, items, minItems, maxItems, minimum, maximum. Booleans are never integers or
numbers; non-finite floats are rejected; an integral float (3.0) counts as an integer.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any

from ethwall.contract import ARMS, ToolName, tool_specs


def _is_type(value: Any, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and math.isfinite(value) and value.is_integer()
    if name == "number":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and math.isfinite(value)
    if name == "array":
        return isinstance(value, (list, tuple))
    if name == "object":
        return isinstance(value, Mapping)
    if name == "null":
        return value is None
    raise ValueError(f"unsupported schema type {name!r}")


def validate(schema: Mapping[str, Any], value: Any, path: str = "$") -> str | None:
    """Return the first violation as a readable message, or None if `value` is valid."""
    types = schema.get("type")
    if types is not None:
        names = [types] if isinstance(types, str) else list(types)
        if not any(_is_type(value, n) for n in names):
            return f"{path}: expected {' or '.join(names)}"
    if "enum" in schema and not any(
        value == option and type(value) is not bool for option in schema["enum"]
    ):
        return f"{path}: not an allowed value"
    if isinstance(value, Mapping):
        props: Mapping[str, Any] = schema.get("properties", {})
        for key in schema.get("required", ()):
            if key not in value:
                return f"{path}: missing required field {key!r}"
        if schema.get("additionalProperties") is False:
            extra = sorted(str(k) for k in value if k not in props)
            if extra:
                return f"{path}: unexpected field(s) {extra}"
        for key, sub in props.items():
            if key in value:
                err = validate(sub, value[key], f"{path}.{key}")
                if err:
                    return err
    if isinstance(value, (list, tuple)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return f"{path}: at least {schema['minItems']} item(s) required"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"{path}: at most {schema['maxItems']} item(s) allowed"
        if "items" in schema:
            for i, item in enumerate(value):
                err = validate(schema["items"], item, f"{path}[{i}]")
                if err:
                    return err
    if _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path}: below minimum {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path}: above maximum {schema['maximum']}"
    return None


def _service_schemas(arm: str = "core") -> dict[ToolName, Mapping[str, Any]]:
    """Tool schemas as the services check them, for one arm (`contract.tool_specs(arm)`).

    Identical to the arm's tool specs except that `place_order.target_position` range limits
    are left to the order service, so an out-of-range target is reported as ORD_LIMIT
    (R-SVC-06) rather than SCHEMA_INVALID. Its integer type is still checked here. The
    incentive arm differs from core only in the claim predicate vocabulary (R-INC-09).
    """
    schemas: dict[ToolName, Mapping[str, Any]] = {
        name: spec.input_schema for name, spec in tool_specs(arm).items()
    }
    order = copy.deepcopy(dict(schemas[ToolName.PLACE_ORDER]))
    target = dict(order["properties"]["target_position"])
    target.pop("minimum", None)
    target.pop("maximum", None)
    order["properties"] = {**order["properties"], "target_position": target}
    schemas[ToolName.PLACE_ORDER] = order
    return schemas


SCHEMAS_BY_ARM: Mapping[str, Mapping[ToolName, Mapping[str, Any]]] = {
    arm: _service_schemas(arm) for arm in ARMS
}
SERVICE_SCHEMAS: Mapping[ToolName, Mapping[str, Any]] = SCHEMAS_BY_ARM["core"]


def service_schemas(arm: str = "core") -> Mapping[ToolName, Mapping[str, Any]]:
    """The services' schemas for `arm` (core = SERVICE_SCHEMAS); unknown arms raise."""
    if arm not in SCHEMAS_BY_ARM:
        raise ValueError(f"unknown arm {arm!r}; one of {ARMS}")
    return SCHEMAS_BY_ARM[arm]
