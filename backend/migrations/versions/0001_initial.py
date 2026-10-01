"""Initial schemas: enterprise, intent, platform, audit; forced row level security.

Revision ID: 0001
Revises:
Create Date: 2026-10-01
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "enterprise.departments",
    "enterprise.agents",
    "enterprise.calling_numbers",
    "enterprise.purpose_codes",
    "intent.intents",
    "intent.intent_events",
    "platform.idempotency_keys",
    "platform.outbox",
    "audit.audit_events",
]


def _rls() -> str:
    parts = []
    for t in TENANT_TABLES:
        parts.append(
            f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;\n"
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING (tenant_id = platform.current_tenant()) "
            f"WITH CHECK (tenant_id = platform.current_tenant());"
        )
    parts.append(
        "ALTER TABLE enterprise.tenants ENABLE ROW LEVEL SECURITY;\n"
        "ALTER TABLE enterprise.tenants FORCE ROW LEVEL SECURITY;\n"
        "CREATE POLICY tenant_isolation ON enterprise.tenants USING (id = platform.current_tenant());"
    )
    return "\n".join(parts)


UPGRADE = """
CREATE SCHEMA enterprise;
CREATE SCHEMA intent;
CREATE SCHEMA platform;
CREATE SCHEMA audit;

-- Tenant of current transaction, set by app via set_config('app.tenant_id', ..., true).
-- Returns NULL when unset, so policies match no rows.
CREATE FUNCTION platform.current_tenant() RETURNS uuid LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
$$;

-- enterprise

CREATE TABLE enterprise.tenants (
    id                  uuid PRIMARY KEY,
    legal_name_en       text NOT NULL,
    legal_name_ar       text NOT NULL,
    cr_number           text,
    domain              text,
    residency_region    text NOT NULL CHECK (residency_region IN ('KSA','IN','AE','EU')),
    verification_status text NOT NULL CHECK (verification_status IN ('PENDING','VERIFIED','SUSPENDED')),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE enterprise.departments (
    id        uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES enterprise.tenants(id),
    name_en   text NOT NULL,
    name_ar   text NOT NULL
);
CREATE INDEX ON enterprise.departments (tenant_id);

CREATE TABLE enterprise.agents (
    tenant_id       uuid NOT NULL REFERENCES enterprise.tenants(id),
    agent_id        text NOT NULL,
    department_id   uuid NOT NULL REFERENCES enterprise.departments(id),
    display_name_en text NOT NULL,
    display_name_ar text NOT NULL,
    employee_ref    text,
    active          boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, agent_id)
);

CREATE TABLE enterprise.calling_numbers (
    id             uuid PRIMARY KEY,
    tenant_id      uuid NOT NULL REFERENCES enterprise.tenants(id),
    department_id  uuid REFERENCES enterprise.departments(id),
    phone          text NOT NULL CHECK (phone ~ '^\\+[1-9][0-9]{6,14}$'),
    status         text NOT NULL CHECK (status IN ('PENDING_VERIFICATION','VERIFIED','REVOKED')),
    cst_registered boolean NOT NULL DEFAULT false,
    UNIQUE (tenant_id, phone)
);

CREATE TABLE enterprise.purpose_codes (
    tenant_id        uuid NOT NULL REFERENCES enterprise.tenants(id),
    code             text NOT NULL,
    version          integer NOT NULL DEFAULT 1,
    display_en       text NOT NULL CHECK (length(display_en) <= 160),
    display_ar       text NOT NULL CHECK (length(display_ar) <= 160),
    max_priority     text NOT NULL CHECK (max_priority IN ('LOW','NORMAL','IMPORTANT','URGENT')),
    max_duration_min integer NOT NULL CHECK (max_duration_min BETWEEN 1 AND 120),
    allowed_channels text[] NOT NULL,
    status           text NOT NULL CHECK (status IN ('PENDING_REVIEW','APPROVED','REJECTED','RETIRED')),
    PRIMARY KEY (tenant_id, code)
);

CREATE TABLE enterprise.api_clients (
    client_id   text PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES enterprise.tenants(id),
    secret_hash text NOT NULL,
    scopes      text[] NOT NULL,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Token endpoint must find a client before tenant is known. App role cannot read api_clients;
-- it calls this function, which returns one row for one client id and nothing else.
CREATE FUNCTION enterprise.lookup_api_client(p_client_id text)
RETURNS TABLE (tenant_id uuid, secret_hash text, scopes text[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = enterprise, pg_temp AS $$
    SELECT c.tenant_id, c.secret_hash, c.scopes
    FROM enterprise.api_clients c
    WHERE c.client_id = p_client_id AND c.active
$$;
-- Tenant status (for example SUSPENDED) is checked by token service inside a tenant transaction.

-- intent

CREATE TABLE intent.intents (
    id                    uuid PRIMARY KEY,
    tenant_id             uuid REFERENCES enterprise.tenants(id),
    agent_id              text,
    department_id         uuid,
    to_phone              text NOT NULL CHECK (to_phone ~ '^\\+[1-9][0-9]{6,14}$'),
    purpose_code          text,
    masked_reference      text CHECK (masked_reference ~ '^[A-Za-z0-9]{3,4}$'),
    subject               text CHECK (length(subject) <= 120),
    priority              text NOT NULL,
    expected_duration_min integer NOT NULL CHECK (expected_duration_min BETWEEN 1 AND 120),
    intent_source         text NOT NULL,
    verification_level    text NOT NULL,
    valid_from            timestamptz NOT NULL,
    valid_until           timestamptz NOT NULL CHECK (valid_until > valid_from),
    deadline              timestamptz,
    preferred_channels    text[] NOT NULL DEFAULT '{}',
    channel_used          text,
    consent_ref           text,
    parent_intent_id      uuid REFERENCES intent.intents(id),
    attempt_count         integer NOT NULL DEFAULT 0,
    language              text NOT NULL CHECK (language IN ('en','ar')),
    proposed_slots        jsonb NOT NULL DEFAULT '[]',
    scheduled_slot        jsonb,
    decline_reason        text,
    outcome_code          text,
    status                text NOT NULL,
    version               integer NOT NULL DEFAULT 1,
    created_at            timestamptz NOT NULL,
    updated_at            timestamptz NOT NULL
);
CREATE INDEX ON intent.intents (tenant_id, created_at DESC);
CREATE INDEX intents_expiry_idx ON intent.intents (valid_until)
    WHERE status IN ('REQUESTED','DELIVERED','ACCEPTED','RESCHEDULED','SCHEDULED');

CREATE TABLE intent.intent_events (
    id          bigserial PRIMARY KEY,
    intent_id   uuid NOT NULL REFERENCES intent.intents(id),
    tenant_id   uuid,
    at          timestamptz NOT NULL,
    from_status text NOT NULL,
    to_status   text NOT NULL,
    actor       text NOT NULL,
    reason      text
);
CREATE INDEX ON intent.intent_events (intent_id, id);

-- platform

CREATE TABLE platform.idempotency_keys (
    tenant_id       uuid NOT NULL,
    key             uuid NOT NULL,
    route           text NOT NULL,
    request_hash    text NOT NULL,
    response_status integer NOT NULL,
    response_body   jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);

CREATE TABLE platform.outbox (
    id           bigserial PRIMARY KEY,
    tenant_id    uuid,
    subject      text NOT NULL,
    payload      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    attempts     integer NOT NULL DEFAULT 0,
    last_error   text,
    dead_at      timestamptz
);
CREATE INDEX outbox_pending_idx ON platform.outbox (id) WHERE published_at IS NULL AND dead_at IS NULL;
CREATE INDEX outbox_dead_idx ON platform.outbox (dead_at) WHERE dead_at IS NOT NULL;

-- audit: append only, hash chained per tenant

CREATE TABLE audit.audit_events (
    id          bigserial PRIMARY KEY,
    event_id    uuid NOT NULL UNIQUE,
    tenant_id   uuid,
    at          timestamptz NOT NULL,
    actor       text NOT NULL,
    action      text NOT NULL,
    object_type text NOT NULL,
    object_id   text NOT NULL,
    reason      text,
    prev_hash   text NOT NULL,
    hash        text NOT NULL
);
CREATE INDEX ON audit.audit_events (tenant_id, id DESC);

CREATE FUNCTION audit.reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit events are append only';
END $$;
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit.audit_events
    FOR EACH ROW EXECUTE FUNCTION audit.reject_change();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit.audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_change();

-- row level security: every tenant table, forced so even table owner obeys it
{RLS}

-- api_clients: readable only by owner (through lookup_api_client); no grants to app or worker.
-- Row level security is enabled but not forced, so the security definer lookup can read it.
ALTER TABLE enterprise.api_clients ENABLE ROW LEVEL SECURITY;

-- grants

GRANT USAGE ON SCHEMA enterprise, intent, platform, audit TO ringsays_app, ringsays_worker;
GRANT EXECUTE ON FUNCTION platform.current_tenant() TO ringsays_app, ringsays_worker;

GRANT SELECT ON enterprise.tenants, enterprise.departments, enterprise.agents,
               enterprise.calling_numbers TO ringsays_app;
GRANT SELECT, INSERT, UPDATE ON enterprise.purpose_codes TO ringsays_app;
GRANT EXECUTE ON FUNCTION enterprise.lookup_api_client(text) TO ringsays_app;
REVOKE ALL ON enterprise.api_clients FROM ringsays_app;

GRANT SELECT, INSERT, UPDATE ON intent.intents TO ringsays_app, ringsays_worker;
GRANT SELECT, INSERT ON intent.intent_events TO ringsays_app, ringsays_worker;
GRANT USAGE ON SEQUENCE intent.intent_events_id_seq TO ringsays_app, ringsays_worker;

GRANT SELECT, INSERT ON platform.idempotency_keys TO ringsays_app;
GRANT INSERT ON platform.outbox TO ringsays_app, ringsays_worker;
GRANT SELECT, UPDATE ON platform.outbox TO ringsays_worker;
GRANT USAGE ON SEQUENCE platform.outbox_id_seq TO ringsays_app, ringsays_worker;

GRANT SELECT, INSERT ON audit.audit_events TO ringsays_app, ringsays_worker;
GRANT USAGE ON SEQUENCE audit.audit_events_id_seq TO ringsays_app, ringsays_worker;
"""

DOWNGRADE = """
DROP SCHEMA audit CASCADE;
DROP SCHEMA platform CASCADE;
DROP SCHEMA intent CASCADE;
DROP SCHEMA enterprise CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE.replace("{RLS}", _rls()))


def downgrade() -> None:
    op.execute(DOWNGRADE)
