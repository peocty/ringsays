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
| Backend | Persistence, API routes, outbox | Not started (stage 2) |
| Shared TS | `@ringsays/domain` types, transition table, UI helpers | Done, tested here (15 tests) |
| Infra | Docker Compose (Postgres, Redis, NATS, API) | Config validated; not run here (no Docker daemon in workspace) |
| Infra | CI workflow | Written; runs once pushed to GitHub |
| Mobile, portal, SDK | All | Not started (stages 5 to 7) |

## Known gaps

- Foundation doc section H transition table predates ADR 0004 refinements.
- Several list endpoints lack a 4XX response in contracts (lint warnings).
- Python 3.11 used in build workspace; Dockerfile and CI use 3.12. Code targets 3.11 or later.
