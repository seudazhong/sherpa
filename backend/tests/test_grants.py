"""Pre-authorization grants: matcher, loop auto-allow, owner-only (ADR-034, APR.B1).

Proves a matching owner grant flips `send_email` from ask → auto-allow (no approval
envelope; effect executed; audit receipt tagged auto_approved), an unmatched recipient
still asks, and grants are owner-only. Integration test — skips without Postgres.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import execute_run
from app.db import SessionLocal, ping_db
from app.models import ApprovalEnvelope, AuditReceipt, EffectInvocation, Run, Tenant, User
from app.models import Session as SessionModel
from app.permissions.grants import (
    PlatformGrant,
    find_matching_grant,
    is_platform_safe_command,
    rule_matches,
)
from app.providers import Finish, MockProvider, TextDelta, ToolCall
from app.services import Forbidden
from app.services import grants as grant_svc
from app.services.context import CallerContext
from app.tools import build_default_registry
from tests.db_guard import drop_owner_tenant

_ARGS = {"to": "me@x.com", "subject": "Hi", "body": "Hello there"}


async def _seed(s: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, Run]:
    tid, uid = uuid.uuid4(), uuid.uuid4()
    s.add(Tenant(tenant_id=tid, slug=f"t-{tid.hex[:8]}", display_name="T", kind="personal"))
    await s.flush()
    s.add(User(tenant_id=tid, id=uid, email="o@e.co", display_name="O", status="active"))
    await s.flush()
    sid, rid = uuid.uuid4(), uuid.uuid4()
    s.add(
        SessionModel(
            tenant_id=tid,
            id=sid,
            user_id=uid,
            umo_key=f"web:chat:{sid}",
            channel="web",
            channel_installation_id="local",
            scope_type="chat",
            external_scope_id=str(sid),
        )
    )
    await s.flush()
    run = Run(tenant_id=tid, id=rid, session_id=sid, run_kind="web_chat", prompt_version="v1")
    s.add(run)
    await s.flush()
    return tid, uid, run


def _script() -> MockProvider:
    return MockProvider(
        script=[
            [ToolCall(id="c1", name="email_send", args=_ARGS), Finish("tool_use")],
            [TextDelta("Sent."), Finish("stop")],
        ]
    )


def test_matcher_exact_recipient() -> None:
    assert rule_matches("email_send", {"recipients": ["me@x.com"]}, {"to": "ME@X.com"})
    assert not rule_matches("email_send", {"recipients": ["me@x.com"]}, {"to": "other@x.com"})
    assert not rule_matches("email_send", {"recipients": []}, {"to": "me@x.com"})
    assert not rule_matches("todo_list", {"recipients": ["me@x.com"]}, {"to": "me@x.com"})


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python -m pytest tests/test_x.py",
        "python -m compileall src",
        "ruff check .",
        "ruff format --check .",
        "pwd",
        "ls -la src",
        "cat pyproject.toml",
    ],
)
def test_platform_safe_commands(command: str) -> None:
    assert is_platform_safe_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q; rm -rf /work",
        "pytest | tee out",
        "ruff check . > result.txt",
        "echo $(cat /etc/passwd)",
        "X=1 pytest",
        "python script.py",
        "sh -c 'pytest'",
        "pytest\nrm -rf /work",
        "pytest &",
    ],
)
def test_platform_safe_command_rejects_shell_composition(command: str) -> None:
    assert not is_platform_safe_command(command)


@pytest.mark.asyncio
async def test_find_matching_grant_returns_platform_safe_command() -> None:
    async with SessionLocal() as s:
        grant = await find_matching_grant(
            s,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            tool_name="sh_exec",
            args={"command": "pytest -q"},
        )
        assert isinstance(grant, PlatformGrant)


@pytest.mark.asyncio
async def test_loop_auto_allows_with_grant() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, run = await _seed(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            await grant_svc.create_grant(
                s, ctx, tool_name="email_send", match_json={"recipients": ["me@x.com"]}
            )

            reason = await execute_run(
                s, run=run, provider=_script(), registry=build_default_registry(), tier="full"
            )
            assert reason == "completed"

            # No approval envelope — the grant pre-authorized it.
            env = await s.scalar(select(ApprovalEnvelope).where(ApprovalEnvelope.run_id == run.id))
            assert env is None

            # The effect actually ran (not left 'prepared').
            inv = (
                await s.execute(
                    select(EffectInvocation).where(
                        EffectInvocation.tenant_id == tid, EffectInvocation.run_id == run.id
                    )
                )
            ).scalar_one()
            assert inv.effect_name == "email_send" and inv.status != "prepared"

            # An audit receipt records the auto-approval.
            outcomes = (
                (
                    await s.execute(
                        select(AuditReceipt.outcome).where(
                            AuditReceipt.tenant_id == tid, AuditReceipt.run_id == run.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert "auto_approved" in outcomes
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_loop_still_asks_without_matching_grant() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, run = await _seed(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            # Grant for a DIFFERENT recipient → does not match this action.
            await grant_svc.create_grant(
                s, ctx, tool_name="email_send", match_json={"recipients": ["other@x.com"]}
            )

            await execute_run(
                s, run=run, provider=_script(), registry=build_default_registry(), tier="full"
            )
            env = await s.scalar(select(ApprovalEnvelope).where(ApprovalEnvelope.run_id == run.id))
            assert env is not None and env.status == "pending"  # still asked
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_find_matching_grant_skips_revoked() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, _run = await _seed(s)
            ctx = CallerContext(tenant_id=tid, user_id=uid, actor="user")
            g = await grant_svc.create_grant(
                s, ctx, tool_name="email_send", match_json={"recipients": ["me@x.com"]}
            )
            assert (
                await find_matching_grant(
                    s, tenant_id=tid, user_id=uid, tool_name="email_send", args=_ARGS
                )
                is not None
            )
            await grant_svc.revoke_grant(s, ctx, grant_id=g.id)
            assert (
                await find_matching_grant(
                    s, tenant_id=tid, user_id=uid, tool_name="email_send", args=_ARGS
                )
                is None
            )
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_grant_from_action_creates_and_merges() -> None:
    # APR.B2: the `always` path persists a grant and merges new recipients into it.
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, _run = await _seed(s)
            g1 = await grant_svc.grant_from_action(
                s, tenant_id=tid, user_id=uid, tool_name="email_send", args={"to": "me@x.com"}
            )
            assert g1 is not None and g1.created_via == "always"
            assert g1.match_json["recipients"] == ["me@x.com"]

            # A second `always` for a different recipient merges into the same grant.
            g2 = await grant_svc.grant_from_action(
                s, tenant_id=tid, user_id=uid, tool_name="email_send", args={"to": "Work@Corp.com"}
            )
            assert g2 is not None and g2.id == g1.id
            assert set(g2.match_json["recipients"]) == {"me@x.com", "work@corp.com"}

            # A non-grantable tool derives nothing.
            assert (
                await grant_svc.grant_from_action(
                    s, tenant_id=tid, user_id=uid, tool_name="todo_list", args={}
                )
                is None
            )
        finally:
            await s.rollback()


@pytest.mark.asyncio
async def test_grants_rest_end_to_end() -> None:
    import httpx
    from httpx import ASGITransport

    from app.config import settings
    from app.main import app
    from app.redis_client import ping_redis

    if not await ping_db() or not await ping_redis():
        pytest.skip("database or redis not reachable")

    await drop_owner_tenant()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        login = await client.post(
            "/auth/login",
            json={"email": settings.owner_email, "password": settings.owner_password},
        )
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}

        created = await client.post(
            "/grants",
            json={"tool_name": "email_send", "match_json": {"recipients": ["me@x.com"]}},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        gid = created.json()["id"]

        listing = await client.get("/grants")
        assert any(g["id"] == gid for g in listing.json()["items"])

        # A non-grantable tool is rejected (422).
        bad = await client.post(
            "/grants", json={"tool_name": "todo_list", "match_json": {"x": 1}}, headers=headers
        )
        assert bad.status_code == 422

        gone = await client.request("DELETE", f"/grants/{gid}", headers=headers)
        assert gone.status_code == 204
        listing2 = await client.get("/grants")
        assert not any(g["id"] == gid for g in listing2.json()["items"])


@pytest.mark.asyncio
async def test_grants_are_owner_only() -> None:
    if not await ping_db():
        pytest.skip("database not reachable")
    async with SessionLocal() as s:
        try:
            tid, uid, _run = await _seed(s)
            agent = CallerContext(tenant_id=tid, user_id=uid, actor="agent")
            with pytest.raises(Forbidden):
                await grant_svc.create_grant(
                    s, agent, tool_name="email_send", match_json={"recipients": ["me@x.com"]}
                )
            with pytest.raises(Forbidden):
                await grant_svc.list_grants(s, agent)
        finally:
            await s.rollback()
