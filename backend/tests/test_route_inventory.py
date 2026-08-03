"""Generated route inventory anti-drift canaries."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "route_inventory.py"
    spec = importlib.util.spec_from_file_location("route_inventory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_route_inventory_is_current_and_deleted_routes_stay_deleted() -> None:
    module = _module()
    rendered = module.render()
    assert module.OUTPUT.read_text(encoding="utf-8") == rendered
    assert "/files/" not in rendered
    assert "sandbox-runs" not in rendered
    assert "| `POST` | `/projects/{project_id}/runtime` |" in rendered
    assert "| `POST` | `/runtime/{runtime_session_id}/exec` |" in rendered
