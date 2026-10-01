.PHONY: setup check contracts backend ts up down

setup:
	pnpm install
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

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
