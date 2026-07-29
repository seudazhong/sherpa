"""Code sandbox tool (ADR-007/025): output formatting, named failures + disabled path.

Pure unit tests (no DB, no Docker): the real docker execution is browser-verified;
here we mock the runner to check the tool wiring, output formatting, the
default 'disabled' behavior, and that every failure reaches the model as a NAMED,
REDACTED observation (events §2.11 ④) instead of a raw exception string.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from app.sandbox.runner import RunResult, runtime_failure_note
from app.tools import ToolContext, build_default_registry


@pytest.mark.asyncio
async def test_run_code_disabled_by_default() -> None:
    reg = build_default_registry()
    out = await reg.get("run_code").execute(
        ToolContext(tenant_id=uuid.uuid4()), {"code": "print(1)"}
    )
    assert "not enabled" in out.llm_content


@pytest.mark.asyncio
async def test_run_code_formats_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "sandbox_kind", "docker")

    async def _fake_execute(code: str) -> RunResult:
        assert "print" in code
        return RunResult(stdout="hello world\n", stderr="", exit_code=0, timed_out=False)

    monkeypatch.setattr("app.sandbox.runner._execute", _fake_execute)

    reg = build_default_registry()
    out = await reg.get("run_code").execute(
        ToolContext(tenant_id=uuid.uuid4()), {"code": "print('hello world')"}
    )
    assert "hello world" in out.llm_content
    assert "[exit 0]" in out.llm_content


@pytest.mark.asyncio
async def test_run_code_timeout_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "sandbox_kind", "docker")

    async def _fake_execute(code: str) -> RunResult:
        return RunResult(stdout="", stderr="", exit_code=-1, timed_out=True)

    monkeypatch.setattr("app.sandbox.runner._execute", _fake_execute)

    reg = build_default_registry()
    out = await reg.get("run_code").execute(
        ToolContext(tenant_id=uuid.uuid4()), {"code": "while True: pass"}
    )
    assert "timed out" in out.llm_content


@pytest.mark.parametrize(
    "reason",
    [
        "runtime_daemon_unreachable",
        "runtime_image_missing",
        "runtime_start_failed",
        "runtime_transport_failed",
        "error:ValueError",
    ],
)
@pytest.mark.asyncio
async def test_run_code_failure_is_named_and_redacted(
    reason: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The tool observation names the failure and carries no raw exception text; the raw
    detail survives only in one structured worker log line."""
    from app.config import settings

    leaky = r"C:\host\private\kek.pem"
    monkeypatch.setattr(settings, "sandbox_kind", "docker")

    async def _fake_execute(code: str) -> RunResult:
        return RunResult("", "", -1, False, error=reason, error_detail=f"boom {leaky}")

    monkeypatch.setattr("app.sandbox.runner._execute", _fake_execute)

    reg = build_default_registry()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.tools.sandbox"):
        out = await reg.get("run_code").execute(
            ToolContext(tenant_id=uuid.uuid4()), {"code": "print(1)"}
        )

    assert reason in out.llm_content
    assert leaky not in out.llm_content
    records = [r for r in caplog.records if r.name == "app.tools.sandbox"]
    assert len(records) == 1
    assert records[0].termination_reason == reason  # type: ignore[attr-defined]
    assert leaky in (records[0].sandbox_error_detail or "")  # type: ignore[attr-defined]


def test_runtime_failure_note_names_the_reason() -> None:
    assert runtime_failure_note("runtime_image_missing").startswith("runtime_image_missing: ")
    assert "unmodelled" in runtime_failure_note("error:ValueError")
