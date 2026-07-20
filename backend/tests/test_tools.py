"""Tool interface: validation, output bounding, registry visibility, execution."""

from __future__ import annotations

import uuid

import pytest

from app.tools import ToolContext, ToolError, bound_text, build_default_registry, validate_args


def _ctx() -> ToolContext:
    return ToolContext(tenant_id=uuid.uuid4(), user_id=uuid.uuid4())


def test_validate_missing_required() -> None:
    schema = {"properties": {"x": {"type": "string"}}, "required": ["x"]}
    with pytest.raises(ToolError):
        validate_args(schema, {})


def test_validate_type_mismatch() -> None:
    schema = {"properties": {"n": {"type": "integer"}}}
    with pytest.raises(ToolError):
        validate_args(schema, {"n": "not-an-int"})


def test_validate_ok() -> None:
    schema = {"properties": {"n": {"type": "integer"}}, "required": ["n"]}
    validate_args(schema, {"n": 5})  # no exception


def test_bound_text_short_not_truncated() -> None:
    out = bound_text("a\nb\nc")
    assert out.truncated is False
    assert out.original_lines == 3


def test_bound_text_long_truncated() -> None:
    text = "\n".join(str(i) for i in range(5000))
    out = bound_text(text)
    assert out.truncated is True
    assert out.original_lines == 5000
    assert "truncated" in out.text


def test_registry_visibility() -> None:
    reg = build_default_registry()
    assert {t.name for t in reg.visible("safe")} == {"echo", "get_time"}
    assert reg.is_visible("echo", "safe") is True


def test_unknown_tool_raises() -> None:
    reg = build_default_registry()
    with pytest.raises(ToolError):
        reg.get("does-not-exist")


@pytest.mark.asyncio
async def test_echo_executes() -> None:
    reg = build_default_registry()
    result = await reg.get("echo").execute(_ctx(), {"text": "hello"})
    assert result.llm_content == "hello"


@pytest.mark.asyncio
async def test_get_time_executes() -> None:
    reg = build_default_registry()
    result = await reg.get("get_time").execute(_ctx(), {})
    assert "T" in result.llm_content
