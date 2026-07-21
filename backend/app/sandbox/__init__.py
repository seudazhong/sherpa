"""Code execution sandbox subsystem (ADR-007/025)."""

from __future__ import annotations

from app.sandbox.runner import RunResult, run_code

__all__ = ["run_code", "RunResult"]
