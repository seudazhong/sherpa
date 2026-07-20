"""Permission policy (ADR-020/008).

v1 policy is deliberate and small: read-only tools never ask; any write/destructive
tool is an *external action* and, per `user_settings.external_actions =
'approval_required'`, requires an approval envelope before dispatch. The policy
version is stamped onto every envelope; a policy change invalidates pending asks.
"""

from __future__ import annotations

from typing import Literal

from app.tools.base import Tool, ToolFlags

POLICY_VERSION = "v1"

Decision = Literal["allow", "ask", "deny"]

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


def evaluate(tool: Tool) -> Decision:
    """ALLOWED gate (api.md §7.1 step 3). v1 fixed policy; a per-tenant policy table
    (allow|ask|deny, last-match) is the future extension point. All v1 tools act on
    the caller's own tenant, so own-data writes are allowed; only external/
    destructive actions ask; unknown effects fail closed to deny.
    """
    effect = classify_effect(tool.flags)
    if effect == READ_ONLY:
        return "allow"
    if effect == IDEMPOTENT_WRITE:
        return "allow"  # own-tenant write on the user's explicit instruction
    if effect == NON_IDEMPOTENT_WRITE:
        return "ask"  # external / destructive → approval envelope
    return "deny"  # fail closed


def requires_approval(tool: Tool) -> bool:
    """Back-compat helper: True when the policy decision is `ask`."""
    return evaluate(tool) == "ask"


def permission_scope(tool_name: str) -> str:
    """Exact policy scope proposed for a tool; clients cannot broaden it."""
    return f"tool:{tool_name}"
