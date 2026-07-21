"""Code sandbox tool (ADR-007/025): output formatting + disabled path.

Pure unit tests (no DB, no Docker): the real docker execution is browser-verified;
here we mock the runner to check the tool wiring, output formatting, and the
default 'disabled' behavior.
"""

from __future__ import annotations

import uuid

import pytest

from app.sandbox.runner import RunResult
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
