"""arq worker: consumes run jobs and executes the core loop (docs/03, docs/04).

Run: `uv run arq app.worker.WorkerSettings`
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from app import queue
from app.channels import (
    compose_reply,
    final_assistant_text,
    pending_approval_for_run,
)
from app.channels.email import build_email_channel_client
from app.channels.qq_official import build_qq_sender
from app.config import settings
from app.connectors.gmail import build_gmail_sync_client
from app.connectors.sync import sync_gmail
from app.core import execute_run, resume_approval
from app.db import SessionLocal
from app.events import append_event, relay_once
from app.models import ApprovalEnvelope, Connector, Run
from app.models import Session as SessionModel
from app.notifications import build_email_sender, deliver_due_firings
from app.observability import bind_context, configure_logging, project_run_trace
from app.providers import build_provider
from app.redis_client import client as redis_client
from app.scheduler import fire_due_schedules, try_acquire_leader
from app.scheduler.pipeline import sync_and_analyze
from app.services import channels as chan_svc
from app.tools import build_default_registry


async def ping(ctx: dict[str, Any]) -> str:
    """Liveness job proving worker wiring."""
    return "pong"


async def _settle_failed(
    tenant_id: uuid.UUID, run_id: uuid.UUID, session_id: uuid.UUID | None
) -> None:
    """Settle a run as failed in a fresh transaction after a provider/loop error."""
    async with SessionLocal() as session:
        run = await session.get(Run, (tenant_id, run_id))
        if run is None or run.status in (
            "succeeded",
            "failed",
            "cancelled",
            "needs_reconciliation",
        ):
            return
        now = datetime.datetime.now(datetime.UTC)
        run.status = "failed"
        run.started_at = run.started_at or now
        run.settled_at = now
        await session.flush()
        await append_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            session_id=session_id,
            event_type="run.settled",
            payload={"reason": "failed", "status": "failed"},
        )
        await session.commit()


async def gmail_sync_job(ctx: dict[str, Any], connector_id: str, run_id: str) -> str:
    """Run a Gmail sync for a connector under a durable gmail_sync run."""
    cid, rid = uuid.UUID(connector_id), uuid.UUID(run_id)
    async with SessionLocal() as session:
        run = (await session.execute(select(Run).where(Run.id == rid))).scalar_one_or_none()
        connector = (
            await session.execute(select(Connector).where(Connector.id == cid))
        ).scalar_one_or_none()
        if run is None or connector is None:
            return "unknown"
        tenant_id, session_id = run.tenant_id, run.session_id
        bind_context(tenant_id=str(tenant_id), run_id=str(rid))
        try:
            run.status = "running"
            run.started_at = datetime.datetime.now(datetime.UTC)
            await session.flush()
            result = await sync_gmail(
                session, connector=connector, client=build_gmail_sync_client()
            )
            run.status = "succeeded"
            run.settled_at = datetime.datetime.now(datetime.UTC)
            await session.flush()
            await append_event(
                session,
                tenant_id=tenant_id,
                run_id=rid,
                session_id=session_id,
                event_type="run.settled",
                payload={
                    "reason": "completed",
                    "status": "succeeded",
                    "new_items": result.new_items,
                },
            )
            await session.commit()
            return "completed"
        except Exception:
            await session.rollback()
            await _settle_failed(tenant_id, rid, session_id)
            return "failed"


async def _deliver_im_reply(run_id: uuid.UUID) -> None:
    """Best-effort: push a settled run's reply back to its channel thread (milestones 4–5).

    Runs in a fresh read session after the run commits so a delivery failure never
    affects run durability. Only channel-bound sessions (``qq``/``email``) deliver;
    web sessions are served by SSE.
    """
    try:
        async with SessionLocal() as session:
            run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
            if run is None or run.session_id is None:
                return
            sess = await session.get(SessionModel, (run.tenant_id, run.session_id))
            if sess is None:
                return
            if sess.channel == "qq":
                await _deliver_qq_reply(session, sess, run.tenant_id, run_id)
            elif sess.channel == "email":
                text = await final_assistant_text(session, run.tenant_id, run_id)
                approval = await pending_approval_for_run(session, run.tenant_id, run_id)
                if text is None and approval is None:
                    return
                body = compose_reply(text, approval)
                await build_email_channel_client().send(
                    to=sess.external_scope_id, subject="Re: Sherpa", text=body
                )
    except Exception:
        return


async def _deliver_qq_reply(
    session: Any, sess: SessionModel, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> None:
    """Deliver a settled run's reply to a QQ thread via a passive C2C reply."""
    text = await final_assistant_text(session, tenant_id, run_id)
    approval = await pending_approval_for_run(session, tenant_id, run_id)
    if text is None and approval is None:
        return
    config = await chan_svc.get_config(session, tenant_id, sess.user_id, "qq")
    if config is None or not config.enabled or not config.app_id:
        return
    secret = chan_svc.reveal_secret(config)
    if secret is None:
        return
    msg_id = await chan_svc.last_inbound_msg_id(session, tenant_id, sess.id)
    body = compose_reply(text, approval)
    sender = build_qq_sender(config.app_id, secret)
    await sender.send_private(sess.external_scope_id, body, msg_id)


async def run_job(ctx: dict[str, Any], run_id: str) -> str:
    """Execute one durable run. v1 commits at settle; per-turn commit is a later refinement."""
    async with SessionLocal() as session:
        run = (
            await session.execute(select(Run).where(Run.id == uuid.UUID(run_id)))
        ).scalar_one_or_none()
        if run is None:
            return "unknown_run"
        tenant_id, rid, session_id = run.tenant_id, run.id, run.session_id
        bind_context(
            tenant_id=str(tenant_id),
            run_id=str(rid),
            session_id=str(session_id) if session_id is not None else None,
        )
        try:
            reason = await execute_run(
                session,
                run=run,
                provider=build_provider(),
                registry=build_default_registry(),
            )
            await project_run_trace(session, tenant_id=tenant_id, run_id=rid)
            await session.commit()
            await _deliver_im_reply(rid)
            return reason
        except Exception:
            await session.rollback()
            await _settle_failed(tenant_id, rid, session_id)
            return "failed"


async def _deliver_im_resume_ack(correlation_id: uuid.UUID, status: str) -> None:
    """Best-effort: confirm an IM-resolved approval's outcome back to the thread."""
    if status not in ("resumed", "failed"):
        return
    try:
        async with SessionLocal() as session:
            env = (
                await session.execute(
                    select(ApprovalEnvelope).where(
                        ApprovalEnvelope.correlation_id == correlation_id
                    )
                )
            ).scalar_one_or_none()
            if env is None or env.session_id is None:
                return
            sess = await session.get(SessionModel, (env.tenant_id, env.session_id))
            if sess is None or sess.channel not in ("qq", "email"):
                return
            msg = (
                f"\u2705 {env.tool_name} completed."
                if status == "resumed"
                else f"\u26a0\ufe0f {env.tool_name} could not be completed."
            )
            if sess.channel == "qq":
                config = await chan_svc.get_config(session, env.tenant_id, sess.user_id, "qq")
                if config is None or not config.enabled or not config.app_id:
                    return
                secret = chan_svc.reveal_secret(config)
                if secret is None:
                    return
                msg_id = await chan_svc.last_inbound_msg_id(session, env.tenant_id, sess.id)
                await build_qq_sender(config.app_id, secret).send_private(
                    sess.external_scope_id, msg, msg_id
                )
            else:
                await build_email_channel_client().send(
                    to=sess.external_scope_id, subject="Re: Sherpa", text=msg
                )
    except Exception:
        return


async def approval_resume_job(ctx: dict[str, Any], correlation_id: str) -> str:
    """Resume a run after an approval decision (api.md §6.3/§6.4).

    Thin arq wrapper; the resume logic lives in app.core.resume (session-taking,
    testable). Idempotent on the bound invocation's settled state.
    """
    cid = uuid.UUID(correlation_id)
    async with SessionLocal() as session:
        try:
            status = await resume_approval(session, cid)
            await session.commit()
            await _deliver_im_resume_ack(cid, status)
            return status
        except Exception:
            await session.rollback()
            return "failed"


async def _relay_loop() -> None:
    """Continuously publish outbox rows to Redis Streams (at-least-once delivery)."""
    while True:
        try:
            async with SessionLocal() as session:
                relayed = await relay_once(session, redis_client)
                await session.commit()
            await asyncio.sleep(0.2 if relayed else 0.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(1.0)


async def _qq_gateway_loop() -> None:
    """Supervise the official QQ botpy WebSocket client (ADR-028).

    Polls for an enabled, credentialed QQ config; while one exists, runs the botpy
    client (``client.start`` blocks until disconnect) and reconnects with a short
    backoff. When no config exists, idles and re-checks. Leader-gated so only one
    worker holds the single-account WS connection.
    """
    from app.channels.qq_official import build_qq_client_for_gateway

    while True:
        try:
            if not await try_acquire_leader("qq_gateway", ttl_ms=55_000):
                await asyncio.sleep(30)
                continue
            creds: tuple[uuid.UUID, uuid.UUID, str, str, str] | None = None
            async with SessionLocal() as session:
                configs = await chan_svc.active_qq_configs(session)
                if configs:
                    cfg = configs[0]
                    secret = chan_svc.reveal_secret(cfg)
                    if secret:
                        creds = (
                            cfg.tenant_id,
                            cfg.user_id,
                            cfg.app_id,
                            secret,
                            cfg.owner_external_id,
                        )
            if creds is None:
                await asyncio.sleep(30)
                continue
            tenant_id, user_id, app_id, secret, owner = creds
            bind_context(tenant_id=str(tenant_id))
            client = build_qq_client_for_gateway(
                tenant_id=tenant_id,
                user_id=user_id,
                app_id=app_id,
                secret=secret,
                owner_openid=owner,
            )
            await client.start(app_id, secret)  # blocks until the WS disconnects
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(15)


async def _startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    ctx["relay_task"] = asyncio.create_task(_relay_loop())
    ctx["qq_gateway_task"] = asyncio.create_task(_qq_gateway_loop())


async def _shutdown(ctx: dict[str, Any]) -> None:
    for key in ("relay_task", "qq_gateway_task"):
        task = ctx.get(key)
        if task is not None:
            task.cancel()


async def sync_and_analyze_job(ctx: dict[str, Any], connector_id: str) -> str:
    """Sync a connector and analyze its new items into candidates."""
    async with SessionLocal() as session:
        connector = (
            await session.execute(select(Connector).where(Connector.id == uuid.UUID(connector_id)))
        ).scalar_one_or_none()
        if connector is None:
            return "unknown_connector"
        try:
            result = await sync_and_analyze(
                session,
                connector=connector,
                sync_client=build_gmail_sync_client(),
                provider=build_provider(),
                provider_name=settings.provider_kind,
                model=settings.provider_model,
            )
            await session.commit()
            return (
                f"synced={result.synced} analyzed={result.analyzed} candidates={result.candidates}"
            )
        except Exception:
            await session.rollback()
            return "failed"


async def scheduler_tick(ctx: dict[str, Any]) -> str:
    """Leader-gated: fire all due schedules (at-most-once per slot)."""
    if not await try_acquire_leader("scheduler_tick", ttl_ms=55_000):
        return "not_leader"
    async with SessionLocal() as session:
        fired = await fire_due_schedules(session, datetime.datetime.now(datetime.UTC))
        await session.commit()
    return f"fired={len(fired)}"


async def periodic_connector_sync(ctx: dict[str, Any]) -> str:
    """Leader-gated: enqueue a sync+analyze job for every active connector."""
    if not await try_acquire_leader("connector_sync", ttl_ms=280_000):
        return "not_leader"
    async with SessionLocal() as session:
        connector_ids = (
            (await session.execute(select(Connector.id).where(Connector.status == "active")))
            .scalars()
            .all()
        )
    for cid in connector_ids:
        await queue.enqueue_sync_and_analyze(cid)
    return f"enqueued={len(connector_ids)}"


async def delivery_tick(ctx: dict[str, Any]) -> str:
    """Leader-gated: deliver ready pending schedule firings (web/email)."""
    if not await try_acquire_leader("delivery_tick", ttl_ms=55_000):
        return "not_leader"
    async with SessionLocal() as session:
        counts = await deliver_due_firings(
            session, build_email_sender(), datetime.datetime.now(datetime.UTC)
        )
        await session.commit()
    return f"delivered={counts}"


class WorkerSettings:
    functions = [ping, run_job, gmail_sync_job, sync_and_analyze_job, approval_resume_job]
    cron_jobs = [
        cron(scheduler_tick, second=0),
        cron(delivery_tick, second=15),
        cron(periodic_connector_sync, minute=set(range(0, 60, 5))),
    ]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
