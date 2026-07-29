"""Hardened code-execution sandbox (ADR-007/025) + shared runtime failure vocabulary.

Each run executes a Python snippet in an ephemeral, **network-disabled** Docker
container with all capabilities dropped, non-root user, read-only rootfs (+ a
small tmpfs /tmp), and memory / pids / CPU / wall-clock caps; the container is
removed afterwards. ``SANDBOX_KIND=disabled`` (default) returns a clear
not-enabled result for offline dev / tests; the real docker path is exercised in
the browser. ``_execute`` is module-level so tests can substitute a fake.

This module also owns the **named runtime termination reasons** shared with
:mod:`app.sandbox.project_sandbox` (events §2.11 ④ / api §10.7). Every container-path
failure gets its OWN name: the old blanket ``sandbox_unavailable`` made an unreachable
daemon, a missing image and a failed create indistinguishable (backlog B-8). The named
reason travels on ``RunResult.error``; the **raw** failure text travels separately on
``RunResult.error_detail`` and is for the operator log only — the model sees a static,
redacted sentence (``runtime_failure_note``), never a host path or exception text (ADR-019).
"""

from __future__ import annotations

import asyncio
import dataclasses

from app.config import settings

# --- named runtime termination reasons (events §2.11 ④) ---------------------

SANDBOX_DISABLED = "sandbox_disabled"
RUNTIME_DAEMON_UNREACHABLE = "runtime_daemon_unreachable"
RUNTIME_IMAGE_MISSING = "runtime_image_missing"
RUNTIME_START_FAILED = "runtime_start_failed"
RUNTIME_TRANSPORT_FAILED = "runtime_transport_failed"

#: One redacted, model-facing sentence per runtime reason. Static by construction: it names
#: the reason and stays actionable, but carries no host path, image reference, daemon message
#: or credential (ADR-019).
RUNTIME_FAILURE_NOTES: dict[str, str] = {
    SANDBOX_DISABLED: (
        "the sandbox runtime is disabled in this deployment, so no command was executed"
    ),
    RUNTIME_DAEMON_UNREACHABLE: (
        "the container runtime daemon could not be reached, so no command was executed"
    ),
    RUNTIME_IMAGE_MISSING: (
        "the sandbox runner image is not available locally, and the offline sandbox never "
        "reaches the network to pull it, so no command was executed"
    ),
    RUNTIME_START_FAILED: (
        "the sandbox container could not be created or started, so no command was executed"
    ),
    RUNTIME_TRANSPORT_FAILED: "the sandbox container ran but its output could not be retrieved",
}

UNMODELLED_NOTE = "the sandbox failed with an unmodelled internal error"

#: Operator-log detail is bounded; it never reaches the model.
DETAIL_MAX = 500


def runtime_failure_note(reason: str) -> str:
    """The redacted observation for a named runtime exit — safe to hand to the model."""
    return f"{reason}: {RUNTIME_FAILURE_NOTES.get(reason, UNMODELLED_NOTE)}"


@dataclasses.dataclass(frozen=True)
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    error: str | None = None
    #: Bounded raw failure text for the OPERATOR log only. ``error`` carries the contract-named
    #: reason; this carries the underlying detail (image name, daemon message, host path) that
    #: must never be handed to the model (events §2.11 ④ + ADR-019).
    error_detail: str | None = None


def named_failure(reason: str, exc: BaseException) -> RunResult:
    return RunResult("", "", -1, False, error=reason, error_detail=str(exc)[:DETAIL_MAX])


def unmodelled_failure(exc: BaseException) -> RunResult:
    """``error:<class>`` — the contract's catch-all for a failure we did not model."""
    return named_failure(f"error:{type(exc).__name__}", exc)


def _run_docker(code: str) -> RunResult:
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound

    try:
        client = docker.from_env()
    except DockerException as exc:
        return named_failure(RUNTIME_DAEMON_UNREACHABLE, exc)
    except Exception as exc:  # noqa: BLE001 - classify and observe, never crash the loop
        return unmodelled_failure(exc)

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
    except ImageNotFound as exc:
        return named_failure(RUNTIME_IMAGE_MISSING, exc)
    except (APIError, DockerException) as exc:
        return named_failure(RUNTIME_START_FAILED, exc)
    except Exception as exc:  # noqa: BLE001
        return unmodelled_failure(exc)

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
        try:
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            return named_failure(RUNTIME_TRANSPORT_FAILED, exc)
        return RunResult(stdout, stderr, exit_code, timed_out)
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


async def _execute(code: str) -> RunResult:
    return await asyncio.to_thread(_run_docker, code)


async def run_code(code: str) -> RunResult:
    """Run a Python snippet in the sandbox (or report a named reason why it could not)."""
    if settings.sandbox_kind != "docker":
        return RunResult("", "", -1, False, error=SANDBOX_DISABLED)
    return await _execute(code)
