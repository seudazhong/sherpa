"""Memory subsystem: embeddings + archival/RAG passage retrieval (milestone 1c)."""

from __future__ import annotations

from app.memory.embeddings import embed_one, embed_texts

__all__ = ["embed_texts", "embed_one"]
