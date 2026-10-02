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

## Stage 4 (2026-10-01)

| Area | Item | Status |
| --- | --- | --- |
| Identity | Phone sign in with one time code (MOCK SMS), device registration with P-256 key | Done, tested here |
| Identity | Access tokens, rotating refresh tokens signed by device key, reuse detection | Done, tested here |
| Identity | Code request limits per phone, network and platform; daily wrong code cap; all fail closed | Done, tested here |
| Client API | Inbox with folders and paging, intent detail, Arabic and English rendering from approved templates | Done, tested here |
| Client API | Respond: talk now, later, propose times, schedule, message instead, decline | Done, tested here |
| Client API | Enterprise SDK endpoints: resolve and respond with Context Token, no account needed | Done, tested here |
| Privacy | Preferences with ETag and validation, consent ledger and withdrawal, export, erasure | Done, tested here |
| Privacy | Previous holder's intents never visible to a later account on the same number (ADR 0009) | Done, tested here |
| Delivery | Real recipient directory from RingSays accounts; push reaches signed in devices (MOCK push provider) | Done, tested here |
| Contracts | Client API responses validated against client.yaml at runtime in tests | Done, tested here |

Test count: 650 backend tests, 15 TypeScript tests.

### Independent review (stage 4)

| # | Defect | Fix |
| --- | --- | --- |
| 1 | New account on erased or recycled number saw and could answer previous holder's intents | Recipient policy bounded by account creation time (ADR 0009) |
| 2 | Sign in codes could be brute forced across challenges; unbounded SMS sending | Old codes invalidated, daily wrong code cap, per network and platform limits |
| 3 | SDK deliveries never created consent entries; withdrawal did not hide them | SDK delivery records contact; withdrawn organisations removed from inbox |
| 4 | Real Accept-Language headers (en-US, ar-SA) rejected | Header parsed with quality values; Arabic default |
| 5 | Message text accepted and silently dropped | Field removed; free text refused until a reviewed design exists |
| 6 | Two first sign ins for one number gave a 500 | Insert with ON CONFLICT |
| 7 | Two devices saving preferences first time gave a 500 | Insert with ON CONFLICT, loser gets 412 |
| 8 | Export listed communications never received | Same filter as inbox |
| 9 | Erasure took up to 30 s to reach other API processes | Shared revocation list in Redis; 5 s cache fallback |

Also: timezone validation now accepts only IANA region zones.

## Stage 5 (2026-10-01)

| Area | Item | Status |
| --- | --- | --- |
| Contracts | Admin API (organisation, verification, people, catalogue, integration, monitor, audit, back office) | Done, tested here (Redocly: 0 errors, 0 warnings; live responses validated) |
| Sign in | OpenID Connect with PKCE, refresh token renewal; roles stored by RingSays and checked per request (ADR 0010) | Done, tested here with MOCK identity provider |
| Organisation | Profile, departments, agents, calling numbers (masked), people and roles with invitations | Done, tested here |
| Verification | Evidence upload (type read from content, 5 MB), submit, RingSays review, approve or reject with reason | Done, tested here; files on local disk (MOCK object storage) |
| Catalogue | Propose, review and retire purpose codes | Done, tested here |
| Integration | API credentials and webhook endpoints (secrets shown once), delivery log, replay | Done, tested here |
| Monitor | Live intent monitor with masked numbers, private number search, detail with history and attempts | Done, tested here |
| Audit | Tenant audit log, chain check, CSV export that can be verified outside RingSays | Done, tested here |
| Back office | Separate database role; onboarding, suspension with containment, review queue | Done, tested here |
| Database | Guard triggers so the API role can never approve its own items (allow list, SQLSTATE RSG01) | Done, tested here |
| Delivery | Receiver rules use the tenant's sector (bank, insurance, finance, government) | Done, tested here |
| Portal | React 19, Arabic first with right to left, English; phone and desktop layouts | Done, tested here in headless Chromium |
| Portal | Strict Content Security Policy, runtime configuration, unprivileged nginx image (ADR 0011) | Policy tested here in browser; nginx image not built here (no Docker daemon) |
| Settings | Fail closed: default environment is production; MOCK sign in only in local | Done, tested here |

Test count: 721 backend tests, 15 domain tests, 27 portal unit tests, 12 browser tests (desktop and phone,
including accessibility scans in Arabic and English with no serious violations).

### Independent review (stage 5, backend)

| # | Defect | Fix |
| --- | --- | --- |
| 1 | Profile could change after RingSays verified it (race with approval) | Tenant row locked and status rechecked; database guard on profile columns |
| 2 | Evidence could be removed while a submission was being decided | Same lock for upload and delete; database guard; approval checks all submitted evidence still exists |
| 3 | Guard trigger skipped any role not named exactly ringsays_app | Allow list (owner, back office); every other role guarded |
| 4 | Unlimited upload read to disk before authentication | Body size capped in outer middleware (Content-Length and streamed count) |
| 5 | Customer number in URL query, so in access logs | Number search moved to POST body |
| 6 | Staff identity provider could consume a tenant invitation | Invitations bind only for tenant identity provider |
| 7 | Failed upload left the file behind | File removed on any failure before commit |
| 8 | Administrator could grant themselves integration rights | No one changes their own roles; catalogue:write needs propose permission |
| 9 | Suspension blocked revoking leaked credentials | Containment actions allowed while suspended |
| 10 | Expired invitations were a dead end; concurrent invites gave 500 | Re-invite renews; unique conflicts return 409 |
| 11 | Invitation acceptance and staff evidence downloads not audited; two contract mismatches | Both audited in tenant chain; contract corrected |

Also fixed from lower-confidence notes: settings fail closed (production by default), local file storage refused in
production, trusted email issuers for providers without `email_verified`, CSV export uses the hashed time format and
names escaped cells, NUL characters return 400, migration downgrade revokes grants.

### Independent review (stage 5, portal)

| # | Defect | Fix |
| --- | --- | --- |
| 1 | Production CSP used a URL with path, which blocks discovery and token calls | Origins derived at container start; same logic in browser tests |
| 2 | nginx dropped security headers in every location | Shared header include in every location |
| 3 | Token expiry sent people to sign in and lost their work | Background renewal with refresh token; expired session shows a prompt, page stays |
| 4 | Possible endless redirect when the API rejects a valid session | No automatic redirect at all |
| 5 | Live refresh plus Load more could skip rows | Infinite query refetches all pages with fresh cursors |
| 6 | Late Load more results could land under a new filter | Pages keyed by filter |
| 7 | Secrets kept in mutation cache; focus lost after dialogs | Cache reset on acknowledgement; focus returned to opener |
| 8 | Arabic Revoke and Cancel had the same label | Revoke is now إبطال |
| 9 | Suspended organisations saw actions that always fail | Only containment actions shown |
| 10 | Profile looked editable while under review | Locked while a request is open |
| 11 | Closed phone menu still in tab order | Hidden when closed; Escape and backdrop close it |
| 12 | Wrong toast after a review decision | "Decision recorded" |
| 13 | Arabic wording and bidi issues | Client ID, Requested, urgent hint isolates, count phrasing, arrow direction |

Also fixed: sign out sends id_token_hint, source maps not shipped, .dockerignore, runtime configuration, uploads
and downloads report expired sessions, Arabic file names (filename*), date filters in Riyadh time, stale dialog
errors, menus follow role changes, accessible tabs, focus moves to page content on navigation.

## Stage 6 (2026-10-01)

| Area | Item | Status |
| --- | --- | --- |
| Client package | Typed client from client contract; P-256 device key, signed refresh, single flight renewal, sign out epoch | Done, tested here (signatures verified with OpenSSL; live tests against local API) |
| API | `POST /auth/logout`: device, refresh tokens and push tokens revoked | Done, tested here |
| API | Fix: refreshed access tokens hid intents received before the refresh (visibility started at refresh time) | Done, tested here (regression test) |
| App | Sign in with SMS code (KSA, India, UAE numbers), Arabic first with right to left, English | Done, tested here (web build in phone sized Chromium) |
| App | Inbox (requests, scheduled, history), intent screen with trust badge, reason, time left; talk now, later, suggest times, message, decline with reason | Done, tested here |
| App | Settings: verified only, quiet hours, organisations with consent withdrawal, data export as file, sign out, delete account | Done, tested here; file share sheet needs device testing |
| App | Push (APNs, FCM), notification opens intent, token kept current | Written, needs device testing; MOCK push provider |
| App | Secure storage (Keychain, Keystore), release build requires https | Written, needs device testing |
| SDK | `RingSaysProvider`, `IntentCard`, `useIntent` over Context Tokens; install binding; en/ar | Done, tested here (fake API and live Context Token test) |
| CI | Mobile, client and SDK in workspace typecheck and tests; e2e job runs portal and app web build | Written; runs when repository is pushed |

Test count: 725 backend tests, 15 domain, 20 client, 10 app unit, 13 SDK, 27 portal unit; browser tests: 12 portal,
6 app.

### Independent review (stage 6)

| # | Defect | Fix |
| --- | --- | --- |
| 1 | Sign out only on the phone; pushes for the old account kept reaching a shared phone | `POST /auth/logout` revokes device, refresh and push tokens; app calls it, always clears the phone |
| 2 | Refresh in flight during sign out wrote the session back | Session epoch; stale refresh results dropped |
| 3 | Android push: channel created after permission prompt; no Firebase file | Channel first; `GOOGLE_SERVICES_JSON` build setting |
| 4 | Push token never updated after sign in | Sent on start, foreground and token rotation |
| 5 | One secure storage read error broke the app until restart | Failed start not cached; unreadable key replaced |
| 6 | Quick quiet hours taps raced with the same version (412) | Buttons disabled while saving |
| 7 | Queries ignored app background and foreground on phones | Focus tied to app state |
| 8 | SDK could show an old token's intent after the token changed | Only the latest request updates the card |
| 9 | Cached organisation texts kept the old language | Reload on language switch |
| 10 | Old notification reopened at next sign in | Handled once, then cleared |
| 11 | Release build could fall back to plain http loopback | https required in release builds |
| 12 | Answer errors hidden behind the open sheet | Errors inside each sheet; ended intents refresh |
| 13 | Data export showed only a count | Saved as a JSON file through share sheet (download on web) |
| 14 | One device key across accounts on a phone | New key after every sign out |
| 15 | Native right to left mixed with app language | Native RTL off; direction from app language |

## Stage 7 (2026-10-02)

| Area | Item | Status |
| --- | --- | --- |
| API | Organisation offered times on create (`offered_slots`); customer picks one (SCHEDULE) | Done, tested here |
| API | `POST /v1/intents/{id}/schedule`: organisation confirms a time the customer suggested | Done, tested here |
| API | Slot rules: at most 120 minutes, at most 7 days ahead, distinct, not before window | Done, tested here |
| API | Context Token lasts as long as the agreed time (LATER, PROPOSE, SCHEDULE extend it) | Done, tested here |
| API | `deadline` in intent display; suggested times never pass it; within 09:00 to 20:00 on the customer's clock | Done, tested here |
| API | Webhooks to a receiver on the same machine (http, loopback) in local environment only | Done, tested here (real HTTP delivery) |
| API | Outside local: http portal origins refused at start; CORS allows `RingSays-Device-Id` | Done, tested here |
| Mock Bank | Server: OAuth, idempotent calls, signed webhook receiver, agent console, bank app API | Done, tested here |
| Mock Bank | Customer app (Expo) with SDK `IntentCard`, Arabic and English | Done, tested here (web build); needs device testing |
| Mock Bank | Integration guide for bank teams (`examples/mock-bank/README.md`) | Done |

Test count: 736 backend tests, 15 domain, 24 client, 10 app unit, 13 SDK, 14 bank server, 3 bank app, 27 portal unit;
browser tests: 12 portal, 6 app, 4 mock bank (console, app, API and webhooks together).

### Independent review (stage 7)

| # | Defect | Fix |
| --- | --- | --- |
| 1 | Offered times had no length or horizon limit: one slot years ahead kept an intent callable | Slots at most 120 minutes, at most 7 days ahead, distinct, not before window; same for customer proposals |
| 2 | Context Token expired at the original window even after a later time was agreed | Token expiry follows the intent's extended validity |
| 3 | Bank answered 204 to webhooks for intents not stored yet, losing them | 503, so RingSays retries |
| 4 | New idempotency key per call, no time limit: lost response could create a second intent | Key per bank operation reused on retry; console request id; 10 s limit; one safe retry |
| 5 | Rescheduled intent kept the old scheduled time in the bank's record | Cleared on reschedule; times reread from API |
| 6 | Global console lockout; unbounded throttle and session maps; customer listing | Per address and per account limits, periodic cleanup, listing marked demo only |
| 7 | Loopback rule mismatched database check (IPv6, case, no path) | One rule: 127.0.0.1 or localhost with a path; check case insensitive; downgrade works under RLS |
| 8 | http origins accepted outside local | Start refused unless every portal origin is https |
| 9 | Suggested times ignored the intent deadline | `deadline` exposed; suggestions stop at it; suggest button off when none fit |
| 10 | ACCEPT and DECLINE kept offered times | Cleared |
| 11 | Console cookie without Secure; plain token storage undocumented | Secure over https; storage guidance in code and guide |

Also found while capturing screenshots: suggested times included 01:00 (comment promised working hours,
code did not check). Fixed in the shared client, so the RingSays app and the SDK both benefit.

## Stage 8 (2026-10-02)

| Area | Item | Status |
| --- | --- | --- |
| Database | No superuser, no BYPASSRLS: explicit policy for worker and back office (migration 0006) | Done, tested here (full suite and Mock Bank end to end on a server with a CREATEROLE only admin) |
| Database | `bootstrap_db`: roles, passwords, ownership as a managed service admin | Done, tested here; CI job repeats it |
| Runtime | Real client address behind load balancers (trusted proxies) | Done, tested here |
| Runtime | Evidence storage on GCS (regional endpoint) and S3 compatible stores | Done, tested here with test doubles and moto; needs a real bucket check |
| Runtime | `/ready` (database, Redis), worker heartbeat, secrets read from files | Done, tested here |
| Runtime | Fix: outbox relay could never publish to a real NATS (no stream); now creates the stream, dedupes by outbox id, fails fast | Done, tested here against nats-server 2.11, single node and three node cluster |
| Image | Hash locked dependencies, numeric non root user, app run from source | Lint clean (hadolint); 753 tests pass on Python 3.12 with the locks; image build runs in CI |
| Kubernetes | Kustomize base, GCP Dammam component, generic component, staging and production overlays, jobs | Validated here: kubeconform strict with CRDs (164 resources), kube-linter clean, Checkov 520 passed |
| Kubernetes | Production NATS cluster configuration | Verified on a real three node cluster (routes, meta leader, stream on three replicas) |
| Terraform | Platform module, staging and production, state bootstrap (Google Cloud me-central2) | Checked here: fmt, tflint with Google ruleset, Checkov 120 passed; `tofu validate` and plan in CI (provider downloads blocked here) |
| Release | Build, SBOM and provenance, Trivy, digest pinning, migrate then roll out, smoke test | Written; release script order tested with a recording kubectl; runs in CI |

Test count: 753 backend tests (plus 3 against a real NATS server), unchanged frontend counts.

Found while building stage 8, now fixed: outbox relay against real NATS (no stream, so nothing was ever
published); NATS client retrying a refused server indefinitely and stalling the worker; production
NATS routes would never authenticate (NATS does not expand variables inside URLs); every API request
would have looked like it came from the load balancer, so the per address sign in code limit would
have applied to the whole country; job manifests referenced a ConfigMap by a name overlays hash.

## Known gaps

- Foundation doc section H transition table predates ADR 0004 refinements.
- Several list endpoints lack a 4XX response in contracts (lint warnings).
- SMS provider and push providers (APNs, FCM) are MOCK; real KSA SMS provider and Apple/Google credentials needed.
- Consumer to consumer intents and Request to Talk are MVP 2.
- A Context Token replayed through idempotency comes back null (shown once); a re-issue endpoint is needed.
- Audit chain heads must be exported to write once storage by an operations job; export target not built.
- Client status cache is per process (30 s); revocations already go through Redis to every process.
- Python 3.11 used in build workspace; Dockerfile and CI use 3.12 (suite also passes on 3.12 with the image locks).
- Verification evidence is on local disk (MOCK object storage); production needs an S3 compatible store in region
  with encryption (settings refuse local storage in production).
- Calling number checks against the operator caller name registry are manual (reviewer); no operator API yet.
- Domain ownership proof (DNS record) is a document upload, not an automatic check.
- Commercial registration and domain are not checked for duplicates across organisations; reviewer must check.
- No four eyes rule yet: one reviewer can approve alone.
- Portal Arabic needs native speaker review; server validation messages are English only.
- Container images not built here (registries blocked); CI builds and scans them.
- Infrastructure never applied to a real Google Cloud project from here; first apply needs the organisation prerequisites in deploy/README.md.
- Real SMS (KSA sender ID) and push adapters missing: production refuses MOCK, so production cannot sign anyone in yet.
- Egress is any public address on 443; narrow to providers with an egress proxy or FQDN policies.
- Disaster recovery outside the single Google Cloud KSA region needs a second provider in the Kingdom.
- Staff sign in binding (first sign in of a seeded staff email) is logged, not in a tenant audit chain.
- Toast messages inside an open dialog may not be announced by screen readers.
- App and SDK not run on real iPhone or Android phones here; push providers are MOCK; app store builds (EAS) not made.
- Device key is software (exportable inside secure storage); Secure Enclave / StrongBox key planned.
- App delivery needs push permission; without it intents go by ordinary call (PSTN).
- Incoming call screen integration (CallKit, ConnectionService) and caller name overlay not built.
- Native header back arrow does not mirror in Arabic (native RTL off by design); app Arabic needs native review.
- SDK card declines with reason "not now"; other reasons only through `useIntent`.
- A lost create response, retried, returns the intent without its Context Token (shown once); re-issue endpoint needed.
- Mock Bank customer app not run on a phone; bank side push to the app is not part of the sample (it polls).
