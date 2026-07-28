"""Text embeddings for archival/RAG memory (ADR-032).

Decoupled from the chat provider via the `EMBEDDING_*` config. The default is a
Sherpa-bundled local model served by an `ollama` container (`bge-m3`, 1024-d),
keeping personal memory on-box (self-hosted ethos + ADR-019). `EMBEDDING_KIND=mock`
— the default for offline dev / tests — returns a deterministic hash-based
pseudo-vector so the pipeline runs without a network call (铁律: no real model
calls in tests) and identical text always maps to the same vector.
`EMBEDDING_KIND=openai_compatible` is an optional external `/v1/embeddings`
override (e.g. the litellm proxy).

Changing the model/dim is a full re-embed, not a toggle: `EMBEDDING_DIM` MUST equal
the `memory_passages.embedding` column width (migration 0026 sets it to 1024).
"""

from __future__ import annotations

import hashlib
import struct

import httpx

from app.config import settings


def _fake_embedding(text: str) -> list[float]:
    """Deterministic pseudo-vector from a hash (mock/offline/test mode)."""
    dim = settings.embedding_dim
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.sha256(f"{text}:{counter}".encode()).digest()
        for i in range(0, len(digest), 4):
            out.append(struct.unpack("<I", digest[i : i + 4])[0] / 0xFFFFFFFF - 0.5)
        counter += 1
    return out[:dim]


def _base_url() -> str:
    if not settings.embedding_base_url:
        raise RuntimeError("EMBEDDING_BASE_URL is required for real embeddings")
    return str(settings.embedding_base_url).rstrip("/")


async def _embed_ollama(texts: list[str]) -> list[list[float]]:
    """Bundled local ollama `/api/embed` (batch input, order preserved)."""
    async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
        resp = await client.post(
            f"{_base_url()}/api/embed",
            json={"model": settings.embedding_model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
    return [[float(x) for x in vec] for vec in data["embeddings"]]


async def _embed_openai_compatible(texts: list[str]) -> list[list[float]]:
    """External OpenAI-style `/v1/embeddings` override (advanced)."""
    headers = {"Authorization": f"Bearer {settings.embedding_api_key}"}
    async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
        resp = await client.post(
            f"{_base_url()}/v1/embeddings",
            json={"model": settings.embedding_model, "input": texts},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    items = sorted(data["data"], key=lambda d: int(d["index"]))
    return [[float(x) for x in d["embedding"]] for d in items]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts (order preserved)."""
    if not texts:
        return []
    kind = settings.embedding_kind
    if kind == "ollama":
        return await _embed_ollama(texts)
    if kind == "openai_compatible":
        return await _embed_openai_compatible(texts)
    return [_fake_embedding(t) for t in texts]


async def embed_one(text: str) -> list[float]:
    return (await embed_texts([text]))[0]
