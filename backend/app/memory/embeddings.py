"""Text embeddings for archival/RAG memory (milestone 1c).

Uses the configured OpenAI-compatible provider's `/v1/embeddings` (the litellm
proxy → text-embedding-3-small, 1536 dims). In `mock` mode — or when no API key
is set (offline dev / tests) — a deterministic hash-based pseudo-embedding is
returned so the pipeline runs without a network call (铁律: no real model calls in
tests). The lexical/FTS retrieval branch still works there, and identical text
always maps to the same vector.
"""

from __future__ import annotations

import hashlib
import struct

import httpx

from app.config import settings


def _fake_embedding(text: str) -> list[float]:
    """Deterministic pseudo-vector from a hash (offline/test mode)."""
    dim = settings.embedding_dim
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.sha256(f"{text}:{counter}".encode()).digest()
        for i in range(0, len(digest), 4):
            out.append(struct.unpack("<I", digest[i : i + 4])[0] / 0xFFFFFFFF - 0.5)
        counter += 1
    return out[:dim]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts (order preserved)."""
    if not texts:
        return []
    if settings.provider_kind == "mock" or not settings.provider_api_key:
        return [_fake_embedding(t) for t in texts]
    headers = {"Authorization": f"Bearer {settings.provider_api_key}"}
    payload = {"model": settings.embedding_model, "input": texts}
    async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
        resp = await client.post(
            f"{settings.provider_base_url.rstrip('/')}/v1/embeddings",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    items = sorted(data["data"], key=lambda d: int(d["index"]))
    return [[float(x) for x in d["embedding"]] for d in items]


async def embed_one(text: str) -> list[float]:
    return (await embed_texts([text]))[0]
