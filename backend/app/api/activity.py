"""Activity ledger + data controls (ADR-021).

`GET /activity` is the "what Sherpa did on my behalf" list (reads/inferences/
actions) projected from the append-only audit ledger. `GET /activity/export`
downloads a JSON bundle of imported + derived data. `POST /activity/delete-imported`
erases imported items and everything derived from them (the ledger is retained).
All tenant-scoped.
"""

from __future__ import annotations

import base64
import datetime
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ActivityPage, ActivityReceipt, DeleteImportedResult
from app.audit import delete_imported_data, export_imported_data
from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import AuditReceipt

router = APIRouter(tags=["activity"])


def _encode(occurred_at: datetime.datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{occurred_at.isoformat()}|{row_id}".encode()).decode()


def _decode(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, rid = raw.split("|", 1)
        return datetime.datetime.fromisoformat(ts), uuid.UUID(rid)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bad_cursor") from None


def _schema(row: AuditReceipt) -> ActivityReceipt:
    return ActivityReceipt(
        id=row.id,
        receipt_type=row.receipt_type,
        actor_type=row.actor_type,
        trigger_type=row.trigger_type,
        action=row.action,
        outcome=row.outcome,
        reversible=row.reversible,
        summary=row.summary_redacted,
        run_id=row.run_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        occurred_at=row.occurred_at,
    )


@router.get("/activity")
async def list_activity(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    receipt_type: Annotated[str | None, Query(alias="type")] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ActivityPage:
    stmt = (
        select(AuditReceipt)
        .where(AuditReceipt.tenant_id == ctx.tenant_id)
        .order_by(AuditReceipt.occurred_at.desc(), AuditReceipt.id.desc())
        .limit(limit + 1)
    )
    if receipt_type:
        stmt = stmt.where(AuditReceipt.receipt_type == receipt_type)
    if cursor:
        ts, rid = _decode(cursor)
        stmt = stmt.where(tuple_(AuditReceipt.occurred_at, AuditReceipt.id) < (ts, rid))
    rows = (await db.execute(stmt)).scalars().all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = _encode(last.occurred_at, last.id)
        rows = rows[:limit]
    return ActivityPage(items=[_schema(r) for r in rows], next_cursor=next_cursor)


@router.get("/activity/export")
async def export_activity(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    bundle = await export_imported_data(db, ctx.tenant_id)
    body = json.dumps(bundle, separators=(",", ":")).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="sherpa-export.json"'},
    )


@router.post("/activity/delete-imported")
async def delete_imported(
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DeleteImportedResult:
    counts = await delete_imported_data(db, ctx.tenant_id)
    await db.commit()
    return DeleteImportedResult(deleted=counts)
