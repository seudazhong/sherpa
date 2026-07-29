"""Personal Drive service (ADR-030, Workspace W1).

Turns the flat ``files`` primitive into a Drive: folders + files as first-class
``drive_nodes``, immutable content-addressed ``storage_blobs`` (reference-counted,
deduped per user), retained ``drive_versions``, a per-user quota ``storage_account``,
and a trash (soft-delete + restore; permanent purge is human-only).

Cross-store ordering (fixes the old ``files`` put/delete-before-commit bug): the
object is content-addressed and written **before** commit and is **never** deleted
inline — a reconciliation/GC worker removes objects only once ``ref_count = 0`` past
retention (see ``app.worker`` / :func:`gc_unreferenced_blobs`). A crash after the
object write but before commit leaves an orphan object (no blob row) that the same
worker sweeps; no bytes are lost and quota never double-counts unchanged bytes.

Quota accounting: each owner counts each *distinct durable blob* once. Multiple
versions/nodes pointing at the same unchanged bytes do not double-count, and dedupe
credit never crosses the user/tenant boundary. ``used_bytes`` is recomputed from the
canonical ``storage_blobs`` rows after each mutation (single-user scale), so it is
provably consistent rather than incrementally drifting.

Every query is scoped by ``tenant_id`` AND ``user_id`` (ADR-015/029): cross-user
reads are structurally impossible. The adapter owns the transaction (services flush,
never commit).
"""

from __future__ import annotations

import base64
import dataclasses
import datetime
import hashlib
import uuid

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.files import build_object_store
from app.models import (
    DriveNode,
    DriveVersion,
    ProjectArtifact,
    ProjectSnapshotEntry,
    StorageAccount,
    StorageBlob,
)
from app.services.context import CallerContext
from app.services.errors import (
    Conflict,
    Forbidden,
    InsufficientStorage,
    Invalid,
    NotFound,
    TooLarge,
    VersionConflict,
)

_MAX_NAME = 255
_LIST_LIMIT = 200


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _require_user(ctx: CallerContext) -> uuid.UUID:
    if ctx.user_id is None:
        raise Invalid("drive requires a user context")
    return ctx.user_id


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > _MAX_NAME or "/" in cleaned:
        raise Invalid("invalid name")
    return cleaned


@dataclasses.dataclass(frozen=True)
class NodePage:
    items: list[DriveNode]
    next_cursor: str | None


@dataclasses.dataclass(frozen=True)
class StorageSummary:
    quota_bytes: int
    used_bytes: int
    reserved_bytes: int
    trashed_bytes: int
    available_bytes: int


@dataclasses.dataclass(frozen=True)
class VersionedContent:
    """The bytes of one pinned version of a Drive file (ADR-043 attachments)."""

    name: str
    content_type: str
    size_bytes: int
    version: int
    data: bytes


def _encode_cursor(is_file: int, name: str, nid: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{is_file}\x00{name}\x00{nid}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[int, str, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        is_file, name, nid = raw.split("\x00", 2)
        return int(is_file), name, uuid.UUID(nid)
    except Exception as exc:  # noqa: BLE001
        raise Invalid("bad_cursor") from exc


# --- storage account + blob accounting -------------------------------------


async def _get_account(db: AsyncSession, ctx: CallerContext, uid: uuid.UUID) -> StorageAccount:
    acct = await db.get(StorageAccount, (ctx.tenant_id, uid))
    if acct is None:
        acct = StorageAccount(
            tenant_id=ctx.tenant_id,
            user_id=uid,
            quota_bytes=settings.drive_quota_bytes,
            used_bytes=0,
            reserved_bytes=0,
            version=1,
        )
        db.add(acct)
        await db.flush()
    return acct


async def _recompute_blob(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, content_hash: bytes
) -> None:
    """Recompute a blob's ref_count from canonical node/version references."""
    blob = await db.get(StorageBlob, (ctx.tenant_id, uid, content_hash))
    if blob is None:
        return
    node_refs = await db.scalar(
        select(func.count())
        .select_from(DriveNode)
        .where(
            DriveNode.tenant_id == ctx.tenant_id,
            DriveNode.user_id == uid,
            DriveNode.content_hash == content_hash,
        )
    )
    version_refs = await db.scalar(
        select(func.count())
        .select_from(DriveVersion)
        .where(
            DriveVersion.tenant_id == ctx.tenant_id,
            DriveVersion.user_id == uid,
            DriveVersion.content_hash == content_hash,
        )
    )
    # Projects share the same immutable, deduped, ref-counted storage_blobs (ADR-037):
    # a blob referenced only by a project snapshot must NOT be GC'd or drop out of usage.
    project_refs = await db.scalar(
        select(func.count())
        .select_from(ProjectSnapshotEntry)
        .where(
            ProjectSnapshotEntry.tenant_id == ctx.tenant_id,
            ProjectSnapshotEntry.user_id == uid,
            ProjectSnapshotEntry.content_hash == content_hash,
        )
    )
    # W3 (ADR-040): a RETAINED project artifact keeps its blob referenced + counted
    # (ephemeral artifacts have retention='ephemeral' and do NOT charge quota).
    artifact_refs = await db.scalar(
        select(func.count())
        .select_from(ProjectArtifact)
        .where(
            ProjectArtifact.tenant_id == ctx.tenant_id,
            ProjectArtifact.user_id == uid,
            ProjectArtifact.content_hash == content_hash,
            ProjectArtifact.retention == "retained",
        )
    )
    total = (
        int(node_refs or 0)
        + int(version_refs or 0)
        + int(project_refs or 0)
        + int(artifact_refs or 0)
    )
    blob.ref_count = total
    if total == 0 and blob.unreferenced_at is None:
        blob.unreferenced_at = _now()
    elif total > 0:
        blob.unreferenced_at = None


async def _recompute_used(db: AsyncSession, ctx: CallerContext, uid: uuid.UUID) -> int:
    used = await db.scalar(
        select(func.coalesce(func.sum(StorageBlob.size_bytes), 0)).where(
            StorageBlob.tenant_id == ctx.tenant_id,
            StorageBlob.user_id == uid,
            StorageBlob.ref_count > 0,
        )
    )
    acct = await _get_account(db, ctx, uid)
    acct.used_bytes = int(used or 0)
    acct.version += 1
    return acct.used_bytes


async def _trashed_bytes(db: AsyncSession, ctx: CallerContext, uid: uuid.UUID) -> int:
    """Bytes held only by trashed nodes (reclaimable by purging the trash)."""
    live = (
        select(DriveNode.content_hash)
        .where(
            DriveNode.tenant_id == ctx.tenant_id,
            DriveNode.user_id == uid,
            DriveNode.node_type == "file",
            DriveNode.trashed_at.is_(None),
            DriveNode.content_hash.is_not(None),
        )
        .subquery()
    )
    trashed = (
        select(DriveNode.content_hash)
        .where(
            DriveNode.tenant_id == ctx.tenant_id,
            DriveNode.user_id == uid,
            DriveNode.node_type == "file",
            DriveNode.trashed_at.is_not(None),
            DriveNode.content_hash.is_not(None),
            DriveNode.content_hash.not_in(select(live.c.content_hash)),
        )
        .distinct()
        .subquery()
    )
    total = await db.scalar(
        select(func.coalesce(func.sum(StorageBlob.size_bytes), 0)).where(
            StorageBlob.tenant_id == ctx.tenant_id,
            StorageBlob.user_id == uid,
            StorageBlob.content_hash.in_(select(trashed.c.content_hash)),
        )
    )
    return int(total or 0)


async def storage_summary(db: AsyncSession, ctx: CallerContext) -> StorageSummary:
    uid = _require_user(ctx)
    acct = await _get_account(db, ctx, uid)
    trashed = await _trashed_bytes(db, ctx, uid)
    available = max(acct.quota_bytes - acct.used_bytes - acct.reserved_bytes, 0)
    return StorageSummary(
        quota_bytes=acct.quota_bytes,
        used_bytes=acct.used_bytes,
        reserved_bytes=acct.reserved_bytes,
        trashed_bytes=trashed,
        available_bytes=available,
    )


# --- node resolution --------------------------------------------------------


async def _get_node(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, node_id: uuid.UUID
) -> DriveNode:
    node = await db.get(DriveNode, (ctx.tenant_id, node_id))
    if node is None or node.user_id != uid:
        raise NotFound("node not found")
    return node


async def _sibling_taken(
    db: AsyncSession,
    ctx: CallerContext,
    uid: uuid.UUID,
    parent_id: uuid.UUID | None,
    name: str,
    *,
    exclude: uuid.UUID | None = None,
) -> bool:
    stmt = select(DriveNode.id).where(
        DriveNode.tenant_id == ctx.tenant_id,
        DriveNode.user_id == uid,
        DriveNode.name == name,
        DriveNode.trashed_at.is_(None),
    )
    stmt = (
        stmt.where(DriveNode.parent_id == parent_id)
        if parent_id is not None
        else stmt.where(DriveNode.parent_id.is_(None))
    )
    if exclude is not None:
        stmt = stmt.where(DriveNode.id != exclude)
    return (await db.scalar(stmt)) is not None


async def _assert_parent_folder(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, parent_id: uuid.UUID | None
) -> None:
    if parent_id is None:
        return
    parent = await _get_node(db, ctx, uid, parent_id)
    if parent.node_type != "folder":
        raise Invalid("parent is not a folder")
    if parent.trashed_at is not None:
        raise Conflict("parent is trashed")


# --- listing / browse -------------------------------------------------------


async def list_nodes(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    parent_id: uuid.UUID | None = None,
    query: str | None = None,
    sort: str = "name",
    cursor: str | None = None,
    limit: int = 50,
    trashed: bool = False,
) -> NodePage:
    uid = _require_user(ctx)
    limit = max(1, min(limit, _LIST_LIMIT))
    is_file = case((DriveNode.node_type == "file", 1), else_=0)
    stmt = select(DriveNode).where(
        DriveNode.tenant_id == ctx.tenant_id,
        DriveNode.user_id == uid,
    )
    if trashed:
        # Flat trash listing (across folders); only the top-most trashed nodes so a
        # trashed folder's children don't clutter the view.
        trashed_ids = (
            select(DriveNode.id)
            .where(
                DriveNode.tenant_id == ctx.tenant_id,
                DriveNode.user_id == uid,
                DriveNode.trashed_at.is_not(None),
            )
            .scalar_subquery()
        )
        stmt = stmt.where(
            DriveNode.trashed_at.is_not(None),
            (DriveNode.parent_id.is_(None)) | (DriveNode.parent_id.not_in(trashed_ids)),
        )
        if query and query.strip():
            stmt = stmt.where(DriveNode.name.ilike(f"%{query.strip()}%"))
    else:
        stmt = stmt.where(DriveNode.trashed_at.is_(None))
        if query and query.strip():
            stmt = stmt.where(DriveNode.name.ilike(f"%{query.strip()}%"))
        else:
            stmt = (
                stmt.where(DriveNode.parent_id == parent_id)
                if parent_id is not None
                else stmt.where(DriveNode.parent_id.is_(None))
            )
    # Folders first, then by name; stable tiebreak on id for keyset paging.
    order_name = DriveNode.name.desc() if sort == "-name" else DriveNode.name.asc()
    stmt = stmt.order_by(is_file.asc(), order_name, DriveNode.id.asc())
    if cursor:
        c_is_file, cname, cid = _decode_cursor(cursor)
        stmt = stmt.where(tuple_(is_file, DriveNode.name, DriveNode.id) > (c_is_file, cname, cid))
    rows = list((await db.execute(stmt.limit(limit + 1))).scalars().all())
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        last_is_file = 1 if last.node_type == "file" else 0
        next_cursor = _encode_cursor(last_is_file, last.name, last.id)
        rows = rows[:limit]
    return NodePage(items=rows, next_cursor=next_cursor)


async def get_node(db: AsyncSession, ctx: CallerContext, node_id: uuid.UUID) -> DriveNode:
    return await _get_node(db, ctx, _require_user(ctx), node_id)


# --- folders ----------------------------------------------------------------


async def create_folder(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    parent_id: uuid.UUID | None,
    name: str,
) -> DriveNode:
    uid = _require_user(ctx)
    name = _validate_name(name)
    await _assert_parent_folder(db, ctx, uid, parent_id)
    if await _sibling_taken(db, ctx, uid, parent_id, name):
        raise Conflict("name already exists")
    node = DriveNode(
        tenant_id=ctx.tenant_id,
        id=uuid.uuid4(),
        user_id=uid,
        parent_id=parent_id,
        node_type="folder",
        name=name,
    )
    db.add(node)
    await db.flush()
    await db.refresh(node, ["created_at", "updated_at"])
    return node


# --- upload / write ---------------------------------------------------------


async def _ensure_blob(
    db: AsyncSession,
    ctx: CallerContext,
    uid: uuid.UUID,
    *,
    data: bytes,
    content_type: str,
) -> tuple[bytes, bool]:
    """Content-address + persist the blob row; write the object before commit.

    Returns (content_hash, is_new_bytes). ``is_new_bytes`` is True only when this
    user did not already hold these exact bytes (dedupe), i.e. it consumes quota.
    """
    content_hash = hashlib.sha256(data).digest()
    existing = await db.get(StorageBlob, (ctx.tenant_id, uid, content_hash))
    is_new = existing is None or existing.ref_count == 0
    if existing is None:
        key = f"{ctx.tenant_id}/{uid}/{content_hash.hex()}"
        await build_object_store().put(key, data, content_type)
        db.add(
            StorageBlob(
                tenant_id=ctx.tenant_id,
                user_id=uid,
                content_hash=content_hash,
                object_key=key,
                size_bytes=len(data),
                content_type=content_type,
                ref_count=0,
            )
        )
        await db.flush()
    return content_hash, is_new


async def ensure_blob(
    db: AsyncSession,
    ctx: CallerContext,
    uid: uuid.UUID,
    *,
    data: bytes,
    content_type: str,
) -> tuple[bytes, bool]:
    """Public wrapper: content-address + persist a blob row (object written before
    commit). Shared by Projects (ADR-037) to reuse Drive's deduped blob store."""
    return await _ensure_blob(db, ctx, uid, data=data, content_type=content_type)


async def recompute_blob(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, content_hash: bytes
) -> None:
    """Public wrapper: recompute a blob's ref_count across Drive + Project references."""
    await _recompute_blob(db, ctx, uid, content_hash)


async def recompute_used(db: AsyncSession, ctx: CallerContext, uid: uuid.UUID) -> int:
    """Public wrapper: recompute the shared per-user account used_bytes."""
    return await _recompute_used(db, ctx, uid)


async def get_account(db: AsyncSession, ctx: CallerContext, uid: uuid.UUID) -> StorageAccount:
    """Public wrapper: get-or-create the shared per-user storage account."""
    return await _get_account(db, ctx, uid)


async def _upload_into(
    db: AsyncSession,
    ctx: CallerContext,
    uid: uuid.UUID,
    *,
    parent_id: uuid.UUID | None,
    name: str,
    data: bytes,
    content_type: str,
) -> DriveNode:
    if len(data) > settings.drive_max_file_bytes:
        raise TooLarge(f"file exceeds {settings.drive_max_file_bytes} bytes")
    name = _validate_name(name)
    await _assert_parent_folder(db, ctx, uid, parent_id)

    acct = await _get_account(db, ctx, uid)
    content_hash = hashlib.sha256(data).digest()
    already = await db.get(StorageBlob, (ctx.tenant_id, uid, content_hash))
    incoming = 0 if (already is not None and already.ref_count > 0) else len(data)
    if acct.used_bytes + acct.reserved_bytes + incoming > acct.quota_bytes:
        raise InsufficientStorage("quota exceeded")

    # Reserve → write object → persist rows → recompute usage (release reservation).
    if incoming:
        acct.reserved_bytes += incoming
        await db.flush()
    try:
        content_hash, _ = await _ensure_blob(db, ctx, uid, data=data, content_type=content_type)
    finally:
        if incoming:
            acct.reserved_bytes = max(acct.reserved_bytes - incoming, 0)

    existing = await db.scalar(
        select(DriveNode).where(
            DriveNode.tenant_id == ctx.tenant_id,
            DriveNode.user_id == uid,
            DriveNode.node_type == "file",
            DriveNode.name == name,
            DriveNode.parent_id == parent_id
            if parent_id is not None
            else DriveNode.parent_id.is_(None),
            DriveNode.trashed_at.is_(None),
        )
    )
    if existing is None:
        if await _sibling_taken(db, ctx, uid, parent_id, name):
            raise Conflict("name already exists")
        node = DriveNode(
            tenant_id=ctx.tenant_id,
            id=uuid.uuid4(),
            user_id=uid,
            parent_id=parent_id,
            node_type="file",
            name=name,
            content_hash=content_hash,
            size_bytes=len(data),
            content_type=content_type,
            version=1,
        )
        db.add(node)
        await db.flush()
    else:
        # Overwrite: retain the prior bytes as a version (still referenced ⇒ still
        # counted; unchanged bytes are not double-counted because dedupe by hash).
        prior_hash = existing.content_hash
        if prior_hash is not None and prior_hash != content_hash:
            db.add(
                DriveVersion(
                    tenant_id=ctx.tenant_id,
                    id=uuid.uuid4(),
                    node_id=existing.id,
                    user_id=uid,
                    version=existing.version,
                    content_hash=prior_hash,
                    size_bytes=existing.size_bytes,
                    content_type=existing.content_type,
                )
            )
        existing.content_hash = content_hash
        existing.size_bytes = len(data)
        existing.content_type = content_type
        existing.version += 1
        node = existing
        await db.flush()
        if prior_hash is not None and prior_hash != content_hash:
            await _recompute_blob(db, ctx, uid, prior_hash)
            # Knowledge hook: a changed backing file marks its sources stale (ADR-036).
            from app.services import knowledge as _knowledge

            await _knowledge.mark_stale_for_file(db, ctx, file_id=existing.id)

    await _recompute_blob(db, ctx, uid, content_hash)
    await _recompute_used(db, ctx, uid)
    await db.flush()
    await db.refresh(node, ["created_at", "updated_at"])
    return node


async def upload(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    parent_id: uuid.UUID | None,
    name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> DriveNode:
    uid = _require_user(ctx)
    return await _upload_into(
        db, ctx, uid, parent_id=parent_id, name=name, data=data, content_type=content_type
    )


# --- read -------------------------------------------------------------------


async def read_node(
    db: AsyncSession, ctx: CallerContext, node_id: uuid.UUID
) -> tuple[DriveNode, bytes]:
    uid = _require_user(ctx)
    node = await _get_node(db, ctx, uid, node_id)
    if node.node_type != "file" or node.content_hash is None:
        raise Invalid("not a file")
    blob = await db.get(StorageBlob, (ctx.tenant_id, uid, node.content_hash))
    if blob is None:
        raise NotFound("blob missing")
    data = await build_object_store().get(blob.object_key)
    return node, data


async def read_node_version(
    db: AsyncSession, ctx: CallerContext, node_id: uuid.UUID, version: int
) -> VersionedContent:
    """Read the bytes of a **specific** version of a file (ADR-043 attachments).

    Attachments pin the version at admission, so a later edit of the Drive file never
    silently rewrites what the model was shown. The current version reads from the node
    itself; older ones from the retained ``drive_versions`` row.
    """
    uid = _require_user(ctx)
    node = await _get_node(db, ctx, uid, node_id)
    if node.node_type != "file":
        raise Invalid("not a file")
    if version == node.version:
        if node.content_hash is None:
            raise NotFound("blob missing")
        content_hash, size, ctype = node.content_hash, node.size_bytes, node.content_type
    else:
        row = await db.scalar(
            select(DriveVersion).where(
                DriveVersion.tenant_id == ctx.tenant_id,
                DriveVersion.node_id == node_id,
                DriveVersion.version == version,
            )
        )
        if row is None:
            raise NotFound("version not found")
        content_hash, size, ctype = row.content_hash, row.size_bytes, row.content_type
    blob = await db.get(StorageBlob, (ctx.tenant_id, uid, content_hash))
    if blob is None:
        raise NotFound("blob missing")
    data = await build_object_store().get(blob.object_key)
    return VersionedContent(
        name=node.name, content_type=ctype, size_bytes=size, version=version, data=data
    )


# --- rename / move ----------------------------------------------------------


async def _is_descendant(
    db: AsyncSession,
    ctx: CallerContext,
    uid: uuid.UUID,
    candidate: uuid.UUID,
    ancestor: uuid.UUID,
) -> bool:
    cur: uuid.UUID | None = candidate
    seen: set[uuid.UUID] = set()
    while cur is not None and cur not in seen:
        if cur == ancestor:
            return True
        seen.add(cur)
        parent = await db.scalar(
            select(DriveNode.parent_id).where(
                DriveNode.tenant_id == ctx.tenant_id, DriveNode.id == cur
            )
        )
        cur = parent
    return False


async def move(
    db: AsyncSession,
    ctx: CallerContext,
    node_id: uuid.UUID,
    *,
    if_version: int,
    parent_id: uuid.UUID | None = None,
    new_parent_given: bool = False,
    name: str | None = None,
) -> DriveNode:
    uid = _require_user(ctx)
    node = await _get_node(db, ctx, uid, node_id)
    if node.trashed_at is not None:
        raise Conflict("node is trashed")
    if node.version != if_version:
        raise VersionConflict("stale version")

    target_parent = parent_id if new_parent_given else node.parent_id
    target_name = _validate_name(name) if name is not None else node.name

    if new_parent_given and target_parent is not None:
        if target_parent == node.id:
            raise Invalid("cannot move into itself")
        await _assert_parent_folder(db, ctx, uid, target_parent)
        if node.node_type == "folder" and await _is_descendant(
            db, ctx, uid, target_parent, node.id
        ):
            raise Invalid("cannot move a folder into its own subtree")

    if (target_parent != node.parent_id or target_name != node.name) and await _sibling_taken(
        db, ctx, uid, target_parent, target_name, exclude=node.id
    ):
        raise Conflict("name already exists")

    node.parent_id = target_parent
    node.name = target_name
    node.version += 1
    await db.flush()
    await db.refresh(node, ["updated_at"])
    return node


# --- versions ---------------------------------------------------------------


async def list_versions(
    db: AsyncSession, ctx: CallerContext, node_id: uuid.UUID
) -> list[DriveVersion]:
    uid = _require_user(ctx)
    await _get_node(db, ctx, uid, node_id)
    rows = (
        (
            await db.execute(
                select(DriveVersion)
                .where(
                    DriveVersion.tenant_id == ctx.tenant_id,
                    DriveVersion.node_id == node_id,
                )
                .order_by(DriveVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def restore_version(
    db: AsyncSession, ctx: CallerContext, node_id: uuid.UUID, version: int
) -> DriveNode:
    uid = _require_user(ctx)
    node = await _get_node(db, ctx, uid, node_id)
    if node.node_type != "file":
        raise Invalid("not a file")
    target = await db.scalar(
        select(DriveVersion).where(
            DriveVersion.tenant_id == ctx.tenant_id,
            DriveVersion.node_id == node_id,
            DriveVersion.version == version,
        )
    )
    if target is None:
        raise NotFound("version not found")

    prior_hash = node.content_hash
    if prior_hash is not None and prior_hash != target.content_hash:
        db.add(
            DriveVersion(
                tenant_id=ctx.tenant_id,
                id=uuid.uuid4(),
                node_id=node.id,
                user_id=uid,
                version=node.version,
                content_hash=prior_hash,
                size_bytes=node.size_bytes,
                content_type=node.content_type,
            )
        )
    node.content_hash = target.content_hash
    node.size_bytes = target.size_bytes
    node.content_type = target.content_type
    node.version += 1
    await db.flush()
    if prior_hash is not None and prior_hash != target.content_hash:
        await _recompute_blob(db, ctx, uid, prior_hash)
    await _recompute_blob(db, ctx, uid, target.content_hash)
    await _recompute_used(db, ctx, uid)
    await db.flush()
    await db.refresh(node, ["updated_at"])
    return node


# --- trash / restore / purge ------------------------------------------------


async def _subtree_ids(
    db: AsyncSession, ctx: CallerContext, uid: uuid.UUID, root_id: uuid.UUID
) -> list[uuid.UUID]:
    ids = [root_id]
    frontier = [root_id]
    while frontier:
        children = (
            (
                await db.execute(
                    select(DriveNode.id).where(
                        DriveNode.tenant_id == ctx.tenant_id,
                        DriveNode.user_id == uid,
                        DriveNode.parent_id.in_(frontier),
                    )
                )
            )
            .scalars()
            .all()
        )
        frontier = list(children)
        ids.extend(frontier)
    return ids


async def trash(db: AsyncSession, ctx: CallerContext, node_id: uuid.UUID) -> DriveNode:
    uid = _require_user(ctx)
    node = await _get_node(db, ctx, uid, node_id)
    if node.trashed_at is not None:
        return node
    now = _now()
    purge_after = now + datetime.timedelta(days=settings.drive_trash_retention_days)
    ids = await _subtree_ids(db, ctx, uid, node_id) if node.node_type == "folder" else [node_id]
    for nid in ids:
        n = await db.get(DriveNode, (ctx.tenant_id, nid))
        if n is not None and n.trashed_at is None:
            n.trashed_at = now
            n.purge_after = purge_after
    await db.flush()
    # Knowledge hook: a deleted file tombstones its sources (retrieval exclusion; ADR-036).
    from app.services import knowledge as _knowledge

    await _knowledge.tombstone_sources_for_files(db, ctx, file_ids=ids)
    await db.refresh(node, ["updated_at"])
    return node


async def restore(db: AsyncSession, ctx: CallerContext, node_id: uuid.UUID) -> DriveNode:
    uid = _require_user(ctx)
    node = await _get_node(db, ctx, uid, node_id)
    if node.trashed_at is None:
        return node
    # The parent must exist and be live, else restore to root.
    if node.parent_id is not None:
        parent = await db.get(DriveNode, (ctx.tenant_id, node.parent_id))
        if parent is None or parent.trashed_at is not None:
            node.parent_id = None
    if await _sibling_taken(db, ctx, uid, node.parent_id, node.name, exclude=node.id):
        raise Conflict("a live sibling with that name exists")
    ids = await _subtree_ids(db, ctx, uid, node_id) if node.node_type == "folder" else [node_id]
    for nid in ids:
        n = await db.get(DriveNode, (ctx.tenant_id, nid))
        if n is not None:
            n.trashed_at = None
            n.purge_after = None
    await db.flush()
    await db.refresh(node, ["updated_at"])
    return node


async def purge(db: AsyncSession, ctx: CallerContext, node_id: uuid.UUID) -> None:
    """Permanent delete (human-only; agents may trash/restore but not purge)."""
    uid = _require_user(ctx)
    if ctx.actor == "agent":
        raise Forbidden("purge is human-only")
    node = await _get_node(db, ctx, uid, node_id)
    ids = await _subtree_ids(db, ctx, uid, node_id) if node.node_type == "folder" else [node_id]
    hashes: set[bytes] = set()
    # Delete leaves first so parent FK (ON DELETE RESTRICT) is satisfied.
    for nid in reversed(ids):
        n = await db.get(DriveNode, (ctx.tenant_id, nid))
        if n is None:
            continue
        if n.content_hash is not None:
            hashes.add(n.content_hash)
        vers = (
            (
                await db.execute(
                    select(DriveVersion.content_hash).where(
                        DriveVersion.tenant_id == ctx.tenant_id, DriveVersion.node_id == nid
                    )
                )
            )
            .scalars()
            .all()
        )
        hashes.update(h for h in vers if h is not None)
        await db.delete(n)
    await db.flush()
    # Knowledge hook: purged files tombstone their sources (ADR-036).
    from app.services import knowledge as _knowledge

    await _knowledge.tombstone_sources_for_files(db, ctx, file_ids=ids)
    for h in hashes:
        await _recompute_blob(db, ctx, uid, h)
    await _recompute_used(db, ctx, uid)
    await db.flush()


# --- path helpers for the agent tools --------------------------------------


def _split_path(path: str) -> list[str]:
    parts = [p for p in path.strip().strip("/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise Invalid("invalid path")
    return parts


async def _resolve_folder(
    db: AsyncSession,
    ctx: CallerContext,
    uid: uuid.UUID,
    segments: list[str],
    *,
    create: bool,
) -> uuid.UUID | None:
    parent: uuid.UUID | None = None
    for seg in segments:
        seg = _validate_name(seg)
        child = await db.scalar(
            select(DriveNode).where(
                DriveNode.tenant_id == ctx.tenant_id,
                DriveNode.user_id == uid,
                DriveNode.name == seg,
                DriveNode.node_type == "folder",
                DriveNode.trashed_at.is_(None),
                DriveNode.parent_id == parent
                if parent is not None
                else DriveNode.parent_id.is_(None),
            )
        )
        if child is None:
            if not create:
                raise NotFound(f"folder not found: {seg}")
            created = await create_folder(db, ctx, parent_id=parent, name=seg)
            parent = created.id
        else:
            parent = child.id
    return parent


async def resolve_file_by_path(db: AsyncSession, ctx: CallerContext, path: str) -> DriveNode:
    uid = _require_user(ctx)
    parts = _split_path(path)
    if not parts:
        raise Invalid("path required")
    folder_id = await _resolve_folder(db, ctx, uid, parts[:-1], create=False)
    node = await db.scalar(
        select(DriveNode).where(
            DriveNode.tenant_id == ctx.tenant_id,
            DriveNode.user_id == uid,
            DriveNode.name == parts[-1],
            DriveNode.trashed_at.is_(None),
            DriveNode.parent_id == folder_id
            if folder_id is not None
            else DriveNode.parent_id.is_(None),
        )
    )
    if node is None:
        raise NotFound("path not found")
    return node


async def write_path(
    db: AsyncSession,
    ctx: CallerContext,
    *,
    path: str,
    data: bytes,
    content_type: str = "text/plain; charset=utf-8",
) -> DriveNode:
    uid = _require_user(ctx)
    parts = _split_path(path)
    if not parts:
        raise Invalid("path required")
    folder_id = await _resolve_folder(db, ctx, uid, parts[:-1], create=True)
    return await _upload_into(
        db, ctx, uid, parent_id=folder_id, name=parts[-1], data=data, content_type=content_type
    )


async def node_path(db: AsyncSession, ctx: CallerContext, node: DriveNode) -> str:
    names = [node.name]
    cur = node.parent_id
    while cur is not None:
        parent = await db.get(DriveNode, (ctx.tenant_id, cur))
        if parent is None:
            break
        names.append(parent.name)
        cur = parent.parent_id
    return "/".join(reversed(names))


# --- reconciliation / GC (worker) ------------------------------------------


async def gc_unreferenced_blobs(db: AsyncSession) -> int:
    """Delete objects + rows for blobs unreferenced past retention (all tenants)."""
    cutoff = _now() - datetime.timedelta(hours=settings.drive_blob_gc_retention_hours)
    rows = (
        (
            await db.execute(
                select(StorageBlob).where(
                    StorageBlob.ref_count == 0,
                    StorageBlob.unreferenced_at.is_not(None),
                    StorageBlob.unreferenced_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    store = build_object_store()
    removed = 0
    for blob in rows:
        await store.delete(blob.object_key)
        await db.delete(blob)
        removed += 1
    await db.flush()
    return removed


async def sweep_orphan_objects(db: AsyncSession) -> int:
    """Delete store objects that have no blob row (crash after write, before commit).

    Legacy ``files`` object keys are still live during the transition (ADR-030), so
    they are kept in the known set and never swept.
    """
    from app.models import File

    store = build_object_store()
    keys = await store.list_keys("")
    if not keys:
        return 0
    known = set((await db.execute(select(StorageBlob.object_key))).scalars().all())
    known.update((await db.execute(select(File.object_key))).scalars().all())
    removed = 0
    for key in keys:
        # Project archive-import staging objects (ADR-037) are transient job inputs,
        # not blob rows; the import job owns their lifecycle. Never sweep them here.
        if key.startswith("project-import/"):
            continue
        if key not in known:
            await store.delete(key)
            removed += 1
    return removed
