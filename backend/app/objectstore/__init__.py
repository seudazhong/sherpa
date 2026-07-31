"""Object-store adapter subsystem: byte storage behind Drive, Knowledge and Projects.

This is the **storage adapter**, not a user-facing file feature. It was called
``app.files`` until ADR-046 O-14, which was actively misleading: the deleted legacy
``files`` stack (``app/services/files.py`` + ``app/api/files.py`` + ``file_*`` tools)
shared the name while being a completely different thing.
"""

from __future__ import annotations

from app.objectstore.store import Buffer, ObjectStore, build_object_store

__all__ = ["Buffer", "ObjectStore", "build_object_store"]
