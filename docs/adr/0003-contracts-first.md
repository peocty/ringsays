# 0003 Contracts are source of truth

Status: Accepted, 2026-10-01

## Decision
`contracts/` holds OpenAPI 3.1 (enterprise, client, admin, shared components), AsyncAPI 3 for internal events, canonical intent state machine, and purpose code seed. Backend enums and TypeScript domain types are tested against these files; any drift fails CI. API clients for TypeScript are generated from OpenAPI, never handwritten.

## Consequences
A change to an API starts as a change to a contract file, reviewed like code. Lint runs with Redocly; current warnings (missing 4XX on a few list endpoints) are tracked, errors are not allowed.
