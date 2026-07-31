"""Code execution sandbox subsystem (ADR-007/025/039/047/048).

One module, one code path: :mod:`app.sandbox.runtime`.
"""

from __future__ import annotations

from app.sandbox.runtime import RunResult

__all__ = ["RunResult"]
