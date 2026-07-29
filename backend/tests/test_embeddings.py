"""Embedding throughput contract (ADR-032): batching, bounded concurrency, retry.

Pure unit tests — the remote call is stubbed, so nothing touches the network. What
matters is the *contract* callers depend on: vectors come back in input order, the
input is split into bounded batches, a flaky backend is retried, a short response is
rejected loudly rather than silently misaligning chunks, and progress is reported.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.memory import embeddings


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend a real backend is configured (the mock kind skips batching entirely)."""
    monkeypatch.setattr(embeddings.settings, "embedding_kind", "ollama")
    monkeypatch.setattr(embeddings.settings, "embedding_base_url", "http://embed.invalid")
    monkeypatch.setattr(embeddings.settings, "embedding_batch_size", 3)
    monkeypatch.setattr(embeddings.settings, "embedding_concurrency", 2)
    monkeypatch.setattr(embeddings.settings, "embedding_max_retries", 3)


@pytest.mark.asyncio
async def test_batches_are_bounded_and_order_is_preserved(
    remote: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    async def fake_batch(client: Any, texts: list[str]) -> list[list[float]]:
        seen.append(list(texts))
        await asyncio.sleep(0)  # let the other in-flight batch interleave
        return [[float(len(t))] for t in texts]

    monkeypatch.setattr(embeddings, "_embed_batch", fake_batch)
    texts = [f"{'x' * (i + 1)}" for i in range(7)]

    out = await embeddings.embed_texts(texts)

    assert [len(b) for b in seen] == [3, 3, 1]
    # Order must follow the input, not batch completion order.
    assert out == [[float(len(t))] for t in texts]


@pytest.mark.asyncio
async def test_concurrency_is_capped(remote: None, monkeypatch: pytest.MonkeyPatch) -> None:
    in_flight = 0
    peak = 0

    async def fake_batch(client: Any, texts: list[str]) -> list[list[float]]:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return [[1.0] for _ in texts]

    monkeypatch.setattr(embeddings, "_embed_batch", fake_batch)

    await embeddings.embed_texts([f"t{i}" for i in range(12)])  # 4 batches, cap 2

    assert peak == 2


@pytest.mark.asyncio
async def test_progress_is_reported_up_to_the_total(
    remote: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_batch(client: Any, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    monkeypatch.setattr(embeddings, "_embed_batch", fake_batch)
    seen: list[tuple[int, int]] = []

    async def progress(done: int, total: int) -> None:
        seen.append((done, total))

    await embeddings.embed_texts([f"t{i}" for i in range(7)], progress=progress)

    assert {total for _, total in seen} == {7}
    assert max(done for done, _ in seen) == 7
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_a_flaky_batch_is_retried(remote: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings.asyncio, "sleep", _no_sleep)
    attempts = 0

    async def flaky(client: Any, texts: list[str]) -> list[list[float]]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("backend warming up")
        return [[1.0] for _ in texts]

    monkeypatch.setattr(embeddings, "_embed_batch", flaky)

    out = await embeddings.embed_texts(["only"])

    assert attempts == 3
    assert out == [[1.0]]


@pytest.mark.asyncio
async def test_exhausted_retries_raise_the_last_error(
    remote: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embeddings.asyncio, "sleep", _no_sleep)

    async def always_fails(client: Any, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("backend down")

    monkeypatch.setattr(embeddings, "_embed_batch", always_fails)

    with pytest.raises(RuntimeError, match="backend down"):
        await embeddings.embed_texts(["a", "b"])


@pytest.mark.asyncio
async def test_short_response_is_rejected(remote: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A backend returning fewer vectors than inputs must fail loudly — silently
    zipping them would attach the wrong vector to the wrong chunk."""

    async def short(client: Any, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts[:-1]]

    monkeypatch.setattr(embeddings, "_embed_ollama", short)

    with pytest.raises(RuntimeError, match="2 vectors for 3"):
        await embeddings._embed_batch(None, ["a", "b", "c"])


@pytest.mark.asyncio
async def test_mock_kind_stays_offline_and_deterministic() -> None:
    """The default (test/offline) kind must not batch or call out at all."""
    first = await embeddings.embed_texts(["stable", "text"])
    second = await embeddings.embed_texts(["stable", "text"])

    assert first == second
    assert len(first) == 2
    assert len(first[0]) == embeddings.settings.embedding_dim


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_one_failing_batch_does_not_poison_the_others(
    remote: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare `gather` propagates the first failure immediately, which exits the
    `async with` and closes the HTTP client while sibling batches are still in flight;
    those then die with "client has been closed" and burn their retries on a corpse.
    Every task must settle, and the caller must see the ORIGINAL error."""
    monkeypatch.setattr(embeddings.asyncio, "sleep", _no_sleep)
    started = 0
    closed_client_errors = 0

    async def batch(client: Any, texts: list[str]) -> list[list[float]]:
        nonlocal started, closed_client_errors
        started += 1
        if client is not None and client.is_closed:
            closed_client_errors += 1
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        if texts[0] == "doomed":
            raise TimeoutError("the real cause")
        await asyncio.sleep(0)
        return [[1.0] for _ in texts]

    monkeypatch.setattr(embeddings, "_embed_batch", batch)
    monkeypatch.setattr(embeddings.settings, "embedding_batch_size", 1)
    monkeypatch.setattr(embeddings.settings, "embedding_concurrency", 2)

    with pytest.raises(TimeoutError, match="the real cause"):
        await embeddings.embed_texts(["ok1", "doomed", "ok2", "ok3", "ok4"])

    assert closed_client_errors == 0, "the client was closed while batches were running"
    # After a hard failure no NEW batch work is started (fail fast, don't grind on).
    assert started < 5 + 1


@pytest.mark.asyncio
async def test_retry_log_names_the_exception_type(
    remote: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`str(httpx.ReadTimeout())` is empty, so logging only the message produced
    `error: ""` — useless when diagnosing a slow embedding backend."""
    import logging

    monkeypatch.setattr(embeddings.asyncio, "sleep", _no_sleep)

    async def timing_out(client: Any, texts: list[str]) -> list[list[float]]:
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(embeddings, "_embed_batch", timing_out)

    with caplog.at_level(logging.WARNING, logger="app.memory.embeddings"):
        with pytest.raises(httpx.ReadTimeout):
            await embeddings.embed_texts(["a"])

    assert any("ReadTimeout" in getattr(r, "error", "") for r in caplog.records)
