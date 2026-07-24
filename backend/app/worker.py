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
from app.core.lease import claim_run_lease, run_heartbeat, worker_identity
from app.db import SessionLocal
from app.events import append_event, relay_once
from app.models import ApprovalEnvelope, Connector, Run, Schedule, ScheduleFiring
from app.models import Session as SessionModel
from app.notifications import build_email_sender, deliver_due_firings
from app.observability import bind_context, configure_logging, project_run_trace
from app.providers import build_provider
from app.redis_client import client as redis_client
from app.scheduler import dispatch_due_agent_tasks, fire_due_schedules, try_acquire_leader
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
        run.lease_expires_at = None
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


async def _deliver_scheduled_task(run_id: uuid.UUID) -> None:
    """Settle a scheduled_task firing + deliver its output (worker wrapper).

    Runs in a fresh session after the run commits so a delivery failure never affects
    run durability.
    """
    try:
        async with SessionLocal() as session:
            await settle_scheduled_firing(session, run_id)
            await session.commit()
    except Exception:
        return


async def settle_scheduled_firing(session: Any, run_id: uuid.UUID) -> str | None:
    """Settle the ``running`` firing for ``run_id`` and best-effort push its result.

    The result is always durably visible in the schedule's dedicated session and, once
    the firing settles, in the web inbox. Email/QQ delivery is a best-effort push on
    top and never changes the firing outcome. Returns the outcome, or None if there is
    no matching firing. Caller owns the transaction.
    """
    firing = (
        await session.execute(
            select(ScheduleFiring).where(
                ScheduleFiring.run_id == run_id, ScheduleFiring.status == "running"
            )
        )
    ).scalar_one_or_none()
    if firing is None:
        return None
    schedule = await session.get(Schedule, (firing.tenant_id, firing.schedule_id))
    run = await session.get(Run, (firing.tenant_id, run_id))
    if schedule is None or run is None:
        return None

    text = await final_assistant_text(session, firing.tenant_id, run_id)
    approval = await pending_approval_for_run(session, firing.tenant_id, run_id)
    failed = run.status in ("failed", "needs_reconciliation") or (text is None and approval is None)
    outcome = "failed" if failed else "delivered"

    if not failed:
        await _push_scheduled_result(session, schedule, compose_reply(text, approval))

    now = datetime.datetime.now(datetime.UTC)
    firing.status = "settled"
    firing.delivery_outcome = outcome
    firing.settled_at = now
    firing.updated_at = now
    await session.flush()
    return outcome


async def _push_scheduled_result(session: Any, schedule: Schedule, body: str) -> None:
    """Best-effort out-of-app push for a scheduled task result (email/qq)."""
    try:
        if schedule.delivery_channel in ("email", "digest_email"):
            await build_email_sender().send(
                to="owner", subject=f"Sherpa: {schedule.name}", body=body
            )
        elif schedule.delivery_channel == "qq":
            config = await chan_svc.get_config(session, schedule.tenant_id, schedule.user_id, "qq")
            secret = chan_svc.reveal_secret(config) if config else None
            owner = getattr(config, "owner_external_id", None) if config else None
            if config and config.enabled and config.app_id and secret and owner:
                await build_qq_sender(config.app_id, secret).send_private(owner, body, None)
    except Exception:
        return


async def run_job(ctx: dict[str, Any], run_id: str) -> str:
    """Execute one durable run. v1 commits at settle; per-turn commit is a later refinement."""
    rid = uuid.UUID(run_id)
    # Phase 1: claim the run + take a liveness lease in an independent committed
    # transaction so the run is visibly "running" and a dead worker is detectable
    # as stale (ADR-029). The lease heartbeat then runs alongside execute_run.
    try:
        tenant_id, session_id = await claim_run_lease(rid, worker_identity())
    except LookupError:
        return "unknown_run"
    bind_context(
        tenant_id=str(tenant_id),
        run_id=str(rid),
        session_id=str(session_id) if session_id is not None else None,
    )
    async with run_heartbeat(rid):
        async with SessionLocal() as session:
            run = (await session.execute(select(Run).where(Run.id == rid))).scalar_one_or_none()
            if run is None:
                return "unknown_run"
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
                await _deliver_scheduled_task(rid)
                return reason
            except Exception:
                await session.rollback()
                await _settle_failed(tenant_id, rid, session_id)
                await _deliver_scheduled_task(rid)
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


async def drive_maintenance(ctx: dict[str, Any]) -> str:
    """Leader-gated: reclaim Drive bytes — GC unreferenced blobs + sweep orphans.

    Objects are content-addressed and never deleted inline (ADR-030). This job is
    the sole deleter: it removes objects for blobs whose ``ref_count = 0`` past the
    retention window, and sweeps objects with no blob row (crash after write, before
    commit). The reconcile is idempotent and convergent.
    """
    if not await try_acquire_leader("drive_maintenance", ttl_ms=280_000):
        return "not_leader"
    from app.services import drive as drive_svc

    async with SessionLocal() as session:
        gced = await drive_svc.gc_unreferenced_blobs(session)
        await session.commit()
    async with SessionLocal() as session:
        swept = await drive_svc.sweep_orphan_objects(session)
    return f"gc={gced} orphans={swept}"


async def agent_task_tick(ctx: dict[str, Any]) -> str:
    """Leader-gated: dispatch due `agent_task` firings as autonomous runs (ADR-031).

    Admits an idempotent `run_kind='scheduled_task'` run per due firing (slot key →
    admission id, so a replay never double-runs), then enqueues the run jobs. Result
    delivery + firing settle happens when the run settles.
    """
    if not await try_acquire_leader("agent_task_tick", ttl_ms=55_000):
        return "not_leader"
    return await _dispatch_agent_tasks()


async def agent_task_dispatch_job(ctx: dict[str, Any]) -> str:
    """One-shot immediate agent_task dispatch (Run Now; ADR-031 amendment).

    Not leader-gated — it dispatches the just-created firing right away so Run Now
    doesn't wait ~30s for the periodic tick. Idempotent (firing slot + run_id guard),
    so racing the tick is a no-op.
    """
    return await _dispatch_agent_tasks()


async def _dispatch_agent_tasks() -> str:
    async with SessionLocal() as session:
        run_ids = await dispatch_due_agent_tasks(session, datetime.datetime.now(datetime.UTC))
        await session.commit()
    for run_id in run_ids:
        await queue.enqueue_run(run_id)
    return f"dispatched={len(run_ids)}"


class WorkerSettings:
    functions = [
        ping,
        run_job,
        gmail_sync_job,
        sync_and_analyze_job,
        approval_resume_job,
        agent_task_dispatch_job,
    ]
    cron_jobs = [
        cron(scheduler_tick, second=0),
        cron(delivery_tick, second=15),
        cron(agent_task_tick, second={5, 35}),
        cron(periodic_connector_sync, minute=set(range(0, 60, 5))),
        cron(drive_maintenance, minute=set(range(0, 60, 10))),
    ]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
