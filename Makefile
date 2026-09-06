.PHONY: install test fmt lint typecheck run up down logs clean health schema

PY ?= python3
COMPOSE ?= docker compose -f deploy/docker-compose.yml

install:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

fmt:
	ruff format backend
	ruff check --fix backend

lint:
	ruff check backend

typecheck:
	mypy backend/app backend/tests

run:
	APP_PROFILE=$${APP_PROFILE:-full} $(PY) -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --reload

health:
	curl -s http://localhost:8000/health | head -c 500; echo

# Full stack (needs Docker)
up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

# Regenerate the published IR JSON schema (Phase 0 contract artefact)
schema:
	$(PY) backend/cli.py export-schema --out backend/tests/fixtures/ir.schema.json
	@echo "schema written to backend/tests/fixtures/ir.schema.json"

clean:
	find backend -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true
	rm -rf .mypy_cache .ruff_cache .pytest_cache
