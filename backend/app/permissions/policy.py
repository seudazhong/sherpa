"""Permission policy (ADR-020/008).

v1 policy is deliberate and small: read-only tools never ask; any write/destructive
tool is an *external action* and, per `user_settings.external_actions =
'approval_required'`, requires an approval envelope before dispatch. The policy
version is stamped onto every envelope; a policy change invalidates pending asks.
"""

from __future__ import annotations

from app.tools.base import Tool, ToolFlags

POLICY_VERSION = "v1"

# Effect taxonomy (events-and-effects.md) as gated by v1.
READ_ONLY = "read_only"
IDEMPOTENT_WRITE = "idempotent_write"
NON_IDEMPOTENT_WRITE = "non_idempotent_write"


def classify_effect(flags: ToolFlags) -> str:
    """Map tool flags to an effect class from the frozen taxonomy."""
    if flags.is_read_only:
        return READ_ONLY
    if flags.is_destructive or not flags.is_concurrency_safe:
        return NON_IDEMPOTENT_WRITE
    return IDEMPOTENT_WRITE


def requires_approval(tool: Tool) -> bool:
    """v1: every non-read-only (external) action must be approved before dispatch."""
    return not tool.flags.is_read_only


def permission_scope(tool_name: str) -> str:
    """Exact policy scope proposed for a tool; clients cannot broaden it."""
    return f"tool:{tool_name}"
