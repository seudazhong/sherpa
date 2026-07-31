"""A fake docker client for the sandbox tests (Phase TR P3).

The real container path is exercised by the ``docker``-marked lane
(``tests/test_runtime_docker.py``, ``uv run pytest -m docker``). This fake covers everything
that lane cannot run in CI: the tar round trip, every named failure branch, and the
credential canary — deterministically and offline.

It models the *actual* API surface :mod:`app.sandbox.runtime` uses —
``containers.create`` → ``put_archive`` → ``start`` → ``wait`` → ``reload`` → ``logs`` →
``get_archive`` → ``remove`` — so a change to that call sequence breaks the fake instead of
silently passing. It also **records the create kwargs**, which is what lets a test assert
that no bind mount and no host path is ever passed to the daemon.
"""

from __future__ import annotations

import dataclasses
import io
import tarfile
from collections.abc import Callable, Iterator
from typing import Any

from app.sandbox.transport import WORK_DIR, WorkspaceFile

#: A syntactically valid, digest-pinned reference for the fake runner. Tests that exercise
#: the happy path must use a pinned reference, because the real code now refuses anything
#: else before it creates a container (config §1.7, enforced in `verify_runner_image`).
FAKE_IMAGE_DIGEST = "sha256:" + "ab" * 32

#: What the first-party runner image is required to advertise about itself.
RUNNER_LABELS = {
    "org.opencontainers.image.title": "sherpa-sandbox-runner",
    "sherpa.capabilities": "python,pytest,ruff",
}


def tar_to_files(raw: bytes, *, prefix: str = "") -> dict[str, WorkspaceFile]:
    """Expand a tar into ``{path: WorkspaceFile}`` (test-side, no validation)."""
    out: dict[str, WorkspaceFile] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            name = m.name[len(prefix) :] if prefix and m.name.startswith(prefix) else m.name
            fh = tf.extractfile(m)
            out[name] = WorkspaceFile(
                data=fh.read() if fh is not None else b"", executable=bool(m.mode & 0o111)
            )
    return out


def files_to_tar(files: dict[str, WorkspaceFile], *, prefix: str = "work/") -> bytes:
    """Build a ``get_archive``-shaped tar (members carry the ``work/`` prefix)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        root = tarfile.TarInfo(prefix.rstrip("/"))
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tf.addfile(root)
        for path in sorted(files):
            f = files[path]
            info = tarfile.TarInfo(prefix + path)
            info.size = len(f.data)
            info.mode = 0o755 if f.executable else 0o644
            tf.addfile(info, io.BytesIO(f.data))
    return buf.getvalue()


@dataclasses.dataclass
class FakeSpec:
    """How the fake container should behave."""

    exit_code: int = 0
    stdout: bytes = b""
    stderr: bytes = b""
    oom: bool = False
    #: Rewrites the ingested tree to simulate what the command did.
    mutate: Callable[[dict[str, WorkspaceFile]], dict[str, WorkspaceFile]] | None = None
    #: Raw bytes returned by ``get_archive`` instead of a tar of the (mutated) tree —
    #: how the hostile-archive tests inject a malicious egress.
    egress_tar: bytes | None = None
    create_error: Exception | None = None
    put_error: Exception | None = None
    start_error: Exception | None = None
    wait_error: Exception | None = None
    logs_error: Exception | None = None
    get_error: Exception | None = None
    #: Labels the fake runner image advertises. Override to model a foreign image.
    image_labels: dict[str, str] | None = None
    #: Raised by ``images.get`` — models a missing or unreachable image.
    image_error: Exception | None = None


class FakeContainer:
    def __init__(self, spec: FakeSpec) -> None:
        self._spec = spec
        self.ingested: dict[str, WorkspaceFile] = {}
        self.ingested_tar: bytes = b""
        self.started = False
        self.killed = False
        self.removed = False
        self.removed_volumes = False
        self.attrs: dict[str, Any] = {"State": {"OOMKilled": spec.oom}}

    def put_archive(self, path: str, data: bytes) -> bool:
        assert path == WORK_DIR
        if self._spec.put_error is not None:
            raise self._spec.put_error
        self.ingested_tar = data
        self.ingested = tar_to_files(data)
        return True

    def start(self) -> None:
        if self._spec.start_error is not None:
            raise self._spec.start_error
        self.started = True

    def wait(self, timeout: float | None = None) -> dict[str, int]:
        if self._spec.wait_error is not None:
            raise self._spec.wait_error
        return {"StatusCode": self._spec.exit_code}

    def reload(self) -> None:
        return None

    def logs(self, stdout: bool = True, stderr: bool = False, stream: bool = False) -> Any:
        if self._spec.logs_error is not None:
            raise self._spec.logs_error
        raw = self._spec.stdout if stdout else self._spec.stderr
        return iter([raw]) if stream else raw

    def get_archive(self, path: str) -> tuple[Iterator[bytes], dict[str, Any]]:
        assert path == WORK_DIR
        if self._spec.get_error is not None:
            raise self._spec.get_error
        if self._spec.egress_tar is not None:
            raw = self._spec.egress_tar
        else:
            files = dict(self.ingested)
            if self._spec.mutate is not None:
                files = self._spec.mutate(files)
            raw = files_to_tar(files)
        return iter([raw]), {"name": "work"}

    def kill(self) -> None:
        self.killed = True

    def remove(self, force: bool = False, v: bool = False) -> None:
        self.removed = True
        self.removed_volumes = v


class FakeContainers:
    def __init__(self, spec: FakeSpec) -> None:
        self._spec = spec
        self.create_kwargs: dict[str, Any] = {}
        self.create_image: str | None = None
        self.container: FakeContainer | None = None

    def create(self, image: str, **kwargs: Any) -> FakeContainer:
        self.create_image = image
        self.create_kwargs = kwargs
        if self._spec.create_error is not None:
            raise self._spec.create_error
        self.container = FakeContainer(self._spec)
        return self.container

    def list(self, all: bool = False, filters: dict[str, Any] | None = None) -> list[Any]:  # noqa: A002
        return []


class FakeImage:
    def __init__(self, labels: dict[str, str]) -> None:
        self.labels = labels
        self.id = FAKE_IMAGE_DIGEST
        self.attrs: dict[str, Any] = {"Config": {"Labels": labels}}


class FakeImages:
    def __init__(self, spec: FakeSpec) -> None:
        self._spec = spec
        self.requested: list[str] = []

    def get(self, name: str) -> FakeImage:
        self.requested.append(name)
        if self._spec.image_error is not None:
            raise self._spec.image_error
        labels = self._spec.image_labels
        return FakeImage(dict(RUNNER_LABELS) if labels is None else labels)


class FakeDockerClient:
    def __init__(self, spec: FakeSpec) -> None:
        self.containers = FakeContainers(spec)
        self.images = FakeImages(spec)


def patch_docker(
    monkeypatch: Any,
    spec: FakeSpec | None = None,
    *,
    from_env_error: Exception | None = None,
    image: str = FAKE_IMAGE_DIGEST,
) -> FakeDockerClient | None:
    """Point ``docker.from_env`` at the fake, force ``SANDBOX_KIND=docker``, and pin a valid
    runner reference by default so tests exercise the path under test rather than tripping
    the (separately tested) image gate."""
    import docker

    from app.config import settings

    client = None if from_env_error is not None else FakeDockerClient(spec or FakeSpec())

    def _from_env(*args: Any, **kwargs: Any) -> FakeDockerClient:
        if from_env_error is not None:
            raise from_env_error
        assert client is not None
        return client

    monkeypatch.setattr(settings, "sandbox_kind", "docker")
    monkeypatch.setattr(settings, "sandbox_image", image)
    monkeypatch.setattr(docker, "from_env", _from_env)
    return client
