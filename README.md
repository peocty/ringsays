# RingSays

Communication intent, context, preference and trust platform. Every contact attempt carries a verified sender, a declared reason and a negotiated time before anyone is interrupted.

Built by PEOCIT Technologies. Launch market: Saudi BFSI, enterprise first. Scale market: India.

## Repository layout

```text
contracts/          source of truth: OpenAPI, AsyncAPI, state machine, purpose code seed
backend/            Python 3.11+ FastAPI modular monolith
packages/domain/    shared TypeScript domain types (mobile, SDK, portal)
apps/portal/        enterprise portal and RingSays back office (React, Arabic and English)
packages/client/    typed API client, device key and session (app and SDK)
apps/mobile/        RingSays app (Expo, React Native), Arabic and English
sdk/react-native/   enterprise SDK: show and answer intents inside a bank's own app
examples/mock-bank/ fictional bank using the SDK: server, agent console, customer app
infra/              Docker Compose for local development
deploy/             Kingdom hosting: Terraform (Google Cloud Dammam), Kustomize, release scripts
docs/adr/           architecture decision records
docs/STATUS.md      what is done, mocked, or needs device testing
docs/HANDOVER.md    start here: architecture, codebase map, security model, path to production
docs/demo/          recorded walkthrough (demo data) and how to record it again
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

### Portal

```bash
make api       # API on :8000 (local environment, MOCK sign in at /dev/oidc)
make seed      # demo bank with portal people and RingSays staff (all MOCK)
make portal    # http://localhost:5173
```

Sign in on the MOCK page as, for example, `admin@mockbank.example` (organisation administrator),
`integration@mockbank.example`, `supervisor@mockbank.example`, `agent@mockbank.example`,
`compliance@mockbank.example`, or RingSays staff `reviewer@ringsays.example` and `ops@ringsays.example`.
Any other email can be typed, for example one you just invited.

Browser tests: `make e2e` (needs the API running; seeds its own isolated data each run).

Settings fail closed: without `RINGSAYS_ENVIRONMENT=local` (set in `backend/.env` by `make setup`) the API
refuses to start with local secrets and MOCK sign in is off.

Background jobs (delivery, expiry, outbox relay, webhooks): `cd backend && .venv/bin/python -m app.worker`.
In local environment the worker uses MOCK push, directory, broker and webhook sender and says so in its log.

### RingSays app

```bash
make api       # with RINGSAYS_OTP_PER_IP_PER_HOUR=100000 for repeated test sign ins
make mobile    # Expo dev server: scan with Expo Go or run a development build
```

Sign in with any KSA number (`5XXXXXXXX`); in local environment the code is shown by
`curl "localhost:8000/dev/sms/last-code?phone=%2B9665XXXXXXXX"`. Send an intent from the portal or API
with `channel_preference: ["PRECALL_PUSH","PSTN"]`, then run the worker once
(`cd backend && .venv/bin/python -m app.worker --once`).

Browser tests of the web build: `make e2e-mobile`. Release builds need `EXPO_PUBLIC_RINGSAYS_API_BASE`
(https) and, for Android push, `GOOGLE_SERVICES_JSON`.

### Enterprise SDK

See `sdk/react-native/README.md`. Create the intent with `channel_preference` containing `SDK`, pass the
returned `context_token` to the bank app, render `<IntentCard token=… />`.

### Mock Bank sample

A whole bank integration (fictional): `examples/mock-bank/README.md`. Quick start: `make bank-setup`,
`make bank`, `make bank-app`, with the API and worker running.

## Deploying

See `deploy/README.md`: Google Cloud Dammam (me-central2) first, cloud neutral manifests for other KSA
clouds, prerequisites, first deployment, release flow, verification checklist. `deploy/scripts/validate.sh`
runs every check on deployment code.

## Rules for contributors

- Change a contract first, then code. Parity tests fail on any drift.
- Every external integration sits behind an interface with a class named `Mock...`. Mocks never report a real external success.
- No phone numbers, names or free text in events or logs.
- Arabic strings must be complete before merge.
- Every status change goes through `state_machine.transition`.

## Product and architecture references

- Foundation document (A to P): shared separately as a Claude Docs link
- Architecture decisions: `docs/adr/`
