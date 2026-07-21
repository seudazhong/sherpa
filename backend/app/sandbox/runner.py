"""Hardened code-execution sandbox (ADR-007/025).

Each run executes a Python snippet in an ephemeral, **network-disabled** Docker
container with all capabilities dropped, non-root user, read-only rootfs (+ a
small tmpfs /tmp), and memory / pids / CPU / wall-clock caps; the container is
removed afterwards. ``SANDBOX_KIND=disabled`` (default) returns a clear
not-enabled result for offline dev / tests; the real docker path is exercised in
the browser. ``_execute`` is module-level so tests can substitute a fake.
"""

from __future__ import annotations

import asyncio
import dataclasses

from app.config import settings


@dataclasses.dataclass(frozen=True)
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    error: str | None = None


def _run_docker(code: str) -> RunResult:
    import docker
    from docker.errors import DockerException

    try:
        client = docker.from_env()
    except DockerException as exc:
        return RunResult("", "", -1, False, error=f"sandbox_unavailable: {exc}")

    try:
        container = client.containers.run(
            settings.sandbox_image,
            command=["python", "-I", "-B", "-c", code],
            network_disabled=True,
            mem_limit=f"{settings.sandbox_mem_mb}m",
            pids_limit=settings.sandbox_pids_limit,
            nano_cpus=1_000_000_000,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            read_only=True,
            tmpfs={"/tmp": "size=32m,mode=1777"},
            user="nobody",
            working_dir="/tmp",
            detach=True,
        )
    except DockerException as exc:
        return RunResult("", "", -1, False, error=f"sandbox_start_failed: {exc}")

    timed_out = False
    try:
        try:
            res = container.wait(timeout=settings.sandbox_timeout_seconds)
            exit_code = int(res.get("StatusCode", -1))
        except Exception:
            timed_out = True
            exit_code = -1
            try:
                container.kill()
            except Exception:
                pass
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
        return RunResult(stdout, stderr, exit_code, timed_out)
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


async def _execute(code: str) -> RunResult:
    return await asyncio.to_thread(_run_docker, code)


async def run_code(code: str) -> RunResult:
    """Run a Python snippet in the sandbox (or report it's disabled)."""
    if settings.sandbox_kind != "docker":
        return RunResult("", "", -1, False, error="sandbox_disabled")
    return await _execute(code)
