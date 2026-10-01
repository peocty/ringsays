"""Delivery attempts, Context Tokens, webhook endpoints and webhook delivery queue.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE SCHEMA context;

-- Context Tokens: only a SHA-256 of the token is stored; the token itself is shown once to the tenant.
CREATE TABLE context.context_tokens (
    token_hash     text PRIMARY KEY,
    tenant_id      uuid NOT NULL REFERENCES enterprise.tenants(id),
    intent_id      uuid NOT NULL REFERENCES intent.intents(id),
    device_id      uuid,
    issued_at      timestamptz NOT NULL,
    expires_at     timestamptz NOT NULL,
    resolve_count  integer NOT NULL DEFAULT 0,
    revoked_at     timestamptz
);
CREATE INDEX ON context.context_tokens (intent_id);
ALTER TABLE context.context_tokens ENABLE ROW LEVEL SECURITY;
-- Not forced: resolve_token below runs as owner and must see a token before tenant is known.
CREATE POLICY tenant_isolation ON context.context_tokens
    USING (tenant_id = platform.current_tenant()) WITH CHECK (tenant_id = platform.current_tenant());

-- Resolve by token hash from a device. First resolve binds token to that device; any other device is
-- refused. Effective time is never earlier than database time, so a caller cannot revive an expired
-- token by passing an old clock. NULL device is refused (NULL comparisons would skip the binding check).
CREATE FUNCTION context.resolve_token(p_hash text, p_device uuid, p_now timestamptz)
RETURNS TABLE (tenant_id uuid, intent_id uuid, outcome text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = context, pg_temp AS $$
DECLARE
    t context.context_tokens%ROWTYPE;
    v_now timestamptz := greatest(coalesce(p_now, now()), now());
BEGIN
    IF p_hash IS NULL OR p_device IS NULL THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, 'unknown'::text; RETURN;
    END IF;
    SELECT * INTO t FROM context.context_tokens WHERE token_hash = p_hash FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, 'unknown'::text; RETURN;
    END IF;
    IF t.revoked_at IS NOT NULL THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, 'revoked'::text; RETURN;
    END IF;
    IF t.expires_at <= v_now THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, 'expired'::text; RETURN;
    END IF;
    IF t.device_id IS NOT NULL AND t.device_id IS DISTINCT FROM p_device THEN
        RETURN QUERY SELECT NULL::uuid, NULL::uuid, 'other_device'::text; RETURN;
    END IF;
    UPDATE context.context_tokens
       SET device_id = p_device, resolve_count = resolve_count + 1
     WHERE token_hash = p_hash;
    RETURN QUERY SELECT t.tenant_id, t.intent_id, 'ok'::text;
END $$;
-- Functions are executable by PUBLIC by default; restrict security definer functions to the API role.
REVOKE EXECUTE ON FUNCTION context.resolve_token(text, uuid, timestamptz) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION enterprise.lookup_api_client(text) FROM PUBLIC;

-- Next time delivery worker should look at an intent; set from valid_from at create, advanced by
-- each delivery decision so waiting or exhausted intents never crowd out due ones.
ALTER TABLE intent.intents ADD COLUMN delivery_next_check_at timestamptz;
UPDATE intent.intents SET delivery_next_check_at = valid_from;
CREATE INDEX intents_delivery_due_idx ON intent.intents (delivery_next_check_at)
    WHERE status = 'REQUESTED';

-- Delivery attempts per intent and channel (fallback ladder trail)
CREATE TABLE intent.delivery_attempts (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid,
    intent_id   uuid NOT NULL REFERENCES intent.intents(id),
    channel     text NOT NULL,
    outcome     text NOT NULL CHECK (outcome IN ('SENDING','SENT','SKIPPED','FAILED','HELD','ERROR')),
    reason      text,
    hold_until  timestamptz,
    at          timestamptz NOT NULL
);
CREATE INDEX ON intent.delivery_attempts (intent_id, id);

-- Webhook endpoints; secret is encrypted with a key held outside database
CREATE TABLE enterprise.webhook_endpoints (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL REFERENCES enterprise.tenants(id),
    url               text NOT NULL CHECK (url ~ '^https://'),
    secret_ciphertext text NOT NULL,
    events            text[] NOT NULL,
    active            boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform.webhook_deliveries (
    id               bigserial PRIMARY KEY,
    tenant_id        uuid NOT NULL,
    endpoint_id      uuid NOT NULL REFERENCES enterprise.webhook_endpoints(id),
    event_id         uuid NOT NULL,
    event_type       text NOT NULL,
    intent_id        uuid NOT NULL,
    payload          jsonb NOT NULL,
    status           text NOT NULL CHECK (status IN ('PENDING','DELIVERED','DEAD')),
    attempts         integer NOT NULL DEFAULT 0,
    next_attempt_at  timestamptz NOT NULL,
    last_status_code integer,
    last_error       text,
    created_at       timestamptz NOT NULL,
    delivered_at     timestamptz,
    UNIQUE (endpoint_id, event_id)
);
CREATE INDEX webhook_due_idx ON platform.webhook_deliveries (next_attempt_at) WHERE status = 'PENDING';
CREATE INDEX ON platform.webhook_deliveries (endpoint_id, intent_id, id);

{RLS}

GRANT USAGE ON SCHEMA context TO ringsays_app, ringsays_worker;
GRANT SELECT, INSERT, UPDATE ON context.context_tokens TO ringsays_app;
GRANT SELECT, UPDATE ON context.context_tokens TO ringsays_worker;
GRANT EXECUTE ON FUNCTION context.resolve_token(text, uuid, timestamptz) TO ringsays_app;

GRANT SELECT, INSERT ON intent.delivery_attempts TO ringsays_app, ringsays_worker;
GRANT UPDATE ON intent.delivery_attempts TO ringsays_worker;
GRANT USAGE ON SEQUENCE intent.delivery_attempts_id_seq TO ringsays_app, ringsays_worker;

GRANT SELECT, INSERT, UPDATE ON enterprise.webhook_endpoints TO ringsays_app;
GRANT SELECT ON enterprise.webhook_endpoints TO ringsays_worker;

GRANT SELECT, INSERT, UPDATE ON platform.webhook_deliveries TO ringsays_app, ringsays_worker;
GRANT USAGE ON SEQUENCE platform.webhook_deliveries_id_seq TO ringsays_app, ringsays_worker;
"""

FORCED = ["intent.delivery_attempts", "enterprise.webhook_endpoints", "platform.webhook_deliveries"]


def _rls() -> str:
    return "\n".join(
        f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;\n"
        f"CREATE POLICY tenant_isolation ON {t} "
        f"USING (tenant_id = platform.current_tenant()) WITH CHECK (tenant_id = platform.current_tenant());"
        for t in FORCED
    )


def upgrade() -> None:
    op.execute(UPGRADE.replace("{RLS}", _rls()))


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE platform.webhook_deliveries;
        DROP TABLE enterprise.webhook_endpoints;
        DROP TABLE intent.delivery_attempts;
        ALTER TABLE intent.intents DROP COLUMN delivery_next_check_at;
        GRANT EXECUTE ON FUNCTION enterprise.lookup_api_client(text) TO PUBLIC;
        DROP SCHEMA context CASCADE;
        """
    )
