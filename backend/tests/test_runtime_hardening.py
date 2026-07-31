"""Regression tests for the four blocking defects found reviewing Phase TR P3 HEAD 2287eb6.

Each section states the defect in terms of what an attacker or an operator could actually do
with it, because that is what decides whether a fix is strong enough:

1. **Egress could exhaust worker memory.** The old path buffered every chunk, joined a second
   full copy, wrapped it in ``BytesIO``, then called ``fh.read()`` on an untrusted member —
   allocating the whole thing *before* the size check. A container that wrote one huge file
   could OOM the worker. The tests here use a tripwire stream that raises if more bytes are
   pulled from the wire than the budget allows, so "bounded" is proven rather than asserted.
2. **Digest pinning was comments only.** Config, ``.env.example`` and compose all defaulted to
   the mutable tag ``sherpa-sandbox-runner:dev``, and the runtime accepted any image at all.
3. **Executable-bit-only changes vanished.** ``base_manifest`` stored content hashes, so
   ``chmod +x`` with identical bytes produced an empty delta.
4. **Every ``container.wait`` failure was reported as ``wall_timeout``.** A dead daemon told
   the user their command was too slow.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from typing import Any

import pytest

from app.config import settings
from app.sandbox import runtime as sbx
from app.sandbox.runtime import is_pinned_image_reference
from app.sandbox.transport import (
    TAR_STREAM_BUFSIZE,
    TarTransport,
    TransportError,
    WorkspaceFile,
)
from tests.fake_docker import FAKE_IMAGE_DIGEST, FakeSpec, patch_docker

# --------------------------------------------------------------------------------------
# Blocker 1 — bounded egress
# --------------------------------------------------------------------------------------


class _Tripwire:
    """A chunk iterator that refuses to hand over more than ``limit`` bytes.

    This is the whole point of the test: if the transport is genuinely bounded it stops
    pulling once the budget is blown, so the tripwire never fires. If it goes back to
    buffering the whole archive, the tripwire raises and the test fails loudly instead of
    quietly allocating a gigabyte on the CI box."""

    def __init__(self, chunks: list[bytes], *, limit: int) -> None:
        self._chunks = chunks
        self._limit = limit
        self.served = 0

    def __iter__(self) -> Any:
        for chunk in self._chunks:
            self.served += len(chunk)
            if self.served > self._limit:
                raise AssertionError(
                    f"transport pulled {self.served} bytes from the wire with a "
                    f"{self._limit}-byte allowance — it is buffering, not streaming"
                )
            yield chunk


class _StreamContainer:
    """Just enough container to drive ``TarTransport.egress``."""

    def __init__(self, chunks: Any) -> None:
        self._chunks = chunks

    def get_archive(self, path: str) -> tuple[Any, dict[str, Any]]:
        return iter(self._chunks), {"name": "work"}


def _tar_bytes(build: Any, *, mode: str = "w") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        build(tf)
    return buf.getvalue()


def _add_file(tf: tarfile.TarFile, name: str, data: bytes, *, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    tf.addfile(info, io.BytesIO(data))


def _chunked(raw: bytes, size: int = 64 * 1024) -> list[bytes]:
    return [raw[i : i + size] for i in range(0, len(raw), size)]


def test_egress_stops_pulling_from_the_wire_once_the_budget_is_blown() -> None:
    """The defect in its purest form: a multi-chunk archive larger than the cap must fail
    *during* the transfer, not after the whole thing is resident."""
    raw = _tar_bytes(lambda tf: _add_file(tf, "work/big.bin", b"z" * (4 * 1024 * 1024)))
    budget = 256 * 1024
    # Allowance = budget + the transport's fixed slack. Anything beyond that is buffering.
    tripwire = _Tripwire(_chunked(raw), limit=budget + 2 * 1024 * 1024)
    t = TarTransport(max_bytes=budget)
    with pytest.raises(TransportError) as ei:
        t.egress(_StreamContainer(tripwire))
    assert ei.value.code == "scratch_too_large"
    assert tripwire.served < len(raw), "the whole archive was pulled before failing"


def test_a_member_larger_than_the_budget_is_refused_before_it_is_allocated() -> None:
    """Decisive form: the member's payload is fully present on the wire, but the tripwire
    only allows the header plus `tarfile`'s constant read-ahead through. The declared size is
    checked against the remaining budget first, so the body is never pulled — if it is, the
    tripwire fires.

    The allowance is written in terms of ``TAR_STREAM_BUFSIZE`` on purpose: the invariant is
    that egress read-ahead is a **constant**, not a fraction of the archive."""
    payload = b"q" * (2 * 1024 * 1024)
    raw = _tar_bytes(lambda tf: _add_file(tf, "work/big.bin", payload))
    allowance = TAR_STREAM_BUFSIZE + 16 * 1024
    tripwire = _Tripwire(_chunked(raw, 8192), limit=allowance)
    t = TarTransport(max_bytes=1024)
    with pytest.raises(TransportError) as ei:
        t.egress(_StreamContainer(tripwire))
    assert ei.value.code == "scratch_too_large"
    assert tripwire.served <= allowance, "the oversized member body was read into memory"


def test_a_member_larger_than_the_budget_is_refused_from_a_materialized_archive() -> None:
    payload = b"q" * (2 * 1024 * 1024)
    raw = _tar_bytes(lambda tf: _add_file(tf, "work/big.bin", payload))
    t = TarTransport(max_bytes=1024)
    with pytest.raises(TransportError) as ei:
        t.expand(raw)
    assert ei.value.code == "scratch_too_large"


def test_a_member_that_lies_about_its_size_is_still_bounded() -> None:
    """``member.size`` is attacker-supplied metadata. The copy loop counts real bytes, so a
    header that understates the payload cannot smuggle it past the budget."""
    real = b"x" * (512 * 1024)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("work/liar.bin")
        info.size = len(real)
        tf.addfile(info, io.BytesIO(real))
    raw = buf.getvalue()
    # Rewrite the declared size to 1 byte, leaving the payload in place.
    tampered = bytearray(raw)
    header = tarfile.TarInfo("work/liar.bin")
    header.size = 1
    tampered[0:512] = header.tobuf()[0:512]

    t = TarTransport(max_bytes=64 * 1024)
    with pytest.raises(TransportError):
        t.expand(bytes(tampered))


def test_a_compressed_egress_archive_is_refused() -> None:
    """``r|`` is uncompressed-streaming only. Docker never returns a compressed archive, and
    accepting one would let a decompression bomb expand between two size checks."""
    raw = _tar_bytes(lambda tf: _add_file(tf, "work/a.txt", b"a" * 1024))
    gz = gzip.compress(raw)
    t = TarTransport(max_bytes=64 * 1024 * 1024)
    with pytest.raises(TransportError) as ei:
        t.expand(gz)
    assert ei.value.code in {"runtime_transport_failed", "path_escape", "scratch_too_large"}


def test_a_highly_compressible_bomb_cannot_expand_past_the_budget() -> None:
    """End to end version of the same property: 64 MiB of zeros compresses to almost nothing,
    so if compression were honoured this would sail past a small budget and materialize far
    more than the cap. It must be refused while still tiny on the wire."""
    raw = _tar_bytes(lambda tf: _add_file(tf, "work/zeros.bin", b"\0" * (64 * 1024 * 1024)))
    gz = gzip.compress(raw)
    assert len(gz) < 1024 * 1024  # the bomb really is small on the wire
    tripwire = _Tripwire(_chunked(gz), limit=4 * 1024 * 1024)
    t = TarTransport(max_bytes=1024 * 1024)
    with pytest.raises(TransportError):
        t.egress(_StreamContainer(tripwire))


def test_a_sparse_member_is_refused() -> None:
    """GNU encoding: a distinct member type."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("work/sparse.bin")
        info.type = tarfile.GNUTYPE_SPARSE
        info.size = 0
        tf.addfile(info)
    t = TarTransport(max_bytes=64 * 1024 * 1024)
    with pytest.raises(TransportError):
        t.expand(buf.getvalue())


def test_a_pax_sparse_member_is_refused() -> None:
    """PAX encoding, which the first fix missed entirely.

    A PAX sparse member keeps an ordinary ``REGTYPE`` header, so a ``type ==
    GNUTYPE_SPARSE`` check does not see it. Its ``size`` describes the *stored* extent while
    ``GNU.sparse.*`` describes what it expands to — exactly the mismatch the budget check
    must never be reasoning about. The 0.1 map is used here because tarfile parses it from
    the header, so the member really does reach our check."""
    payload = b"data"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("work/pax-sparse.bin")
        info.size = len(payload)
        info.pax_headers = {
            "GNU.sparse.name": "work/pax-sparse.bin",
            "GNU.sparse.map": "0,4",
            "GNU.sparse.size": str(1024 * 1024 * 1024),
        }
        tf.addfile(info, io.BytesIO(payload))
    t = TarTransport(max_bytes=64 * 1024 * 1024)
    with pytest.raises(TransportError) as ei:
        t.expand(buf.getvalue())
    assert ei.value.code == "path_escape"


def test_a_malformed_sparse_header_is_a_named_failure_not_a_crash() -> None:
    """tarfile raises ``ValueError`` — not ``TarError`` — on some malformed PAX sparse maps.
    An uncaught one would leave the sandbox boundary as a crash instead of an observation."""
    payload = b"data"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("work/broken-sparse.bin")
        info.size = len(payload)
        info.pax_headers = {
            "GNU.sparse.major": "1",
            "GNU.sparse.minor": "0",
            "GNU.sparse.name": "work/broken-sparse.bin",
            "GNU.sparse.realsize": str(1024 * 1024 * 1024),
        }
        tf.addfile(info, io.BytesIO(payload))
    t = TarTransport(max_bytes=64 * 1024 * 1024)
    with pytest.raises(TransportError) as ei:
        t.expand(buf.getvalue())
    assert ei.value.code in {"path_escape", "runtime_transport_failed"}


def test_the_sparse_detector_covers_every_encoding() -> None:
    """Unit-level cover for the three ways tarfile can present a sparse member, so a change
    to one branch cannot silently drop the others."""
    from app.sandbox.transport import _is_sparse

    gnu = tarfile.TarInfo("a")
    gnu.type = tarfile.GNUTYPE_SPARSE
    assert _is_sparse(gnu) is True

    attr = tarfile.TarInfo("b")
    attr.sparse = [(0, 10)]  # type: ignore[attr-defined]
    assert _is_sparse(attr) is True

    pax = tarfile.TarInfo("c")
    pax.pax_headers = {"GNU.sparse.realsize": "999999999"}
    assert _is_sparse(pax) is True

    plain = tarfile.TarInfo("d")
    plain.size = 4
    assert _is_sparse(plain) is False


def test_a_multi_chunk_archive_inside_the_budget_still_round_trips() -> None:
    """Bounding must not break the normal case: many wire chunks, one correct result."""
    payload = bytes(range(256)) * 4096  # 1 MiB, not compressible into nothing
    raw = _tar_bytes(lambda tf: _add_file(tf, "work/data.bin", payload))
    assert len(_chunked(raw)) > 1
    t = TarTransport(max_bytes=8 * 1024 * 1024)
    files = t.egress(_StreamContainer(_chunked(raw)))
    assert files["data.bin"].data == payload


def test_the_ingress_archive_is_capped_too() -> None:
    t = TarTransport(max_bytes=4096)
    with pytest.raises(TransportError) as ei:
        t.build({"big.bin": WorkspaceFile(b"y" * 8192)}, set())
    assert ei.value.code == "scratch_too_large"


def test_egress_peak_memory_stays_near_the_retained_workspace() -> None:
    """Aggregate shape: many medium members. Kept as a smoke test, but note it is **not**
    the worst case — see the single-large-member test below, which is what the first
    implementation actually failed."""
    import random
    import tracemalloc

    rng = random.Random(1234)
    payload = bytes(rng.getrandbits(8) for _ in range(256 * 1024))  # 256 KiB, incompressible
    n_members = 24
    total = len(payload) * n_members  # ~6 MiB

    def build(tf: tarfile.TarFile) -> None:
        for i in range(n_members):
            _add_file(tf, f"work/f{i:03d}.bin", payload)

    raw = _tar_bytes(build)
    chunks = _chunked(raw)  # built OUTSIDE the measurement: this is wire data, not our copy
    t = TarTransport(max_bytes=32 * 1024 * 1024)

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        files = t.egress(_StreamContainer(chunks))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(files) == n_members
    allowed = total + len(payload) * 2 + 2 * 1024 * 1024
    assert peak < allowed, f"peak {peak} exceeded {allowed} for a {total}-byte workspace"


#: Documented peak budget for egress, over and above the bytes actually retained.
#:
#: The drain loop's transients are constants, not fractions of the member:
#:   * ``COPY_CHUNK_BYTES`` (256 KiB) — the bytes object ``readinto`` allocates per read;
#:   * ``TAR_STREAM_BUFSIZE`` (32 KiB) — tarfile's stream read-ahead;
#:   * ``_WIRE_SLACK_BYTES`` (1 MiB) — what the wire reader may hold past the budget;
#:   * a ``BufferedReader`` buffer and interpreter overhead.
#: 2 MiB covers all of them with room to spare, and — crucially — it does **not** scale with
#: the member, which is the property under test.
_EGRESS_CONSTANT_OVERHEAD = 2 * 1024 * 1024


def _measure_egress_peak(member_size: int, *, retain_source: bool) -> tuple[int, int]:
    """Return ``(peak, retained)`` for a single-member egress of ``member_size`` bytes."""
    import random
    import tracemalloc

    rng = random.Random(99)
    block = bytes(rng.getrandbits(8) for _ in range(65536))  # incompressible
    payload = (block * (member_size // len(block) + 1))[:member_size]
    raw = _tar_bytes(lambda tf: _add_file(tf, "work/big.bin", payload))
    chunks = _chunked(raw)
    if not retain_source:
        del payload
    t = TarTransport(max_bytes=64 * 1024 * 1024)

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        files = t.egress(_StreamContainer(chunks))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(files["big.bin"].data) == member_size
    retained = member_size
    del files
    return peak, retained


def test_a_near_cap_single_member_does_not_create_full_size_transient_copies() -> None:
    """The worst case, and the one the first implementation failed.

    A single large member is where full-size transients show up; a workspace of many small
    members hides them. Measured before this fix, on this exact shape: **3.01x** the member
    — ``bytearray(size)``, plus the same-size bytes object ``tarfile.readinto`` allocates
    when handed a whole-member view, plus a final ``bytes(buf)`` copy. Three full-size
    allocations where one is needed.

    The fix reads in ``COPY_CHUNK_BYTES`` views (so ``readinto``'s transient is a constant)
    and **hands over ownership** of the filled ``bytearray`` instead of copying it. Peak is
    then the retained member plus a constant.
    """
    size = 8 * 1024 * 1024
    peak, retained = _measure_egress_peak(size, retain_source=False)
    allowed = retained + _EGRESS_CONSTANT_OVERHEAD
    assert peak < allowed, (
        f"peak {peak} ({peak / size:.2f}x the member) exceeded {allowed}; "
        "a full-size transient copy is back"
    )


def test_the_overhead_is_constant_rather_than_proportional_to_the_member() -> None:
    """Quadrupling the member must not quadruple the overhead.

    This is the property that actually distinguishes "one allocation plus a constant" from
    "N allocations": a ratio test alone passes for any N once the member is large enough, so
    the overhead itself is measured at two sizes and required not to grow with the member."""
    small, large = 4 * 1024 * 1024, 16 * 1024 * 1024
    peak_small, retained_small = _measure_egress_peak(small, retain_source=False)
    peak_large, retained_large = _measure_egress_peak(large, retain_source=False)
    overhead_small = peak_small - retained_small
    overhead_large = peak_large - retained_large
    assert overhead_large < overhead_small + _EGRESS_CONSTANT_OVERHEAD, (
        f"overhead grew with the member: {overhead_small} -> {overhead_large} "
        f"for {small} -> {large} bytes"
    )


def test_a_large_member_is_bounded_even_while_the_source_workspace_is_retained() -> None:
    """The reviewer's shape: the caller still holds the original bytes while egress runs.

    Peak is then source + result + constant, and must not be a *multiple* of either."""
    size = 8 * 1024 * 1024
    peak, retained = _measure_egress_peak(size, retain_source=True)
    allowed = retained * 2 + _EGRESS_CONSTANT_OVERHEAD
    assert peak < allowed, f"peak {peak} exceeded {allowed} with the source retained"


def test_the_transfer_cap_is_coherent_with_the_change_set_bound() -> None:
    """A 2 GiB transfer cap was incoherent: any change set that large is rejected downstream
    anyway, and the worker would have been asked to hold multiples of its own footprint."""
    assert settings.sandbox_scratch_max_bytes == 512 * 1024 * 1024
    assert settings.sandbox_scratch_max_bytes >= settings.working_copy_max_changed_bytes
    assert settings.sandbox_scratch_max_bytes <= 1024 * 1024 * 1024


# --------------------------------------------------------------------------------------
# Blocker 2 — the pinned-image contract is enforced, not documented
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "sha256:" + "0" * 64,
        "sherpa-sandbox-runner@sha256:" + "a" * 64,
        "ghcr.io/acme/runner@sha256:" + "f" * 64,
        # A registry with an explicit port is a legitimate immutable reference.
        "localhost:5000/sherpa-sandbox-runner@sha256:" + "b" * 64,
        "registry.internal:8443/team/sub/runner@sha256:" + "c" * 64,
    ],
)
def test_immutable_references_are_accepted(ref: str) -> None:
    assert is_pinned_image_reference(ref) is True


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "sherpa-sandbox-runner:dev",  # the exact default that used to ship
        "sherpa-sandbox-runner",
        "python:3.11-slim",
        "localhost:5000/runner:latest",  # a port does not make a tag immutable
        "sha256:tooshort",
        "sha256:" + "A" * 64,  # uppercase is not a valid digest
        "sha256:" + "0" * 63,
        "sha256:" + "0" * 65,
        "sha512:" + "0" * 64,  # only sha256 is accepted
        "sha256:" + "g" * 64,  # not hex
        "runner@sha256:" + "0" * 64 + " ",  # trailing junk (after strip this is fine)
    ][:-1],
)
def test_mutable_or_malformed_references_are_rejected(ref: str) -> None:
    assert is_pinned_image_reference(ref) is False


def test_allowing_registry_ports_did_not_weaken_the_digest_itself() -> None:
    """Widening the *name* half must not widen the *digest* half."""
    for bad in ("sha256:" + "0" * 63, "sha256:" + "0" * 65, "sha256:" + "Z" * 64, "sha256:"):
        assert is_pinned_image_reference(f"localhost:5000/runner@{bad}") is False


async def _tiny_ws() -> sbx.Workspace:
    data = b"x\n"

    async def _read(_h: bytes) -> bytes:
        return data

    return await sbx.materialize(
        [
            sbx.MaterializeEntry(
                "a.txt", "file", hashlib.sha256(data).digest(), len(data), False, None
            )
        ],
        _read,
    )


async def test_a_tag_is_refused_and_no_container_is_created(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The headline defect: the shipped default was a mutable tag, and the runtime ran it."""
    client = patch_docker(monkeypatch, FakeSpec(), image="sherpa-sandbox-runner:dev")
    assert client is not None
    out = await sbx.run_workspace(await _tiny_ws(), "true")
    assert out.result.error == sbx.RUNTIME_IMAGE_UNTRUSTED
    assert client.containers.container is None, "a container was created for an unpinned image"


async def test_an_unset_image_is_refused_with_an_actionable_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = patch_docker(monkeypatch, FakeSpec(), image="")
    assert client is not None
    out = await sbx.run_workspace(await _tiny_ws(), "true")
    assert out.result.error == sbx.RUNTIME_IMAGE_UNTRUSTED
    assert "SANDBOX_IMAGE" in (out.result.error_detail or "")
    assert client.containers.container is None


async def test_a_pinned_but_foreign_image_is_refused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A digest for some other image is refused.

    This is a **misconfiguration guard, not a provenance check** — the label it keys on is
    ordinary image metadata that anyone building an image can set. Its value is turning
    "operator pasted the wrong digest" into one clear refusal instead of a container with no
    /work volume and no tooling failing confusingly later. The actual trust root is that
    SANDBOX_IMAGE is an operator-chosen immutable digest, i.e. an allowlist of one."""
    client = patch_docker(
        monkeypatch,
        FakeSpec(image_labels={"org.opencontainers.image.title": "python"}),
    )
    assert client is not None
    out = await sbx.run_workspace(await _tiny_ws(), "true")
    assert out.result.error == sbx.RUNTIME_IMAGE_UNTRUSTED
    assert client.containers.container is None


def test_the_label_check_is_documented_as_forgeable_not_as_provenance() -> None:
    """Guard against the docs drifting back into claiming the label proves origin.

    An attacker who can get a hostile digest configured can equally set that digest's
    labels, so the check must never be described as authenticating the image."""
    doc = sbx.verify_runner_image.__doc__ or ""
    lowered = doc.lower()
    assert "forgeable" in lowered
    assert "not" in lowered and "provenance" in lowered
    assert "out of\n    scope" in lowered or "out of scope" in lowered.replace("\n    ", " ")


async def test_an_unlabelled_image_is_refused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = patch_docker(monkeypatch, FakeSpec(image_labels={}))
    assert client is not None
    out = await sbx.run_workspace(await _tiny_ws(), "true")
    assert out.result.error == sbx.RUNTIME_IMAGE_UNTRUSTED


async def test_a_missing_pinned_image_keeps_its_own_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """ "Not built yet" and "not allowed" are different operator problems and must not
    collapse into one name."""
    from docker.errors import ImageNotFound

    patch_docker(monkeypatch, FakeSpec(image_error=ImageNotFound("no such image")))
    out = await sbx.run_workspace(await _tiny_ws(), "true")
    assert out.result.error == sbx.RUNTIME_IMAGE_MISSING


async def test_an_api_error_while_inspecting_the_image_is_a_transport_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The daemon answered, and answered with an error — it is reachable. Reporting that as
    `runtime_daemon_unreachable` sent the operator to check the wrong thing."""
    from docker.errors import APIError

    patch_docker(monkeypatch, FakeSpec(image_error=APIError("500 server error")))
    out = await sbx.run_workspace(await _tiny_ws(), "true")
    assert out.result.error == sbx.RUNTIME_TRANSPORT_FAILED


async def test_a_connection_failure_while_inspecting_the_image_is_an_outage(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import requests.exceptions as rexc

    patch_docker(monkeypatch, FakeSpec(image_error=rexc.ConnectionError("refused")))
    out = await sbx.run_workspace(await _tiny_ws(), "true")
    assert out.result.error == sbx.RUNTIME_DAEMON_UNREACHABLE


async def test_the_approved_runner_is_allowed_through(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = patch_docker(monkeypatch, FakeSpec(stdout=b"ok\n"))
    assert client is not None
    out = await sbx.run_workspace(await _tiny_ws(), "true")
    assert out.result.error is None
    assert client.containers.create_image == FAKE_IMAGE_DIGEST
    assert client.images.requested == [FAKE_IMAGE_DIGEST]


def test_the_shipped_default_is_not_a_runnable_mutable_tag() -> None:
    """A fresh checkout must fail loudly rather than appear to work by running a tag."""
    from app.config import Settings

    assert is_pinned_image_reference(Settings().sandbox_image) is False


# --------------------------------------------------------------------------------------
# Blocker 3 — an executable-bit change is a change
# --------------------------------------------------------------------------------------


async def _ws_with(files: dict[str, tuple[bytes, bool]]) -> sbx.Workspace:
    blobs = {hashlib.sha256(d).digest(): d for d, _x in files.values()}

    async def _read(h: bytes) -> bytes:
        return blobs[h]

    entries = [
        sbx.MaterializeEntry(p, "file", hashlib.sha256(d).digest(), len(d), executable, None)
        for p, (d, executable) in files.items()
    ]
    return await sbx.materialize(entries, _read)


async def test_chmod_plus_x_with_identical_content_is_a_modification() -> None:
    """The defect: the baseline stored only a content hash, so `chmod +x` produced an empty
    delta and the mode change was silently dropped instead of reaching the change set."""
    ws = await _ws_with({"run.sh": (b"#!/bin/sh\necho hi\n", False)})
    result = {"run.sh": WorkspaceFile(data=ws.files["run.sh"].data, executable=True)}
    delta = sbx.compute_delta(ws, result)
    assert {e.path: e.change_kind for e in delta.entries} == {"run.sh": "modified"}
    assert delta.entries[0].executable is True


async def test_chmod_minus_x_with_identical_content_is_a_modification() -> None:
    ws = await _ws_with({"run.sh": (b"#!/bin/sh\n", True)})
    result = {"run.sh": WorkspaceFile(data=ws.files["run.sh"].data, executable=False)}
    delta = sbx.compute_delta(ws, result)
    assert {e.path: e.change_kind for e in delta.entries} == {"run.sh": "modified"}
    assert delta.entries[0].executable is False


async def test_an_untouched_executable_is_not_reported_as_changed() -> None:
    """The other half of the guarantee: comparing the bit must not invent phantom churn."""
    ws = await _ws_with({"run.sh": (b"#!/bin/sh\n", True), "a.txt": (b"a\n", False)})
    delta = sbx.compute_delta(ws, dict(ws.files))
    assert delta.entries == []


async def test_content_and_mode_changing_together_is_one_modification() -> None:
    ws = await _ws_with({"run.sh": (b"old\n", False)})
    result = {"run.sh": WorkspaceFile(data=b"new\n", executable=True)}
    delta = sbx.compute_delta(ws, result)
    assert {e.path: e.change_kind for e in delta.entries} == {"run.sh": "modified"}
    assert delta.entries[0].executable is True
    assert delta.entries[0].data == b"new\n"


def test_the_executable_bit_survives_a_tar_round_trip() -> None:
    t = TarTransport(max_bytes=1024 * 1024)
    raw = t.build({"run.sh": WorkspaceFile(b"#!/bin/sh\n", executable=True)}, set())
    back = t.expand(_reprefix(raw))
    assert back["run.sh"].executable is True


def _reprefix(raw: bytes) -> bytes:
    """Re-emit an ingress tar with the ``work/`` prefix docker's ``get_archive`` adds."""
    out = io.BytesIO()
    with (
        tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as src,
        tarfile.open(fileobj=out, mode="w") as dst,
    ):
        for m in src.getmembers():
            m.name = f"work/{m.name}"
            dst.addfile(m, src.extractfile(m) if m.isfile() else None)
    return out.getvalue()


# --------------------------------------------------------------------------------------
# Blocker 4 — only a real timeout is a wall_timeout
# --------------------------------------------------------------------------------------


def _docker_shaped_read_timeout() -> Exception:
    """Exactly what a real daemon produces on ``container.wait(timeout=N)``.

    Measured, not assumed: docker-py does not translate the timeout, so it surfaces as
    ``requests.exceptions.ConnectionError`` wrapping urllib3's ``ReadTimeoutError`` — the
    same class a genuinely unreachable daemon raises. That collision is why the old
    catch-everything branch mislabelled real outages as slow commands."""
    import requests.exceptions as rexc
    import urllib3.exceptions as uexc

    return rexc.ConnectionError(uexc.ReadTimeoutError(None, "npipe", "Read timed out."))  # type: ignore[arg-type]


async def test_a_real_read_timeout_is_a_wall_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = patch_docker(monkeypatch, FakeSpec(wait_error=_docker_shaped_read_timeout()))
    assert client is not None
    out = await sbx.run_workspace(await _tiny_ws(), "sleep 999")
    assert out.result.timed_out is True
    assert out.result.error is None
    assert client.containers.container is not None
    assert client.containers.container.killed


async def test_a_plain_read_timeout_class_is_also_a_wall_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import requests.exceptions as rexc

    patch_docker(monkeypatch, FakeSpec(wait_error=rexc.ReadTimeout("read timed out")))
    out = await sbx.run_workspace(await _tiny_ws(), "sleep 999")
    assert out.result.timed_out is True


async def test_a_dead_daemon_during_wait_is_not_a_wall_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The user-visible defect: the daemon dying mid-run told the user their command was too
    slow. It is an outage and must say so."""
    import requests.exceptions as rexc

    patch_docker(monkeypatch, FakeSpec(wait_error=rexc.ConnectionError("connection refused")))
    out = await sbx.run_workspace(await _tiny_ws(), "pytest -q")
    assert out.result.error == sbx.RUNTIME_DAEMON_UNREACHABLE
    assert out.result.timed_out is False


async def test_an_api_error_during_wait_is_a_transport_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from docker.errors import APIError

    patch_docker(monkeypatch, FakeSpec(wait_error=APIError("500 server error")))
    out = await sbx.run_workspace(await _tiny_ws(), "pytest -q")
    assert out.result.error == sbx.RUNTIME_TRANSPORT_FAILED
    assert out.result.timed_out is False


async def test_an_unmodelled_wait_failure_carries_its_class(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    patch_docker(monkeypatch, FakeSpec(wait_error=ValueError("boom")))
    out = await sbx.run_workspace(await _tiny_ws(), "pytest -q")
    assert out.result.error == "error:ValueError"
    assert out.result.timed_out is False


async def test_a_failed_wait_never_reports_a_fabricated_exit_code(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A misclassified wait used to return ``exit_code=-1, timed_out=True`` — which reads as
    "ran and was killed". A runtime fault must not look like a command outcome."""
    import requests.exceptions as rexc

    patch_docker(monkeypatch, FakeSpec(wait_error=rexc.ConnectionError("gone")))
    out = await sbx.run_workspace(await _tiny_ws(), "pytest -q")
    assert out.files is None
    assert out.result.error is not None


def test_the_timeout_detector_walks_the_exception_chain() -> None:
    import requests.exceptions as rexc

    assert sbx._is_read_timeout(_docker_shaped_read_timeout()) is True
    assert sbx._is_read_timeout(rexc.ReadTimeout("x")) is True
    assert sbx._is_read_timeout(TimeoutError("x")) is True
    assert sbx._is_read_timeout(rexc.ConnectionError("refused")) is False
    assert sbx._is_read_timeout(ValueError("nope")) is False


# --- connect timeouts are NOT wall timeouts ------------------------------------------
#
# Second-review defect: both requests.ConnectTimeout (which subclasses Timeout) and
# urllib3.ConnectTimeoutError (which subclasses urllib3's TimeoutError) satisfied a naive
# "is this a timeout?" test, so an outage that stalled while *connecting* was reported to the
# user as their command running too long. By the time container.wait is called this client
# has already created and started the container over the same pool, so failing to connect now
# is an outage, never a wall-clock expiry.


def _connect_timeout_cases() -> list[tuple[str, Exception]]:
    import requests.exceptions as rexc
    import urllib3.exceptions as uexc

    return [
        ("requests.ConnectTimeout", rexc.ConnectTimeout("connect timed out")),
        ("urllib3.ConnectTimeoutError", uexc.ConnectTimeoutError(None, "connect timed out")),
        (
            "requests.ConnectionError(urllib3.ConnectTimeoutError)",
            rexc.ConnectionError(uexc.ConnectTimeoutError(None, "connect timed out")),
        ),
        (
            "requests.ConnectTimeout(urllib3.ConnectTimeoutError)",
            rexc.ConnectTimeout(uexc.ConnectTimeoutError(None, "connect timed out")),
        ),
    ]


@pytest.mark.parametrize(
    "name,exc", _connect_timeout_cases(), ids=lambda v: getattr(v, "__name__", None) or str(v)[:40]
)
def test_a_connect_timeout_is_not_a_read_timeout(name: str, exc: Exception) -> None:
    assert sbx._is_read_timeout(exc) is False, f"{name} was treated as a wall-clock expiry"


@pytest.mark.parametrize(
    "name,exc", _connect_timeout_cases(), ids=lambda v: getattr(v, "__name__", None) or str(v)[:40]
)
async def test_a_connect_timeout_during_wait_is_an_outage_not_a_wall_timeout(  # type: ignore[no-untyped-def]
    name: str, exc: Exception, monkeypatch
) -> None:
    """End to end: the boundary must report an outage, not blame the command."""
    patch_docker(monkeypatch, FakeSpec(wait_error=exc))
    out = await sbx.run_workspace(await _tiny_ws(), "pytest -q")
    assert out.result.timed_out is False, f"{name} became a wall_timeout"
    assert out.result.error == sbx.RUNTIME_DAEMON_UNREACHABLE


def test_a_connect_timeout_nested_under_a_read_timeout_still_loses() -> None:
    """Order independence: a connect marker anywhere in the chain wins, so a chain that
    happens to carry both cannot be talked into `wall_timeout`."""
    import requests.exceptions as rexc
    import urllib3.exceptions as uexc

    exc = rexc.ConnectionError(uexc.ReadTimeoutError(None, "npipe", "read timed out"))
    assert sbx._is_read_timeout(exc) is True
    exc.__cause__ = uexc.ConnectTimeoutError(None, "connect timed out")
    assert sbx._is_read_timeout(exc) is False


def test_a_read_timeout_is_still_recognized_after_the_connect_exclusion() -> None:
    """The exclusion must not swing the other way and break genuine wall-clock expiry."""
    import requests.exceptions as rexc
    import urllib3.exceptions as uexc

    assert sbx._is_read_timeout(rexc.ReadTimeout("read timed out")) is True
    assert sbx._is_read_timeout(uexc.ReadTimeoutError(None, "npipe", "read timed out")) is True
    assert sbx._is_read_timeout(_docker_shaped_read_timeout()) is True


def test_the_timeout_detector_terminates_on_a_self_referential_chain() -> None:
    """Defensive: a cyclic ``__cause__`` must not hang the worker."""
    a = ValueError("a")
    b = ValueError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert sbx._is_read_timeout(a) is False
