"""Object storage adapter (ADR-030 content-addressed bytes).

Pluggable backend: MinIO (S3-compatible) for the running stack, and an in-memory
backend for offline dev / tests. Object **keys are server-generated** (a uuid, never a
user-supplied path) to avoid traversal; ``storage_blobs`` maps a content hash → key for
Drive, Knowledge, Projects and chat attachments. The MinIO client is synchronous, so
calls run in a thread.

**Two memory-shaped operations exist for the Projects sandbox path** (config §1.7 peak
model), because a project file can legitimately be hundreds of megabytes:

* ``put`` accepts any :class:`~collections.abc.Buffer` (``bytes``, ``bytearray``,
  ``memoryview``) and streams it **without materializing a second copy**. The obvious
  ``io.BytesIO(data)`` costs a full duplicate of the payload, which on a 500 MiB file is
  500 MiB of avoidable RSS.
* ``get_prefix`` reads only the first N bytes. It exists so a caller can classify an object
  (binary? diffable?) without pulling a 500 MiB blob in order to decide **not** to diff it.
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from typing import BinaryIO

from app.config import settings

#: Anything supporting the buffer protocol. Accepting this instead of ``bytes`` is what lets
#: the sandbox hand over the ``bytearray`` it already filled rather than copying it.
Buffer = bytes | bytearray | memoryview


class _BufferReader(io.RawIOBase):
    """A read-only binary stream over a buffer, with **no copy of the whole payload**.

    MinIO's ``put_object`` reads in bounded parts. Slicing a ``memoryview`` is a view, not a
    copy, so the only transient is whatever slice the client asks for — constant, rather
    than proportional to the object. The obvious ``io.BytesIO(data)`` instead duplicates the
    entire payload up front, which on a 500 MiB project file is 500 MiB of avoidable RSS.
    """

    def __init__(self, buf: Buffer) -> None:
        super().__init__()
        self._view = memoryview(buf).cast("B")
        self._pos = 0

    def __len__(self) -> int:
        return len(self._view)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readinto(self, b: Any) -> int:
        target = memoryview(b).cast("B")
        take = min(len(target), len(self._view) - self._pos)
        if take <= 0:
            return 0
        target[:take] = self._view[self._pos : self._pos + take]
        self._pos += take
        return take

    def close(self) -> None:
        if not self.closed:
            self._view.release()
        super().close()


class ObjectStore(Protocol):
    async def put(self, key: str, data: Buffer, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def get_prefix(self, key: str, length: int) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def list_keys(self, prefix: str = "") -> list[str]: ...


class MemoryObjectStore:
    """In-memory blob store for offline dev / tests."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def put(self, key: str, data: Buffer, content_type: str) -> None:
        # Stored objects are immutable, so a caller-owned bytearray must be snapshotted here
        # (the caller may reuse or release it). This is the one place the copy is required,
        # and it replaces — rather than adds to — the copy the caller used to make.
        self._blobs[key] = bytes(data)

    async def get(self, key: str) -> bytes:
        if key not in self._blobs:
            raise KeyError(key)
        return self._blobs[key]

    async def get_prefix(self, key: str, length: int) -> bytes:
        if key not in self._blobs:
            raise KeyError(key)
        return self._blobs[key][:length]

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

    def _put_sync(self, key: str, data: Buffer, content_type: str) -> None:
        self._ensure_bucket()
        reader = _BufferReader(data)
        try:
            self._client.put_object(
                self._bucket,
                key,
                # `_BufferReader` is a real `RawIOBase`; minio only ever calls `read(n)` on
                # it. The cast is to the stub's narrower `BinaryIO`, not a behaviour claim.
                cast("BinaryIO", reader),
                length=len(reader),
                content_type=content_type,
            )
        finally:
            reader.close()

    def _get_sync(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def _get_prefix_sync(self, key: str, length: int) -> bytes:
        # A ranged GET: the server sends only what we asked for, so classifying a 500 MiB
        # object costs `length` bytes on the wire and in memory, not 500 MiB.
        resp = self._client.get_object(self._bucket, key, offset=0, length=length)
        try:
            return resp.read()
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

    async def put(self, key: str, data: Buffer, content_type: str) -> None:
        await asyncio.to_thread(self._put_sync, key, data, content_type)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get_sync, key)

    async def get_prefix(self, key: str, length: int) -> bytes:
        return await asyncio.to_thread(self._get_prefix_sync, key, length)

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
