"""Personal file workspace service (ADR-012; milestone 2).

Per-user files: the blob lives in object storage under a server-generated key; the
``files`` row holds the logical path + metadata. ``put_file`` upserts by path
(overwriting bumps the version and replaces the blob). Paths are normalized and
traversal is rejected. Own-data; the adapter owns the transaction.
"""

from __future__ import annotations

import hashlib
import posixpath
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.files import build_object_store
from app.models import File
from app.services.context import CallerContext
from app.services.errors import Invalid, NotFound

_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB per file (v1 cap)


def _require_user(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("file storage requires a user context")
    return ctx.user_id


def _normalize_path(path: str) -> str:
    candidate = path.strip().lstrip("/")
    norm = posixpath.normpath(candidate)
    if not norm or norm == "." or norm.startswith("..") or "/../" in norm:
        raise Invalid("invalid file path")
    if len(norm) > 1024:
        raise Invalid("file path too long")
    return norm


async def put_file(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    path: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> File:
    uid = _require_user(ctx)
    path = _normalize_path(path)
    if len(data) > _MAX_BYTES:
        raise Invalid(f"file exceeds {_MAX_BYTES} bytes")
    store = build_object_store()
    content_hash = hashlib.sha256(data).digest()
    key = f"{ctx.tenant_id}/{uid}/{uuid.uuid4().hex}"
    await store.put(key, data, content_type)

    existing = await db.scalar(
        select(File).where(File.tenant_id == ctx.tenant_id, File.user_id == uid, File.path == path)
    )
    if existing is None:
        row = File(
            tenant_id=ctx.tenant_id,
            id=uuid.uuid4(),
            user_id=uid,
            path=path,
            object_key=key,
            size_bytes=len(data),
            content_type=content_type,
            content_hash=content_hash,
        )
        db.add(row)
        await db.flush()
        return row
    old_key = existing.object_key
    existing.object_key = key
    existing.size_bytes = len(data)
    existing.content_type = content_type
    existing.content_hash = content_hash
    existing.version += 1
    await db.flush()
    await store.delete(old_key)
    return existing


async def _resolve(
    db: AsyncSession,
    ctx: CallerContext,
    uid: uuid.UUID,
    *,
    file_id: uuid.UUID | None,
    path: str | None,
) -> File:
    if file_id is not None:
        row = await db.get(File, (ctx.tenant_id, file_id))
        if row is None or row.user_id != uid:
            raise NotFound("file not found")
        return row
    if path is None:
        raise Invalid("file_id or path required")
    row = await db.scalar(
        select(File).where(
            File.tenant_id == ctx.tenant_id,
            File.user_id == uid,
            File.path == _normalize_path(path),
        )
    )
    if row is None:
        raise NotFound("file not found")
    return row


async def read_file(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    file_id: uuid.UUID | None = None,
    path: str | None = None,
) -> tuple[File, bytes]:
    uid = _require_user(ctx)
    row = await _resolve(db, ctx, uid, file_id=file_id, path=path)
    data = await build_object_store().get(row.object_key)
    return row, data


async def list_files(db: AsyncSession, ctx: CallerContext, *, limit: int = 200) -> list[File]:
    uid = _require_user(ctx)
    rows = (
        (
            await db.execute(
                select(File)
                .where(File.tenant_id == ctx.tenant_id, File.user_id == uid)
                .order_by(File.path)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def delete_file(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    file_id: uuid.UUID | None = None,
    path: str | None = None,
) -> None:
    uid = _require_user(ctx)
    row = await _resolve(db, ctx, uid, file_id=file_id, path=path)
    object_key = row.object_key
    await db.delete(row)
    await db.flush()
    await build_object_store().delete(object_key)
