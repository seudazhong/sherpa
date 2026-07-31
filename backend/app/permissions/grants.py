"""Pre-authorization grant matching (ADR-034, Phase APPROVALS).

Owner-configured grants let the core loop auto-allow a matching external action
instead of asking. Matching is **per-tool and conservative** (no wildcard/regex in
v1): each supported tool registers a pure `matches(rule, args) -> bool`. A grant only
applies when `evaluate(tool)` already returned `ask`; a match flips it to `allow`
(the action still records its effect + an audit receipt tagged
`auto_approved_by_grant`). Grants are owner-only and never widen a scope.

First supported tool: `send_email` (exact, lowercased recipient allowlist). Adding a
tool = register a matcher + a `derive` rule (for the `always` → persist-grant path).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PermissionGrant

Rule = dict[str, object]
Args = dict[str, object]
Matcher = Callable[[Rule, Args], bool]


def _recipients(rule: Rule) -> set[str]:
    raw = rule.get("recipients", [])
    if not isinstance(raw, list):
        return set()
    return {str(a).strip().lower() for a in raw if str(a).strip()}


def _match_send_email(rule: Rule, args: Args) -> bool:
    allow = _recipients(rule)
    if not allow:
        return False
    return str(args.get("to", "")).strip().lower() in allow


def _derive_send_email(args: Args) -> Rule | None:
    to = str(args.get("to", "")).strip().lower()
    return {"recipients": [to]} if to else None


# tool_name -> (matcher, derive-rule-from-action-args). Only these tools support grants.
_MATCHERS: dict[str, Matcher] = {"email_send": _match_send_email}
_DERIVERS: dict[str, Callable[[Args], Rule | None]] = {"email_send": _derive_send_email}


def supported_tools() -> frozenset[str]:
    return frozenset(_MATCHERS)


def is_grantable(tool_name: str) -> bool:
    return tool_name in _MATCHERS


def rule_matches(tool_name: str, rule: Rule, args: Args) -> bool:
    matcher = _MATCHERS.get(tool_name)
    return bool(matcher and matcher(rule, args))


def derive_rule(tool_name: str, args: Args) -> Rule | None:
    """Derive a grant rule from an approved action (for the `always` choice)."""
    deriver = _DERIVERS.get(tool_name)
    return deriver(args) if deriver else None


async def find_matching_grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    tool_name: str,
    args: Args,
) -> PermissionGrant | None:
    """Return an active owner grant whose rule matches this action, or None."""
    matcher = _MATCHERS.get(tool_name)
    if matcher is None:
        return None
    rows = (
        (
            await session.execute(
                select(PermissionGrant).where(
                    PermissionGrant.tenant_id == tenant_id,
                    PermissionGrant.user_id == user_id,
                    PermissionGrant.tool_name == tool_name,
                    PermissionGrant.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for grant in rows:
        if matcher(grant.match_json, args):
            return grant
    return None
