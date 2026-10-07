"""A small JSON-Schema subset validator for tool arguments.

Supports what the tool schemas use: type, enum, required, properties, additionalProperties,
items, minimum, maximum, minLength, maxLength, minItems, maxItems and pattern. Unknown
properties are rejected so a model cannot smuggle extra fields through to the web app.
"""
from __future__ import annotations

import re
from typing import Any

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}

# Identifiers from the web app (projects, compounds, runs, models). Used in URL paths, so no slashes or dots-only.
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


def validate(value: Any, schema: dict, path: str = "arguments") -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    problems: list[str] = []
    expected = schema.get("type")
    if expected and not _TYPES[expected](value):
        return [f"{path} must be {'an' if expected in {'integer', 'object', 'array'} else 'a'} {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path} must be one of: {', '.join(map(str, schema['enum']))}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            problems.append(f"{path} is too short (minimum {schema['minLength']} characters)")
        if len(value) > schema.get("maxLength", 1 << 30):
            problems.append(f"{path} is too long (maximum {schema['maxLength']} characters)")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            problems.append(f"{path} has an unexpected format")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path} must be at most {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            problems.append(f"{path} needs at least {schema['minItems']} item(s)")
        if len(value) > schema.get("maxItems", 1 << 30):
            problems.append(f"{path} has too many items (maximum {schema['maxItems']})")
        if "items" in schema:
            for index, item in enumerate(value[:200]):
                problems.extend(validate(item, schema["items"], f"{path}[{index}]"))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{path}.{name} is required")
        for name, item in value.items():
            if name in properties:
                problems.extend(validate(item, properties[name], f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                problems.append(f"{path}.{name} is not an accepted argument")
            elif isinstance(schema.get("additionalProperties"), dict):
                problems.extend(validate(item, schema["additionalProperties"], f"{path}.{name}"))
    return problems
