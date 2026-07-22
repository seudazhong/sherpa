"""Object storage for personal files (ADR-012; milestone 2).

Pluggable backend: MinIO (S3-compatible) for the running stack, and an in-memory
backend for offline dev / tests. Object **keys are server-generated** (a uuid,
never the user's path) to avoid traversal; the ``files`` table maps a logical
per-user path → key. The MinIO client is synchronous, so calls run in a thread.
"""

from __future__ import annotations

import asyncio
import io
from typing import Protocol

from app.config import settings


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def list_keys(self, prefix: str = "") -> list[str]: ...


class MemoryObjectStore:
    """In-memory blob store for offline dev / tests."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self._blobs[key] = data

    async def get(self, key: str) -> bytes:
        if key not in self._blobs:
            raise KeyError(key)
        return self._blobs[key]

    async def delete(self, key: str) -> None:
        self._blobs.pop(key, None)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._blobs if k.startswith(prefix)]


class MinioObjectStore:
    """MinIO/S3-compatible object store; the bucket is created on first use."""

    def __init__(self) -> None:
        from minio import Minio

        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._bucket = settings.minio_bucket
        self._ensured = False

    def _ensure_bucket(self) -> None:
        if not self._ensured:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
            self._ensured = True

    def _put_sync(self, key: str, data: bytes, content_type: str) -> None:
        self._ensure_bucket()
        self._client.put_object(
            self._bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )

    def _get_sync(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return bytes(resp.read())
        finally:
            resp.close()
            resp.release_conn()

    def _delete_sync(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)

    def _list_sync(self, prefix: str) -> list[str]:
        self._ensure_bucket()
        return [
            obj.object_name
            for obj in self._client.list_objects(self._bucket, prefix=prefix, recursive=True)
            if obj.object_name is not None
        ]

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._put_sync, key, data, content_type)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get_sync, key)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete_sync, key)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._list_sync, prefix)


_memory_store = MemoryObjectStore()
_minio_store: MinioObjectStore | None = None


def build_object_store() -> ObjectStore:
    """Return the configured object store (minio for the stack; memory offline)."""
    global _minio_store
    if settings.storage_kind == "minio":
        if _minio_store is None:
            _minio_store = MinioObjectStore()
        return _minio_store
    return _memory_store
