"""Caller context (ADR-023, docs/11 §3).

Unifies the two adapters' identities into one value the capability layer accepts:
REST builds it from the authenticated session (`actor="user"`), the agent tool
adapter builds it from the runtime `ToolContext` (`actor="agent"`), and background
jobs use `actor="system"`. It carries tenant/user for isolation + audit, and the
optional run binding for effect/receipt provenance.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Literal

Actor = Literal["user", "agent", "system"]


@dataclasses.dataclass(frozen=True)
class CallerContext:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    actor: Actor
    session_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    invocation_id: uuid.UUID | None = None
