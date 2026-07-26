"""Knowledge ingestion internals (ADR-036): parsers + chunking (KB2a)."""

from __future__ import annotations

from app.knowledge.chunking import Chunk, chunk_document, estimate_tokens
from app.knowledge.parsers import (
    DocSection,
    NormalizedDoc,
    ParseError,
    detect_language,
    parse_document,
)

__all__ = [
    "Chunk",
    "chunk_document",
    "estimate_tokens",
    "DocSection",
    "NormalizedDoc",
    "ParseError",
    "detect_language",
    "parse_document",
]
