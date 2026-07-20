"""Tools: interface, registry, validation, output bounding, starter built-ins."""

from __future__ import annotations

from app.tools.base import DisplayPayload, Tool, ToolContext, ToolError, ToolFlags, ToolResult
from app.tools.bounding import BoundedOutput, bound_text, spill_output
from app.tools.builtin import EchoTool, GetTimeTool, build_default_registry
from app.tools.registry import FULL, SAFE, ToolRegistry
from app.tools.validate import validate_args

__all__ = [
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolFlags",
    "ToolError",
    "DisplayPayload",
    "ToolRegistry",
    "SAFE",
    "FULL",
    "build_default_registry",
    "EchoTool",
    "GetTimeTool",
    "bound_text",
    "spill_output",
    "BoundedOutput",
    "validate_args",
]
