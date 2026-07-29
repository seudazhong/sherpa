"""Sherpa backend test package.

Python executes this module before any other member of the ``tests`` package — including
``conftest.py`` — which makes it the only place guaranteed to run *before* ``app.config``
builds its ``Settings`` singleton. The suite's data-plane isolation (ADR-044, backlog B-9)
is therefore installed here and nowhere else.
"""

from __future__ import annotations

from tests.db_guard import apply_test_environment

apply_test_environment()
