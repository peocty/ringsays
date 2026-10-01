# Build status register

Updated at end of each stage. Status values:

- **Done, tested here**: code written and its tests pass in Claude's build workspace
- **Written, needs device testing**: compiles and unit tests pass, but needs a real iPhone or Android phone
- **Mock, needs real provider**: works end to end with a labelled mock adapter
- **Not started**

## Stage 1 (2026-10-01)

| Area | Item | Status |
| --- | --- | --- |
| Repository | Monorepo layout, pnpm workspaces, Turborepo, Makefile | Done, tested here |
| Contracts | Enterprise, Client, Admin OpenAPI 3.1; shared components | Done, tested here (Redocly lint: 0 errors) |
| Contracts | AsyncAPI internal events | Done, tested here (AsyncAPI CLI: 0 errors) |
| Contracts | Canonical intent state machine | Done, tested here |
| Contracts | Purpose code seed (8 BFSI codes, en and ar) | Done; Arabic needs native speaker review |
| Backend | Intent domain types, creation rules, state machine | Done, tested here (490 tests) |
| Backend | FastAPI app with health endpoint | Done, tested here |
| Backend | Persistence, API routes, outbox | Done in stage 2, see below |
| Shared TS | `@ringsays/domain` types, transition table, UI helpers | Done, tested here (15 tests) |
| Infra | Docker Compose (Postgres, Redis, NATS, API) | Config validated; not run here (no Docker daemon in workspace) |
| Infra | CI workflow | Written; runs once pushed to GitHub |
| Mobile, portal, SDK | All | Not started (stages 5 to 7) |

## Stage 2 (2026-10-01)

| Area | Item | Status |
| --- | --- | --- |
| Database | Alembic migration: enterprise, intent, platform, audit schemas | Done, tested here on PostgreSQL 16 |
| Database | Three roles, forced row level security on every tenant table (ADR 0007) | Done, tested here |
| Auth | OAuth 2.0 client credentials token endpoint, scoped JWT, scrypt client secrets | Done, tested here; production key must move to KMS |
| Enterprise API | Create, get, cancel, calling, outcome; list and propose purpose codes | Done, tested here |
| Enterprise API | Idempotency keys on every write (replay and mismatch) | Done, tested here |
| Enterprise API | Problem details errors; OAuth standard errors on token endpoint | Done, tested here |
| Enterprise API | Responses validated against OpenAPI contract at runtime in tests | Done, tested here |
| Platform | Transactional outbox, relay with retry, personal data guard | Done, tested here with MockPublisher |
| Platform | NATS JetStream publisher | Written, not run here (no NATS server in workspace) |
| Audit | Hash chained, append only audit log with verification | Done, tested here |
| Jobs | Expiry sweep (multi worker safe) | Done, tested here |
| Demo | Seed script for a MOCK bank tenant with 8 purpose codes | Done, run live here |
| Live check | API served by uvicorn, token issued, intent created and read over HTTP | Done here |
| Not yet | Rate limits, webhooks, Context Tokens, delivery, client API | Stage 3 |

Test count: 547 backend tests (unit and integration), 15 TypeScript tests.

### Independent review (stage 2)

A separate reviewer audited stage 2 and found 8 defects. All are fixed, each with a regression test in
`tests/integration/test_review_fixes.py` or `test_tenant_isolation.py`.

| # | Defect | Fix |
| --- | --- | --- |
| 1 | Concurrent expiry workers could deadlock across tenants | Each intent expires in its own short transaction |
| 2 | `parent_intent_id` could point at another tenant's intent | Parent loaded through tenant scope; 422 if absent |
| 3 | SQL errors could write phone numbers to logs | Engines hide parameters; generic 500 handler logs type only |
| 4 | Unknown client ids answered faster (timing probe) | Dummy scrypt check of equal cost |
| 5 | Outbox could reorder events and drop poison messages silently | Single relay via advisory lock, stop at first failure, dead letter with alert log |
| 6 | Audit TRUNCATE not blocked; tail removal undetectable | TRUNCATE trigger; `chain_heads` export and anchored verification |
| 7 | Request `department_id` stored unchecked | Must equal agent's department, else 422 |
| 8 | Revoked client kept access until token expiry; missing scope claim gave 500 | Client and tenant status rechecked per request (30 s cache); scope claim required |

Areas reviewer found clean: transaction scoped tenant setting, idempotency under concurrency, optimistic locking, personal data in outbox payloads and responses.

Contract fix found by conformance tests: `PurposeCode` response schema inherited `additionalProperties: false` from create schema, which would have rejected valid responses. Split into `PurposeCodeFields`.

## Known gaps

- Foundation doc section H transition table predates ADR 0004 refinements.
- Several list endpoints lack a 4XX response in contracts (lint warnings).
- `POST /intents/{id}/calling` moves intent straight to IN_PROGRESS; stage 3 adds pre call push before that.
- Webhook delivery to tenants is not yet built, so tenants must poll `GET /intents/{id}` until stage 3.
- Audit chain heads must be exported to write once storage by an operations job; export target not built.
- Client status cache is per process (30 s); a shared Redis cache comes with rate limits in stage 3.
- Python 3.11 used in build workspace; Dockerfile and CI use 3.12. Code targets 3.11 or later.
