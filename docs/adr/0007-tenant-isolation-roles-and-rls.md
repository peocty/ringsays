# 0007 Tenant isolation: three database roles and forced row level security

Status: Accepted, 2026-10-01

## Decision
- `ringsays_owner` owns schemas and runs migrations only.
- `ringsays_app` is the only role the API uses. It has no BYPASSRLS and owns nothing.
- `ringsays_worker` runs background jobs (expiry sweep, outbox relay) and bypasses row level security. API code never uses it.
- Every tenant table has row level security enabled and forced, with one policy: `tenant_id = platform.current_tenant()`. The API sets `app.tenant_id` with `set_config(..., true)` at the start of each transaction, so the setting never leaks across pooled connections.
- No setting means no rows, so a forgotten tenant scope fails closed.
- `enterprise.api_clients` is readable only through a security definer function returning one client by id; the API role has no grant on the table.
- Audit table rejects UPDATE and DELETE by trigger and is hash chained per tenant.
- API role may insert outbox rows but cannot read them; only worker reads and publishes.
- Tenants still pending verification cannot send intents (403), so customers never receive an intent from an organisation RingSays has not verified.

## Evidence
`tests/integration/test_tenant_isolation.py` probes every intent endpoint and every tenant table from a second tenant and from no tenant, and checks cross tenant writes are refused by the database.
