"""Tool registry + visibility gate (docs/05: registered → visible → allowed → executable).

v1 implements registration + trust-tier visibility (SAFE for untrusted-content
sessions, FULL for authenticated users). Call-time authorization (permission engine)
arrives with the approval work; keep visibility and authorization separate.
"""

from __future__ import annotations

from app.tools.base import Tool, ToolError

SAFE = "safe"
FULL = "full"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._safe: set[str] = set()

    def register(self, tool: Tool, *, safe: bool = False) -> None:
        self._tools[tool.name] = tool
        if safe:
            self._safe.add(tool.name)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"unknown tool: {name}") from None

    def is_visible(self, name: str, tier: str) -> bool:
        if name not in self._tools:
            return False
        return tier == FULL or name in self._safe

    def visible(self, tier: str) -> list[Tool]:
        if tier == FULL:
            return list(self._tools.values())
        return [tool for name, tool in self._tools.items() if name in self._safe]

    def schemas(self, tier: str) -> list[dict[str, object]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self.visible(tier)
        ]
