# RingSays developer handover

For the PEOCIT engineering team taking over the build. Read this first, then `docs/STATUS.md` (what
is done, mocked or untested) and `docs/adr/` (why things are the way they are).

## 1. What RingSays does

An organisation (launch: KSA banks) never cold calls. It creates an **intent**: verified sender,
approved purpose, expected length, optional offered times. The customer answers in the RingSays app or
inside the bank's own app (SDK): talk now, later, pick or suggest a time, message instead, decline.
The organisation hears back by signed webhook and calls only when the customer said yes. Every step
is recorded in a tamper evident audit chain per organisation.

Watch `docs/demo/ringsays-demo.mp4` (65 s) before reading code.

## 2. Architecture in one picture

```text
 Bank systems ──OAuth client credentials──► Enterprise API ─┐
 Portal (React) ──OIDC + PKCE────────────► Admin API ──────┤        PostgreSQL 16 (RLS per tenant
 Back office portal (internal) ──OIDC────► Back office API ─┤──────► and per user, hash chained audit)
 RingSays app / bank app SDK ──device key► Client API ─────┘             │ outbox (same transaction)
                                                                          ▼
                       Worker: delivery ladder, expiry, outbox relay ──► NATS JetStream
                               webhooks (HMAC signed) ──► bank        Redis: limits, revocations
                               SMS (Taqnyat/Unifonic), push (FCM/APNs)
```

One FastAPI deployable (modular monolith, ADR 0001), run as three processes: API, back office API
(separate deployment, separate database role) and worker.

## 3. Codebase map

| Path | What | Start reading at |
| --- | --- | --- |
| `contracts/` | Source of truth: OpenAPI (enterprise, client, admin), AsyncAPI events, intent state machine, purpose code seed | `state-machines/intent.yaml` |
| `backend/app/core/` | Settings (fail closed), database sessions and scopes, auth, OIDC, phone hashing, problem responses, trusted proxies | `config.py`, `db.py` |
| `backend/app/modules/intent/` | Intent creation, state machine, display text | `state_machine.py` |
| `backend/app/modules/delivery/` | Channel ladder (SDK, push, PSTN), adapters interfaces and MOCKs | `service.py`, `adapters.py` |
| `backend/app/modules/identity/` | Sign in codes, device keys, sessions, refresh rotation | `service.py` |
| `backend/app/modules/client/` | App and SDK API (inbox, answers, preferences, consents) | `api.py` |
| `backend/app/modules/enterprise/`, `context/` | Bank API surface, Context Tokens for the SDK | `service.py` |
| `backend/app/modules/admin/` | Portal API, roles and permissions table, back office, monitor | `access.py` |
| `backend/app/modules/webhooks/`, `audit/` | Signed webhook delivery; per tenant hash chain | |
| `backend/app/platform/` | Outbox relay, idempotency, rate limits, evidence storage, real providers | `providers/` |
| `backend/migrations/` | Alembic, raw SQL: schemas, RLS policies, grants, guard triggers | `0001_initial.py`, `0006_portable_rls.py` |
| `apps/portal/` | Enterprise portal and back office UI (Arabic default, strict CSP) | `src/App.tsx` |
| `apps/mobile/` | RingSays app (Expo, React Native) | `src/` |
| `packages/client/` | Typed client, device key, session (app and SDK share it) | `session.ts` |
| `packages/domain/` | Shared TypeScript domain types, parity tested against contracts | |
| `sdk/react-native/` | `<IntentCard token=… />` for banks' own apps | `src/RingSays.tsx` |
| `examples/mock-bank/` | A whole fictional bank integration: server, agent console, customer app, e2e, demo recording | `README.md` |
| `deploy/` | OpenTofu (Google Cloud Dammam), Kustomize overlays, release and validation scripts | `README.md` |
| `docs/adr/` | 15 decisions | 0007 (tenant isolation), 0009 (identity), 0015 (providers) |

## 4. Run and test

```bash
make setup                # TypeScript and Python dependencies
make up                   # Docker: Postgres, Redis, NATS, migrations, API (MOCK adapters)
make check                # contracts lint, ruff, mypy, all backend and TypeScript tests
make e2e                  # portal browser tests (API running)
make e2e-mobile           # app web build browser tests
make e2e-bank             # whole Mock Bank flow in the browser
examples/mock-bank/demo/run.sh   # record the walkthrough video again (needs ffmpeg)
deploy/scripts/validate.sh       # every deployment check (kubeconform, kube-linter, Checkov, tflint)
```

Without Docker see the root `README.md`. Gotchas:

- The integration suite's `infra/sql/00-roles.sql` removes BYPASSRLS cluster wide (as managed
  PostgreSQL has none); a development database then needs `make migrate` (migration 0006 adds the
  explicit worker and back office policies).
- In local environment the sign in code is at `GET /dev/sms/last-code?phone=…`; the worker runs once
  with `python -m app.worker --once`.
- Settings fail closed: without `RINGSAYS_ENVIRONMENT=local` the API refuses local secrets and MOCK.

Current results (2026-10-02): 780 backend tests (Python 3.12 locks, real NATS included), all
TypeScript workspace tests, portal 12, app 6 and Mock Bank 4 browser tests, deploy validation clean.

## 5. Security model (keep these true)

| Rule | Where enforced |
| --- | --- |
| Tenant data only through forced row level security; no role has BYPASSRLS or SUPERUSER | migrations 0001, 0006; `core/db.py` sets scope per transaction; missing scope means no rows |
| API role cannot read credentials table, outbox or approve anything | grants, security definer functions, guard triggers (ADR 0007, 0010) |
| Phone numbers stored as keyed hash (pepper outside database), encrypted copy only for export | `core/phone.py` |
| Sessions bound to a device P-256 key; refresh reuse revokes the family | `identity/service.py`, `packages/client/session.ts` |
| Context Token shown once, bound to the first device that opens it | `context/service.py` |
| Webhooks HMAC SHA-256 over timestamp and raw body; https 443 public addresses only outside local | `webhooks/` |
| Every status change through `state_machine.transition`; every write audited | `intent/state_machine.py`, `audit/service.py` |
| No numbers, names or free text in events or logs; push payload carries ids only | review rule; tests assert payloads |
| Strict CSP everywhere (portal, console), DOM text only | `apps/portal/nginx`, Mock Bank console |
| Data stays in the Kingdom: me-central2, regional endpoints, CMEK, Assured Workloads | `deploy/terraform` (ADR 0014) |

## 6. Rules for changes

1. Contract first (`contracts/`), then code: parity tests fail on drift; generated types are checked in CI.
2. Every external integration behind an interface with a `Mock…` class; mocks never report real success.
3. Migrations expand then contract across two releases; RLS and grants in the same migration as the table.
4. Arabic strings complete before merge (native review still outstanding, see STATUS).
5. Each stage ends with an independent review; findings and fixes go into `docs/STATUS.md`.

## 7. Path to first production customer

| Step | Owner | Notes |
| --- | --- | --- |
| Repository on GitHub (`peocty/ringsays`); CI runs on every push | Done 2026-10-02 | Same name, same case, in OpenTofu `github_repository` |
| Google Cloud organisation prerequisites, Assured Workloads KSA folder | PEOCIT cloud admin | `deploy/README.md` "Prerequisites" |
| Apply staging, release, run the verification checklist | Platform team | Staging runs MOCK providers |
| CST sender name, Taqnyat or Unifonic account | PEOCIT | Weeks of lead time; start now |
| Apple developer key, Firebase project, app store builds (EAS) | Mobile team | Test the app on real phones first (STATUS: "needs device testing") |
| Production apply, provider secrets, first release | Platform team | `deploy/README.md` "Real providers" |
| Pilot bank: tenant verification in back office, sandbox credentials, webhook, SDK in their app | PEOCIT and bank | Mock Bank is the reference integration |
| Bank security review | Bank | Kustomize YAML, Checkov results, ADRs, audit export are the evidence |

## 8. Open items, ranked

1. **Real phone testing** of app and SDK (iOS and Android), push permission flows, app store builds.
2. **Live provider check** (Taqnyat or Unifonic, FCM, APNs) on staff phones before the pilot.
3. **Context Token re-issue** endpoint (a lost create response leaves the SDK without its token).
4. **Four eyes approval** in the back office (one reviewer can approve alone today).
5. **Audit chain head export** to write once storage (operations job).
6. **Egress narrowing** to the provider list (egress proxy or FQDN policies).
7. **Asymmetric token signing** with a KMS held key (HS256 shared secret today).
8. Hardware backed device key (Secure Enclave, StrongBox); CallKit and ConnectionService integration.
9. Native Arabic review of portal and app; Arabic server validation messages.
10. MVP 2: consumer to consumer intents, Request to Talk, contact discovery (ADR 0006).

The full list with context is "Known gaps" in `docs/STATUS.md`.

## 9. Operations

Runbook: `deploy/README.md` (first deployment, every release, verification checklist, backups and
recovery, rotation, scaling, maintenance windows, erasure, real providers). Release order is fixed:
prerequisites, migration job to completion, then everything else; a failed migration changes nothing.
