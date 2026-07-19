# Sherpa — convenience task runner (Unix/CI). On Windows dev, use the raw
# `uv` / `npm` commands directly (see AGENTS.md). Canonical commands live in AGENTS.md.

.PHONY: help install dev worker test lint typecheck fmt migrate up down fe-install fe-dev fe-build fe-lint

help:
	@echo "backend:  install dev worker test lint typecheck fmt migrate"
	@echo "frontend: fe-install fe-dev fe-build fe-lint"
	@echo "infra:    up down"

# ── Backend (run in backend/) ──
install:      ; cd backend && uv sync
dev:          ; cd backend && uv run uvicorn app.main:app --reload
worker:       ; cd backend && uv run arq app.worker.WorkerSettings
test:         ; cd backend && uv run pytest
lint:         ; cd backend && uv run ruff check . && uv run ruff format --check .
typecheck:    ; cd backend && uv run mypy app
fmt:          ; cd backend && uv run ruff format .
migrate:      ; cd backend && uv run alembic upgrade head

# ── Frontend (run in frontend/) ──
fe-install:   ; cd frontend && npm ci
fe-dev:       ; cd frontend && npm run dev
fe-build:     ; cd frontend && npm run build
fe-lint:      ; cd frontend && npm run lint

# ── Infra ──
up:           ; docker compose -f infra/docker-compose.yml --env-file .env up --build
down:         ; docker compose -f infra/docker-compose.yml down
