"""Session search projection (ADR-029 P1)."""

from __future__ import annotations

from app.search.indexer import SearchHit, reindex_all, reindex_session, search

__all__ = ["SearchHit", "reindex_all", "reindex_session", "search"]
