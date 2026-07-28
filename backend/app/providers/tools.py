"""Tool-schema serializers: one canonical internal shape → each provider wire format.

Sherpa's internal tool schema is ``{name, description, input_schema}`` (JSON Schema for the
arguments). Each provider needs a different serialization (ADR-041; mirrors AstrBot's
``openai_schema``/``anthropic_schema``/``google_schema``). Gemini additionally requires a
stricter JSON Schema (single ``type`` string, no ``additionalProperties``, ``array`` items
present), so its serializer sanitizes the schema.
"""

from __future__ import annotations

from typing import Any

from app.providers.base import ToolSchema


def to_openai_tools(tools: list[ToolSchema] | None) -> list[dict[str, object]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object"}),
            },
        }
        for t in tools
    ]


def to_anthropic_tools(tools: list[ToolSchema] | None) -> list[dict[str, object]] | None:
    """Anthropic's tool shape is already `{name, description, input_schema}` — near identity."""
    if not tools:
        return None
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("input_schema", {"type": "object"}),
        }
        for t in tools
    ]


def _sanitize_gemini_schema(schema: Any) -> Any:
    """Recursively coerce a JSON Schema into what Gemini's `functionDeclarations` accepts:
    a single `type` string (list types → first non-null), no `additionalProperties`, and
    an `items` present on arrays (research: Gemini rejects these otherwise)."""
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k == "additionalProperties":
            continue
        if k == "type" and isinstance(v, list):
            non_null = [t for t in v if t != "null"]
            out["type"] = non_null[0] if non_null else "string"
        elif k == "properties" and isinstance(v, dict):
            out["properties"] = {pk: _sanitize_gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out["items"] = _sanitize_gemini_schema(v)
        else:
            out[k] = _sanitize_gemini_schema(v) if isinstance(v, dict) else v
    if out.get("type") == "array" and "items" not in out:
        out["items"] = {"type": "string"}
    return out


def to_gemini_tools(tools: list[ToolSchema] | None) -> list[dict[str, object]] | None:
    if not tools:
        return None
    decls: list[dict[str, object]] = []
    for t in tools:
        params = _sanitize_gemini_schema(t.get("input_schema", {"type": "object"}))
        decl: dict[str, object] = {"name": t["name"], "description": t.get("description", "")}
        # Gemini rejects an empty `parameters` object; omit it when there are no properties.
        if isinstance(params, dict) and params.get("properties"):
            decl["parameters"] = params
        decls.append(decl)
    return [{"functionDeclarations": decls}]
