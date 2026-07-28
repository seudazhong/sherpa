"""Embedding defaults, deployment vs code (ADR-032, amended 2026-07-28).

The bundled `ollama`/`bge-m3` service is the *shipped deployment* default: compose
starts it with the core stack and defaults `EMBEDDING_KIND=ollama`, so semantic memory
and the Knowledge base (ADR-036) work out of the box. The `Settings` *code* default
stays `mock` so a bare `uv run` / `uv run pytest` never makes a real model call.

These are drift guards: docs/infra and code defaults have contradicted each other
before (contract said "default = bundled ollama" while everything shipped `mock`).
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def test_settings_code_default_stays_mock_and_offline() -> None:
    assert Settings.model_fields["embedding_kind"].default == "mock"
    assert Settings.model_fields["embedding_base_url"].default is None


def test_model_selection_is_bge_m3_1024() -> None:
    assert Settings.model_fields["embedding_model"].default == "bge-m3"
    assert Settings.model_fields["embedding_dim"].default == 1024


def test_compose_defaults_to_the_bundled_ollama() -> None:
    # web + worker both embed (REST search / ingestion), so both carry the default.
    assert COMPOSE.count("EMBEDDING_KIND: ${EMBEDDING_KIND:-ollama}") == 2
    assert "EMBEDDING_BASE_URL: ${EMBEDDING_BASE_URL:-http://ollama:11434}" in COMPOSE


def test_ollama_service_starts_with_the_core_stack() -> None:
    ollama_block = COMPOSE.split("\n  ollama:\n", 1)[1].split("\n\n", 1)[0]
    assert "ollama/ollama" in ollama_block
    assert "profiles:" not in ollama_block


def test_env_example_ships_ollama_uncommented() -> None:
    lines = [line.strip() for line in ENV_EXAMPLE.splitlines()]
    assert "EMBEDDING_KIND=ollama" in lines
    assert "EMBEDDING_BASE_URL=http://ollama:11434" in lines
    assert "EMBEDDING_MODEL=bge-m3" in lines
    assert "EMBEDDING_DIM=1024" in lines
