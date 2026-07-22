"""files -> personal Drive migration (ADR-030, W1; P2.3)

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-23

Migrates existing flat ``files`` rows into the Personal Drive as first-class
``drive_nodes`` (folders + file), backed by reference-counted ``storage_blobs``
that reuse the file's existing object key (no byte copy). Version + content hash
are preserved. Legacy ``files`` rows are left intact so the old ``/files`` REST/
tool surface keeps working during the transition; both reference the same object.

Pure metadata copy over the sync bind — no object-store access needed. Object
keys are never exposed to clients.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_QUOTA = 5 * 1024 * 1024 * 1024  # 5 GiB (matches settings.drive_quota_bytes default)


def _split(path: str) -> list[str]:
    return [p for p in path.strip().strip("/").split("/") if p not in ("", ".", "..")]


def upgrade() -> None:
    bind = op.get_bind()
    files = list(
        bind.execute(
            sa.text(
                "SELECT tenant_id, id, user_id, path, object_key, size_bytes, "
                "content_type, content_hash, version, created_at, updated_at "
                "FROM files ORDER BY tenant_id, user_id, path"
            )
        ).mappings()
    )
    if not files:
        return

    owners: set[tuple[uuid.UUID, uuid.UUID]] = set()
    # (tenant, user, parent_id, name) -> folder id, to reuse created folders.
    folders: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, str], uuid.UUID] = {}

    for f in files:
        tenant_id, user_id = f["tenant_id"], f["user_id"]
        owners.add((tenant_id, user_id))
        segments = _split(f["path"]) or [str(f["id"])]
        folder_names, filename = segments[:-1], segments[-1]

        # Upsert the reference-counted blob (reuse the legacy object key; dedupe hash).
        bind.execute(
            sa.text(
                "INSERT INTO storage_blobs "
                "(tenant_id, user_id, content_hash, object_key, size_bytes, "
                " content_type, ref_count, unreferenced_at) "
                "VALUES (:t, :u, :h, :k, :s, :ct, 1, NULL) "
                "ON CONFLICT (tenant_id, user_id, content_hash) "
                "DO UPDATE SET ref_count = storage_blobs.ref_count + 1, "
                " unreferenced_at = NULL"
            ),
            {
                "t": tenant_id,
                "u": user_id,
                "h": f["content_hash"],
                "k": f["object_key"],
                "s": f["size_bytes"],
                "ct": f["content_type"],
            },
        )

        parent_id: uuid.UUID | None = None
        for name in folder_names:
            key = (tenant_id, user_id, parent_id, name)
            existing = folders.get(key)
            if existing is None:
                existing = bind.execute(
                    sa.text(
                        "SELECT id FROM drive_nodes WHERE tenant_id = :t AND user_id = :u "
                        "AND node_type = 'folder' AND name = :n "
                        "AND parent_id IS NOT DISTINCT FROM :p AND trashed_at IS NULL"
                    ),
                    {"t": tenant_id, "u": user_id, "n": name, "p": parent_id},
                ).scalar()
            if existing is None:
                existing = uuid.uuid4()
                bind.execute(
                    sa.text(
                        "INSERT INTO drive_nodes "
                        "(tenant_id, id, user_id, parent_id, node_type, name, size_bytes, "
                        " content_type, version) "
                        "VALUES (:t, :id, :u, :p, 'folder', :n, 0, "
                        " 'application/octet-stream', 1)"
                    ),
                    {"t": tenant_id, "id": existing, "u": user_id, "p": parent_id, "n": name},
                )
            folders[key] = existing
            parent_id = existing

        # Create the file node (skip if a sibling of that name already exists).
        clash = bind.execute(
            sa.text(
                "SELECT 1 FROM drive_nodes WHERE tenant_id = :t AND user_id = :u "
                "AND name = :n AND parent_id IS NOT DISTINCT FROM :p AND trashed_at IS NULL"
            ),
            {"t": tenant_id, "u": user_id, "n": filename, "p": parent_id},
        ).scalar()
        if clash:
            filename = f"{filename}-{str(f['id'])[:8]}"
        bind.execute(
            sa.text(
                "INSERT INTO drive_nodes "
                "(tenant_id, id, user_id, parent_id, node_type, name, content_hash, "
                " size_bytes, content_type, version, created_at, updated_at) "
                "VALUES (:t, :id, :u, :p, 'file', :n, :h, :s, :ct, :v, :ca, :ua)"
            ),
            {
                "t": tenant_id,
                "id": uuid.uuid4(),
                "u": user_id,
                "p": parent_id,
                "n": filename,
                "h": f["content_hash"],
                "s": f["size_bytes"],
                "ct": f["content_type"],
                "v": f["version"],
                "ca": f["created_at"],
                "ua": f["updated_at"],
            },
        )

    # Create/refresh each owner's storage account with recomputed used_bytes.
    for tenant_id, user_id in owners:
        used = bind.execute(
            sa.text(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM storage_blobs "
                "WHERE tenant_id = :t AND user_id = :u AND ref_count > 0"
            ),
            {"t": tenant_id, "u": user_id},
        ).scalar()
        bind.execute(
            sa.text(
                "INSERT INTO storage_accounts "
                "(tenant_id, user_id, quota_bytes, used_bytes, reserved_bytes, version) "
                "VALUES (:t, :u, :q, :used, 0, 1) "
                "ON CONFLICT (tenant_id, user_id) "
                "DO UPDATE SET used_bytes = :used, version = storage_accounts.version + 1"
            ),
            {"t": tenant_id, "u": user_id, "q": _DEFAULT_QUOTA, "used": int(used or 0)},
        )


def downgrade() -> None:
    # Drive rows are derived from files; drop them (blobs cascade via ref-count GC).
    op.execute("DELETE FROM drive_versions;")
    op.execute("DELETE FROM drive_nodes;")
    op.execute("DELETE FROM storage_blobs;")
    op.execute("DELETE FROM storage_accounts;")
