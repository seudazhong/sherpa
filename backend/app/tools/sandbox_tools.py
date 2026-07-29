"""Code execution tool (ADR-007/025): the agent runs Python in a hardened sandbox.

The sandbox (ephemeral, network-off, resource-capped container) is the safety
boundary, so the policy engine allows it without approval. Output is bounded by
the loop. Only FULL-tier (authenticated) sessions get it — never the
untrusted-content analysis pipeline.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.sandbox import run_code
from app.sandbox.runner import SANDBOX_DISABLED, runtime_failure_note
from app.tools.base import ToolContext, ToolFlags, ToolResult
from app.tools.validate import validate_args

logger = logging.getLogger("app.tools.sandbox")


class RunCodeTool:
    name = "run_code"
    description = (
        "Execute a short Python 3 snippet in a secure sandbox (no network, "
        "ephemeral, memory/time limited) and return its output. Use for "
        "calculations, quick scripts, or checking logic. Print results to see them."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"code": {"type": "string", "minLength": 1, "maxLength": 100000}},
        "required": ["code"],
    }
    flags = ToolFlags(is_read_only=False, is_concurrency_safe=True, is_destructive=False)

    async def execute(self, ctx: ToolContext, args: dict[str, object]) -> ToolResult:
        validate_args(self.input_schema, args)
        result = await run_code(str(args["code"]))
        if result.error:
            # One structured worker log line (with the bounded raw detail) + one redacted
            # observation naming the same reason — never the raw exception text, which can
            # carry host paths (events §2.11 ④ + ADR-019).
            logger.warning(
                "run_code sandbox failed",
                extra={
                    "termination_reason": result.error,
                    "sandbox_kind": settings.sandbox_kind,
                    "sandbox_image": settings.sandbox_image,
                    "sandbox_error_detail": result.error_detail,
                },
            )
            if result.error == SANDBOX_DISABLED:
                return ToolResult(llm_content="code execution is not enabled in this environment")
            return ToolResult(llm_content=f"sandbox error: {runtime_failure_note(result.error)}")
        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout.rstrip("\n"))
        if result.stderr:
            parts.append("[stderr]\n" + result.stderr.rstrip("\n"))
        if result.timed_out:
            parts.append(f"[timed out after {settings.sandbox_timeout_seconds}s]")
        parts.append(f"[exit {result.exit_code}]")
        return ToolResult(llm_content="\n".join(parts))


def sandbox_tools() -> list[object]:
    return [RunCodeTool()]
