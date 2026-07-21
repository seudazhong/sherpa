"""Files subsystem: object storage for personal files (milestone 2)."""

from __future__ import annotations

from app.files.store import ObjectStore, build_object_store

__all__ = ["ObjectStore", "build_object_store"]
