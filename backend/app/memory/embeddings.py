"""Text embeddings for archival/RAG memory (ADR-032).

Decoupled from the chat provider via the `EMBEDDING_*` config. The default is a
Sherpa-bundled local model served by an `ollama` container (`bge-m3`, 1024-d) — or,
with a GPU, the same model served by a host-installed ollama reached over
`host.docker.internal` — keeping personal memory on-box (self-hosted ethos +
ADR-019). `EMBEDDING_KIND=mock`
— the default for offline dev / tests — returns a deterministic hash-based
pseudo-vector so the pipeline runs without a network call (铁律: no real model
calls in tests) and identical text always maps to the same vector.
`EMBEDDING_KIND=openai_compatible` is an optional external `/v1/embeddings`
override (e.g. the litellm proxy).

Changing the model/dim is a full re-embed, not a toggle: `EMBEDDING_DIM` MUST equal
the `memory_passages.embedding` column width (migration 0026 sets it to 1024).

Throughput: `embed_texts` splits its input into `EMBEDDING_BATCH_SIZE` batches and
keeps `EMBEDDING_CONCURRENCY` of them in flight over one shared connection pool, with
a bounded exponential-backoff retry per batch and an optional progress callback. A
whole document used to ride on a single request bounded by the *chat* provider's
timeout, which made large sources slow and — past 60s — fail outright.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
from collections.abc import Awaitable, Callable

import httpx

from app.config import settings

logger = logging.getLogger("app.memory.embeddings")

# (embedded_so_far, total) — awaited after each batch so callers can persist progress.
ProgressCallback = Callable[[int, int], Awaitable[None]]


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


async def _embed_ollama(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """Bundled local ollama `/api/embed` (batch input, order preserved)."""
    resp = await client.post(
        f"{_base_url()}/api/embed",
        json={"model": settings.embedding_model, "input": texts},
    )
    resp.raise_for_status()
    data = resp.json()
    return [[float(x) for x in vec] for vec in data["embeddings"]]


async def _embed_openai_compatible(
    client: httpx.AsyncClient, texts: list[str]
) -> list[list[float]]:
    """External OpenAI-style `/v1/embeddings` override (advanced)."""
    headers = {"Authorization": f"Bearer {settings.embedding_api_key}"}
    resp = await client.post(
        f"{_base_url()}/v1/embeddings",
        json={"model": settings.embedding_model, "input": texts},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    items = sorted(data["data"], key=lambda d: int(d["index"]))
    return [[float(x) for x in d["embedding"]] for d in items]


async def _embed_batch(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """One remote call for one batch, with an arity guard so a short response can never
    silently misalign vectors with their chunks."""
    if settings.embedding_kind == "ollama":
        vectors = await _embed_ollama(client, texts)
    else:
        vectors = await _embed_openai_compatible(client, texts)
    if len(vectors) != len(texts):
        raise RuntimeError(f"embedding backend returned {len(vectors)} vectors for {len(texts)}")
    return vectors


async def _embed_batch_with_retry(
    client: httpx.AsyncClient, texts: list[str], *, attempts: int
) -> list[list[float]]:
    """Bounded exponential-backoff retry for one batch; the last error is re-raised so
    the caller can turn it into a named exit."""
    attempts = max(1, attempts)
    delay = 0.5
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await _embed_batch(client, texts)
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised below
            last_error = exc
            if attempt >= attempts:
                break
            logger.warning(
                "embedding batch failed, retrying",
                extra={"attempt": attempt, "attempts": attempts, "error": str(exc)},
            )
            await asyncio.sleep(delay)
            delay *= 2
    assert last_error is not None
    raise last_error


async def embed_texts(
    texts: list[str], *, progress: ProgressCallback | None = None
) -> list[list[float]]:
    """Embed texts (order preserved), in bounded concurrent batches."""
    if not texts:
        return []
    total = len(texts)
    if settings.embedding_kind not in ("ollama", "openai_compatible"):
        vectors = [_fake_embedding(t) for t in texts]
        if progress is not None:
            await progress(total, total)
        return vectors

    size = max(1, settings.embedding_batch_size)
    concurrency = max(1, settings.embedding_concurrency)
    batches = [texts[i : i + size] for i in range(0, total, size)]
    results: list[list[list[float]]] = [[] for _ in batches]
    semaphore = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def run(index: int, batch: list[str], client: httpx.AsyncClient) -> None:
        nonlocal done
        async with semaphore:
            results[index] = await _embed_batch_with_retry(
                client, batch, attempts=settings.embedding_max_retries
            )
        async with lock:
            done += len(batch)
            embedded = done
        if progress is not None:
            await progress(embedded, total)

    async with httpx.AsyncClient(
        timeout=settings.embedding_timeout_seconds,
        limits=httpx.Limits(max_connections=concurrency),
    ) as client:
        await asyncio.gather(*(run(i, batch, client) for i, batch in enumerate(batches)))
    return [vector for batch in results for vector in batch]


async def embed_one(text: str) -> list[float]:
    return (await embed_texts([text]))[0]
