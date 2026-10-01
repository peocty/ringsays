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
make check     # lint contracts, lint and type check backend, run all tests
make up        # start Postgres, Redis, NATS and API with mock adapters
```

API health: http://localhost:8000/health

## Rules for contributors

- Change a contract first, then code. Parity tests fail on any drift.
- Every external integration sits behind an interface with a class named `Mock...`. Mocks never report a real external success.
- No phone numbers, names or free text in events or logs.
- Arabic strings must be complete before merge.
- Every status change goes through `state_machine.transition`.

## Product and architecture references

- Foundation document (A to P): shared separately as a Claude Docs link
- Architecture decisions: `docs/adr/`
