# RingSays

Communication intent, context, preference and trust platform. Every contact attempt carries a verified sender, a declared reason and a negotiated time before anyone is interrupted.

Built by PEOCIT Technologies. Launch market: Saudi BFSI, enterprise first. Scale market: India.

## Repository layout

```text
contracts/          source of truth: OpenAPI, AsyncAPI, state machine, purpose code seed
backend/            Python 3.11+ FastAPI modular monolith
packages/domain/    shared TypeScript domain types (mobile, SDK, portal)
apps/               mobile (React Native) and portal (React), from stage 5
sdk/                enterprise SDK, from stage 6
infra/              Docker Compose for local; Helm and Terraform from stage 8
docs/adr/           architecture decision records
docs/STATUS.md      what is done, mocked, or needs device testing
```

## Run locally

Requirements: Node 22, pnpm 10, Python 3.11 or later, Docker.

```bash
make setup     # install TypeScript and Python dependencies
make up        # Docker: Postgres, Redis, NATS, migrations and API, all with mock adapters
```

Without Docker, against a local PostgreSQL 16:

```bash
psql -U postgres -f infra/sql/00-roles.sql
psql -U postgres -c "CREATE DATABASE ringsays OWNER ringsays_owner"
make migrate   # create schemas, row level security and grants
make seed      # MOCK bank tenant; prints client id and secret once
make check     # lint contracts, lint and type check backend, run all tests
```

Integration tests create and drop their own database `ringsays_test`, using
`RINGSAYS_TEST_PG_ADMIN_URL` (default `postgresql+psycopg://postgres@127.0.0.1:5432/postgres`).

Try the API:

```bash
curl -X POST localhost:8000/oauth/token -d grant_type=client_credentials -d client_id=... -d client_secret=...
curl -X POST localhost:8000/v1/intents -H "Authorization: Bearer <token>" \
  -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{"to":{"phone":"+966500000009"},"agent_id":"agt_demo_01","purpose_code":"LOAN.APPLICATION.UPDATE",
       "masked_reference":"4821","priority":"NORMAL","expected_duration_min":5,
       "valid_from":"<now+5m ISO>","valid_until":"<now+65m ISO>"}'
```

API health: http://localhost:8000/health

Background jobs (delivery, expiry, outbox relay, webhooks): `cd backend && .venv/bin/python -m app.worker`.
In local environment the worker uses MOCK push, directory, broker and webhook sender and says so in its log.

## Rules for contributors

- Change a contract first, then code. Parity tests fail on any drift.
- Every external integration sits behind an interface with a class named `Mock...`. Mocks never report a real external success.
- No phone numbers, names or free text in events or logs.
- Arabic strings must be complete before merge.
- Every status change goes through `state_machine.transition`.

## Product and architecture references

- Foundation document (A to P): shared separately as a Claude Docs link
- Architecture decisions: `docs/adr/`
