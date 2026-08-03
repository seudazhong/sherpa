"""Permission policy (ADR-020/008).

v1 policy is deliberate and small: read-only tools never ask; any write/destructive
tool is an *external action* and, per `user_settings.external_actions =
'approval_required'`, requires an approval envelope before dispatch. The policy
version is stamped onto every envelope; a policy change invalidates pending asks.
"""

from __future__ import annotations

import fnmatch
from typing import Literal

from app.services.archive import ArchiveError, _normalize_path
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


_SENSITIVE_FS_TOOLS = frozenset({"fs_write", "fs_edit", "fs_delete"})
_SENSITIVE_BASENAME_PATTERNS = (".env*", "*.pem", "*.key", "id_*")


def _policy_path(raw: object) -> str | None:
    try:
        return _normalize_path(str(raw or "").strip(), depth_cap=64, length_cap=1024)
    except ArchiveError:
        return None


def is_sensitive_project_path(raw: object) -> bool:
    """Whether a Project mutation crosses the credential/CI approval boundary."""
    path = _policy_path(raw)
    if path is None:
        return True  # fail closed; the filesystem layer will later reject the bad path
    lowered = path.casefold()
    if lowered == ".github" or lowered.startswith(".github/workflows"):
        return True
    return any(
        fnmatch.fnmatch(segment.casefold(), pattern)
        for segment in path.split("/")
        for pattern in _SENSITIVE_BASENAME_PATTERNS
    )


def evaluate(tool: Tool, args: dict[str, object] | None = None) -> Decision:
    """ALLOWED gate (api.md §7.1 step 3). v1 fixed policy; a per-tenant policy table
    (allow|ask|deny, last-match) is the future extension point. All v1 tools act on
    the caller's own tenant, so own-data writes are allowed; only external/
    destructive actions ask; unknown effects fail closed to deny.
    """
    if (
        getattr(tool, "name", "") in _SENSITIVE_FS_TOOLS
        and args is not None
        and (
            is_sensitive_project_path(args.get("path"))
            or (tool.name == "fs_delete" and bool(args.get("recursive", False)))
        )
    ):
        return "ask"
    effect = classify_effect(tool.flags)
    if effect == READ_ONLY:
        return "allow"
    if effect == IDEMPOTENT_WRITE:
        return "allow"  # own-tenant write on the user's explicit instruction
    if effect == NON_IDEMPOTENT_WRITE:
        return "ask"  # external / destructive → approval envelope
    return "deny"  # fail closed


def requires_approval(tool: Tool, args: dict[str, object] | None = None) -> bool:
    """Back-compat helper: True when the policy decision is `ask`."""
    return evaluate(tool, args) == "ask"


def permission_scope(tool_name: str, args: dict[str, object] | None = None) -> str:
    """Exact policy scope proposed for a tool; clients cannot broaden it."""
    if tool_name in _SENSITIVE_FS_TOOLS and args is not None:
        path = _policy_path(args.get("path")) or "invalid"
        if path:
            return f"tool:{tool_name}:path:{path[:440]}"
    return f"tool:{tool_name}"
