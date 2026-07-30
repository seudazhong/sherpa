"""Hardened runtime failure vocabulary for the code-execution sandbox (ADR-007/025).

This module owns the **named runtime termination reasons** shared by every sandbox
entry point (today :mod:`app.sandbox.project_sandbox`; events §2.11 ④ / api §10.7).
Every container-path failure gets its OWN name: the old blanket ``sandbox_unavailable``
made an unreachable daemon, a missing image and a failed create indistinguishable
(backlog B-8). The named reason travels on ``RunResult.error``; the **raw** failure text
travels separately on ``RunResult.error_detail`` and is for the operator log only — the
model sees a static, redacted sentence (``runtime_failure_note``), never a host path or
exception text (ADR-019).

The general-purpose ``run_code`` snippet runner that used to live here is **deleted**
(ADR-045 clean break / ADR-048 O-12): ephemeral execution is reached through
``runtime.open(scope="ephemeral")`` + ``sh.exec``, so there is exactly one sandbox code
path instead of two.
"""

from __future__ import annotations

import dataclasses

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
