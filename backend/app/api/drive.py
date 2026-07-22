"""Personal Drive REST surface (api.md §10.2; ADR-030 W1).

Thin adapter over ``app.services.drive`` — the same capability layer the agent
tools use (ADR-023). Downloads are session-authenticated GETs (no CSRF); mutations
require CSRF. ``DELETE`` (permanent purge) is human-only. Object keys are never
exposed; blobs are content-addressed + reference-counted server-side.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import DriveNode as DriveNodeModel
from app.models import DriveVersion as DriveVersionModel
from app.services import CallerContext, ServiceError
from app.services import drive as svc

router = APIRouter(tags=["drive"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


class DriveNode(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    node_type: Literal["folder", "file"]
    name: str
    size_bytes: int
    content_type: str
    version: int
    trashed: bool
    updated_at: datetime.datetime


class DriveNodePage(BaseModel):
    items: list[DriveNode]
    next_cursor: str | None


class DriveVersionOut(BaseModel):
    version: int
    size_bytes: int
    content_type: str
    created_at: datetime.datetime


class StorageAccountOut(BaseModel):
    quota_bytes: int
    used_bytes: int
    reserved_bytes: int
    trashed_bytes: int
    available_bytes: int


class FolderCreate(BaseModel):
    parent_id: uuid.UUID | None = None
    name: Annotated[str, Field(min_length=1, max_length=255)]


class NodeMove(BaseModel):
    if_version: int
    parent_id: uuid.UUID | None = None
    name: Annotated[str, Field(min_length=1, max_length=255)] | None = None


class RestoreVersion(BaseModel):
    version: int


def _node(row: DriveNodeModel) -> DriveNode:
    return DriveNode(
        id=row.id,
        parent_id=row.parent_id,
        node_type=row.node_type,  # type: ignore[arg-type]
        name=row.name,
        size_bytes=row.size_bytes,
        content_type=row.content_type,
        version=row.version,
        trashed=row.trashed_at is not None,
        updated_at=row.updated_at,
    )


def _version(row: DriveVersionModel) -> DriveVersionOut:
    return DriveVersionOut(
        version=row.version,
        size_bytes=row.size_bytes,
        content_type=row.content_type,
        created_at=row.created_at,
    )


@router.get("/drive/nodes")
async def list_nodes(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
    parent: uuid.UUID | None = None,
    query: str | None = None,
    sort: str = "name",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DriveNodePage:
    try:
        page = await svc.list_nodes(
            db,
            _caller(ctx),
            parent_id=parent,
            query=query,
            sort=sort,
            cursor=cursor,
            limit=limit,
        )
    except ServiceError as e:
        raise _http(e) from None
    return DriveNodePage(items=[_node(n) for n in page.items], next_cursor=page.next_cursor)


@router.get("/drive/storage")
async def storage(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> StorageAccountOut:
    s = await svc.storage_summary(db, _caller(ctx))
    return StorageAccountOut(
        quota_bytes=s.quota_bytes,
        used_bytes=s.used_bytes,
        reserved_bytes=s.reserved_bytes,
        trashed_bytes=s.trashed_bytes,
        available_bytes=s.available_bytes,
    )


@router.post("/drive/folders", status_code=201)
async def create_folder(
    body: FolderCreate,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DriveNode:
    try:
        node = await svc.create_folder(db, _caller(ctx), parent_id=body.parent_id, name=body.name)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return _node(node)


@router.post("/drive/files", status_code=201)
async def upload_file(
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
    upload: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
    parent_id: Annotated[str | None, Form()] = None,
) -> DriveNode:
    data = await upload.read()
    pid = uuid.UUID(parent_id) if parent_id else None
    fname = name or upload.filename or "upload"
    try:
        node = await svc.upload(
            db,
            _caller(ctx),
            parent_id=pid,
            name=fname,
            data=data,
            content_type=upload.content_type or "application/octet-stream",
        )
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return _node(node)


@router.get("/drive/nodes/{node_id}/content")
async def download(
    node_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    try:
        node, data = await svc.read_node(db, _caller(ctx), node_id)
    except ServiceError as e:
        raise _http(e) from None
    return Response(
        content=data,
        media_type=node.content_type,
        headers={"Content-Disposition": f'attachment; filename="{node.name}"'},
    )


@router.patch("/drive/nodes/{node_id}")
async def patch_node(
    node_id: uuid.UUID,
    body: NodeMove,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DriveNode:
    try:
        node = await svc.move(
            db,
            _caller(ctx),
            node_id,
            if_version=body.if_version,
            parent_id=body.parent_id,
            new_parent_given="parent_id" in body.model_fields_set,
            name=body.name,
        )
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return _node(node)


@router.get("/drive/nodes/{node_id}/versions")
async def versions(
    node_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> list[DriveVersionOut]:
    try:
        rows = await svc.list_versions(db, _caller(ctx), node_id)
    except ServiceError as e:
        raise _http(e) from None
    return [_version(r) for r in rows]


@router.post("/drive/nodes/{node_id}/restore-version")
async def restore_version(
    node_id: uuid.UUID,
    body: RestoreVersion,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DriveNode:
    try:
        node = await svc.restore_version(db, _caller(ctx), node_id, body.version)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return _node(node)


@router.post("/drive/nodes/{node_id}/trash")
async def trash(
    node_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DriveNode:
    try:
        node = await svc.trash(db, _caller(ctx), node_id)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return _node(node)


@router.post("/drive/nodes/{node_id}/restore")
async def restore(
    node_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DriveNode:
    try:
        node = await svc.restore(db, _caller(ctx), node_id)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return _node(node)


@router.delete("/drive/nodes/{node_id}", status_code=204)
async def purge(
    node_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    try:
        await svc.purge(db, _caller(ctx), node_id)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
