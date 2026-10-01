.PHONY: bank-setup bank bank-app e2e-bank setup check contracts backend ts up down migrate seed api portal mobile mobile-web e2e e2e-mobile

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
	cd backend && RINGSAYS_ENVIRONMENT=local RINGSAYS_OTP_PER_IP_PER_HOUR=$${RINGSAYS_OTP_PER_IP_PER_HOUR:-30} .venv/bin/uvicorn app.main:app --port 8000 --reload

portal:
	pnpm --filter @ringsays/portal dev

mobile:
	pnpm --filter @ringsays/client build && pnpm --filter @ringsays/mobile start

mobile-web:
	pnpm --filter @ringsays/client build && EXPO_PUBLIC_RINGSAYS_PREVIEW_PUSH_TOKEN=mock-web pnpm --filter @ringsays/mobile export:web

e2e:
	pnpm --filter @ringsays/portal build && pnpm --filter @ringsays/portal e2e

# Needs `make api` started with RINGSAYS_OTP_PER_IP_PER_HOUR=100000 (all test sign ins share one address).
e2e-mobile: mobile-web
	pnpm --filter @ringsays/mobile e2e

migrate:
	cd backend && .venv/bin/alembic upgrade head

seed:
	cd backend && RINGSAYS_ENVIRONMENT=local .venv/bin/python -m app.scripts.seed

# Mock Bank (fictional bank using the SDK). Needs `make api` and a worker (cd backend && .venv/bin/python -m app.worker).
bank-setup:
	pnpm --filter @ringsays/react-native-sdk build && pnpm --filter @mockbank/server build && pnpm --filter @mockbank/server run setup

bank:
	pnpm --filter @mockbank/server start

bank-app:
	pnpm --filter @ringsays/react-native-sdk build && pnpm --filter @mockbank/app web

e2e-bank:
	pnpm --filter @ringsays/react-native-sdk build && pnpm --filter @mockbank/server build && pnpm --filter @mockbank/app export:web && pnpm --filter @mockbank/app e2e
