"""Lightweight argument validation against a tool's input schema.

Not a full JSON-Schema engine (v1 keeps deps minimal): checks required keys are
present and primitive types match. Sufficient for the v1 starter tools; swap in a
full validator when tool inputs grow complex.
"""

from __future__ import annotations

from app.tools.base import ToolError

_PY_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate_args(schema: dict[str, object], args: dict[str, object]) -> None:
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if key not in args:
                raise ToolError(f"missing required argument: {key}")

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return
    for key, value in args.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        declared = spec.get("type")
        expected = _PY_TYPES.get(declared) if isinstance(declared, str) else None
        # bool is a subtype of int; guard so a bool isn't accepted as integer/number
        if expected and (
            not isinstance(value, expected) or (declared != "boolean" and isinstance(value, bool))
        ):
            raise ToolError(f"argument '{key}' must be of type {declared}")
