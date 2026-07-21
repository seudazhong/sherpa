"""Personal files REST (ADR-023: parity with the file_* agent tools).

Multipart upload, list, download, and delete over app.services.files. Downloads
are session-authenticated GETs (no CSRF); mutations require CSRF. Blobs stream
from object storage; object keys are never exposed.
"""

from __future__ import annotations

import datetime
import posixpath
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import RequestContext, require_context, require_csrf
from app.db import get_session
from app.models import File as FileModel
from app.services import CallerContext, ServiceError
from app.services import files as svc

router = APIRouter(tags=["files"])


def _caller(rc: RequestContext) -> CallerContext:
    return CallerContext(tenant_id=rc.tenant_id, user_id=rc.user_id, actor="user")


def _http(e: ServiceError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.code)


class FileItem(BaseModel):
    id: uuid.UUID
    path: str
    size_bytes: int
    content_type: str
    version: int
    updated_at: datetime.datetime


class FilePage(BaseModel):
    items: list[FileItem]


def _item(row: FileModel) -> FileItem:
    return FileItem(
        id=row.id,
        path=row.path,
        size_bytes=row.size_bytes,
        content_type=row.content_type,
        version=row.version,
        updated_at=row.updated_at,
    )


@router.get("/files")
async def list_files(
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FilePage:
    rows = await svc.list_files(db, _caller(ctx))
    return FilePage(items=[_item(r) for r in rows])


@router.post("/files", status_code=201)
async def upload_file(
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
    path: Annotated[str, Form()],
    upload: Annotated[UploadFile, File()],
) -> FileItem:
    data = await upload.read()
    try:
        row = await svc.put_file(
            db,
            _caller(ctx),
            path=path,
            data=data,
            content_type=upload.content_type or "application/octet-stream",
        )
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
    return _item(row)


@router.get("/files/{file_id}/content")
async def download_file(
    file_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    try:
        row, data = await svc.read_file(db, _caller(ctx), file_id=file_id)
    except ServiceError as e:
        raise _http(e) from None
    filename = posixpath.basename(row.path) or "download"
    return Response(
        content=data,
        media_type=row.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/files/{file_id}", status_code=204)
async def delete_file(
    file_id: uuid.UUID,
    ctx: Annotated[RequestContext, Depends(require_csrf)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    try:
        await svc.delete_file(db, _caller(ctx), file_id=file_id)
        await db.commit()
    except ServiceError as e:
        raise _http(e) from None
