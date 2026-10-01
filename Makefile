.PHONY: setup check contracts backend ts up down migrate seed api portal e2e

setup:
	pnpm install
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && (test -f .env || cp .env.example .env)

contracts:
	pnpm lint:contracts

backend:
	cd backend && .venv/bin/ruff check app tests && .venv/bin/mypy && .venv/bin/pytest

ts:
	pnpm typecheck && pnpm test

check: contracts backend ts

up:
	docker compose -f infra/docker/docker-compose.yml up --build

down:
	docker compose -f infra/docker/docker-compose.yml down

api:
	cd backend && RINGSAYS_ENVIRONMENT=local .venv/bin/uvicorn app.main:app --port 8000 --reload

portal:
	pnpm --filter @ringsays/portal dev

e2e:
	pnpm --filter @ringsays/portal build && pnpm --filter @ringsays/portal e2e

migrate:
	cd backend && .venv/bin/alembic upgrade head

seed:
	cd backend && RINGSAYS_ENVIRONMENT=local .venv/bin/python -m app.scripts.seed
