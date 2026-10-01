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

## Stage 3 (2026-10-01)

| Area | Item | Status |
| --- | --- | --- |
| Delivery | Fallback ladder SDK, pre call push, PSTN (ADR 0008), with attempt trail per intent | Done, tested here with MOCK push and directory |
| Delivery | Receiver rules: verified only, quiet hours across midnight in receiver timezone, window rules, duration rules | Done, tested here |
| Delivery | URGENT from verified organisations passes quiet hours; quiet hours past validity skip app delivery | Done, tested here |
| Delivery | Pushes carry intent id and kind only; sent outside transactions with a lease | Done, tested here |
| Context Tokens | 128 bit token shown once, SHA-256 stored, first device binds, revoked at end, expires with intent | Done, tested here |
| Webhooks | Queued in same transaction, HMAC signed, ordered per intent, backoff 30 s to 1 h for 24 h, dead letter, replay | Done, tested here with MOCK HTTP sender |
| Webhooks | Secrets encrypted at rest (Fernet; KMS key in production), SSRF guard with pinned IP and SNI | Done; real HTTP sender not run here (no egress) |
| Limits | Per tenant creates per minute, URGENT per day, per recipient per tenant per day; atomic Redis script | Done, tested here on Redis |
| Limits | Redis outage: fail open for normal intents, fail closed (503) for URGENT | Done, tested here |
| Worker | `python -m app.worker` running delivery, expiry, outbox relay and webhooks, each job isolated | Done, tested here |

Test count: 617 backend tests, 15 TypeScript tests.

### Independent review (stage 3)

| # | Defect | Fix |
| --- | --- | --- |
| 1 | Token resolve: NULL device bypassed binding; function executable by every role; caller clock could revive expired tokens | NULL refused, execute revoked from PUBLIC, effective time never earlier than database time |
| 2 | Calling now push sent even when call was refused, and ignored receiver rules | Push only after committed transition, receiver rules applied, never on replay |
| 3 | One bad row (for example bad timezone) stopped every job for every tenant | Per item and per job isolation; error recorded with 5 minute backoff |
| 4 | Waiting or exhausted intents could starve due ones | `delivery_next_check_at` scheduling |
| 5 | SDK resolve delivered before window opened | Delivery refused before `valid_from`; early resolve delivered when window opens |
| 6 | Rejected or concurrent duplicate requests consumed rate limits | Limits counted after validation under idempotency lock |
| 7 | Slow tenant endpoint held locks and made signatures stale | Lease then send outside transaction; signed at send time; 20 sends per endpoint per run |
| 8 | SSRF guard missed NAT64, IPv4 compatible and other embedded forms; any port | Embedded IPv4 unwrapped, port 443 only, connection pinned to checked address |
| 9 | Push could repeat after a failed commit; SDK resolve raced worker | Two phase push with lease; SDK resolve locks intent |

Also raised: limiter failing open removed the URGENT cap. Now URGENT fails closed.

## Known gaps

- Foundation doc section H transition table predates ADR 0004 refinements.
- Several list endpoints lack a 4XX response in contracts (lint warnings).
- Webhook endpoint registration and replay have service functions but no HTTP route yet; admin API arrives with the portal (stage 5).
- Recipient directory is MOCK until client API and identity (stage 4).
- Tenant sector is fixed to BANK for receiver rules until admin API sets it per tenant.
- A Context Token replayed through idempotency comes back null (shown once); a re-issue endpoint is needed.
- Audit chain heads must be exported to write once storage by an operations job; export target not built.
- Client status cache is per process (30 s); move to Redis when more than one API instance runs.
- Python 3.11 used in build workspace; Dockerfile and CI use 3.12. Code targets 3.11 or later.
