"""Identity: RingSays users, devices, sign in, refresh tokens, preferences, consents.

User data is isolated by forced row level security on `app.user_id`. Intents become readable to their
recipient through a second, read only policy matching `intent.intents.to_phone_hash` against the signed
in user's phone hash. Phone numbers are stored only as a keyed hash plus an encrypted copy (for export).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE SCHEMA identity;

CREATE FUNCTION platform.current_user_id() RETURNS uuid LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
$$;
CREATE FUNCTION platform.current_phone_hash() RETURNS text LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.phone_hash', true), '')
$$;
-- When the signed in account was created. Intents created before it belong to an earlier holder of the
-- number (erased account or recycled number) and are never visible to this account.
CREATE FUNCTION platform.current_user_since() RETURNS timestamptz LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.user_since', true), '')::timestamptz
$$;

CREATE TABLE identity.users (
    id               uuid PRIMARY KEY,
    phone_hash       text NOT NULL UNIQUE,
    phone_ciphertext text,
    display_name     text CHECK (length(display_name) <= 80),
    locale           text NOT NULL DEFAULT 'ar' CHECK (locale IN ('en','ar')),
    created_at       timestamptz NOT NULL,
    erased_at        timestamptz
);

CREATE TABLE identity.devices (
    id          uuid PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES identity.users(id),
    platform    text NOT NULL CHECK (platform IN ('IOS','ANDROID')),
    public_key  text NOT NULL,
    app_version text NOT NULL,
    apns_token  text,
    pushkit_token text,
    fcm_token   text,
    created_at  timestamptz NOT NULL,
    revoked_at  timestamptz
);
CREATE INDEX ON identity.devices (user_id) WHERE revoked_at IS NULL;

-- Sign in challenges hold no phone number: keyed hash of phone, keyed hash of code.
CREATE TABLE identity.otp_challenges (
    id          uuid PRIMARY KEY,
    phone_hash  text NOT NULL,
    phone_ciphertext text NOT NULL,  -- needed once to create the user; cleared when consumed
    code_hash   text NOT NULL,
    created_at  timestamptz NOT NULL,
    expires_at  timestamptz NOT NULL,
    attempts    integer NOT NULL DEFAULT 0,
    consumed_at timestamptz
);
CREATE INDEX ON identity.otp_challenges (phone_hash, created_at DESC);

CREATE TABLE identity.refresh_tokens (
    token_hash  text PRIMARY KEY,
    user_id     uuid NOT NULL REFERENCES identity.users(id),
    device_id   uuid NOT NULL REFERENCES identity.devices(id),
    family_id   uuid NOT NULL,
    issued_at   timestamptz NOT NULL,
    expires_at  timestamptz NOT NULL,
    rotated_at  timestamptz,
    revoked_at  timestamptz
);
CREATE INDEX ON identity.refresh_tokens (family_id);

CREATE TABLE identity.preferences (
    user_id    uuid PRIMARY KEY REFERENCES identity.users(id),
    document   jsonb NOT NULL,
    version    integer NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL
);

-- Organisations that have contacted this user, the basis they declared, and the user's withdrawal.
CREATE TABLE identity.consents (
    id           uuid PRIMARY KEY,
    user_id      uuid NOT NULL REFERENCES identity.users(id),
    tenant_id    uuid NOT NULL REFERENCES enterprise.tenants(id),
    purpose      text NOT NULL,
    basis_ref    text,
    granted_at   timestamptz NOT NULL,
    withdrawn_at timestamptz,
    UNIQUE (user_id, tenant_id)
);

-- Find or create user for a phone hash, before any user is signed in. Returns user id only.
CREATE FUNCTION identity.user_for_phone(p_hash text, p_ciphertext text, p_now timestamptz)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = identity, pg_temp AS $$
DECLARE v_id uuid;
BEGIN
    IF p_hash IS NULL OR length(p_hash) <> 64 THEN
        RAISE EXCEPTION 'invalid phone hash';
    END IF;
    -- ON CONFLICT makes two simultaneous first sign ins for one number resolve to the same user.
    INSERT INTO identity.users (id, phone_hash, phone_ciphertext, created_at)
    VALUES (gen_random_uuid(), p_hash, p_ciphertext, greatest(p_now, now()))
    ON CONFLICT (phone_hash) DO NOTHING;
    SELECT id INTO v_id FROM identity.users WHERE phone_hash = p_hash AND erased_at IS NULL;
    RETURN v_id;
END $$;
REVOKE EXECUTE ON FUNCTION identity.user_for_phone(text, text, timestamptz) FROM PUBLIC;

-- Recipient read access to intents addressed to the signed in user's phone.
ALTER TABLE intent.intents ADD COLUMN to_phone_hash text;
CREATE INDEX intents_recipient_idx ON intent.intents (to_phone_hash, created_at DESC);
CREATE POLICY recipient_read ON intent.intents FOR SELECT
    USING (to_phone_hash IS NOT NULL AND to_phone_hash = platform.current_phone_hash()
           AND created_at >= platform.current_user_since());
CREATE POLICY recipient_read ON intent.intent_events FOR SELECT
    USING (EXISTS (SELECT 1 FROM intent.intents i WHERE i.id = intent_id
                   AND i.to_phone_hash = platform.current_phone_hash()
                   AND i.created_at >= platform.current_user_since()));

{RLS}

-- Per user audit chain for account level actions (for example erasure): rows with no tenant whose object
-- is the signed in user. Each user can read and append only their own chain.
CREATE POLICY own_user_audit ON audit.audit_events
    USING (tenant_id IS NULL AND object_type = 'user' AND object_id = platform.current_user_id()::text)
    WITH CHECK (tenant_id IS NULL AND object_type = 'user' AND object_id = platform.current_user_id()::text);

GRANT USAGE ON SCHEMA identity TO ringsays_app, ringsays_worker;
GRANT EXECUTE ON FUNCTION platform.current_user_id() TO ringsays_app, ringsays_worker;
GRANT EXECUTE ON FUNCTION platform.current_phone_hash() TO ringsays_app, ringsays_worker;
GRANT EXECUTE ON FUNCTION platform.current_user_since() TO ringsays_app, ringsays_worker;
GRANT EXECUTE ON FUNCTION identity.user_for_phone(text, text, timestamptz) TO ringsays_app;
GRANT SELECT, UPDATE ON identity.users TO ringsays_app;
GRANT SELECT ON identity.users TO ringsays_worker;
GRANT SELECT, INSERT, UPDATE ON identity.devices TO ringsays_app;
GRANT SELECT ON identity.devices TO ringsays_worker;
GRANT SELECT, INSERT, UPDATE ON identity.otp_challenges TO ringsays_app;
GRANT SELECT, INSERT, UPDATE ON identity.refresh_tokens TO ringsays_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON identity.preferences TO ringsays_app;
GRANT SELECT ON identity.preferences TO ringsays_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON identity.consents TO ringsays_app;
GRANT SELECT, INSERT ON identity.consents TO ringsays_worker;
"""

USER_TABLES = {
    "identity.devices": "user_id",
    "identity.refresh_tokens": "user_id",
    "identity.preferences": "user_id",
    "identity.consents": "user_id",
}


def _rls() -> str:
    parts = [
        # Enabled, not forced: user_for_phone runs as owner and must find or create a user before
        # anyone is signed in. API role (not owner) still sees only its own row.
        "ALTER TABLE identity.users ENABLE ROW LEVEL SECURITY;\n"
        "CREATE POLICY own_user ON identity.users USING (id = platform.current_user_id());"
    ]
    for table, column in USER_TABLES.items():
        parts.append(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;\n"
            f"CREATE POLICY own_rows ON {table} USING ({column} = platform.current_user_id()) "
            f"WITH CHECK ({column} = platform.current_user_id());"
        )
    return "\n".join(parts)


def upgrade() -> None:
    op.execute(UPGRADE.replace("{RLS}", _rls()))


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS own_user_audit ON audit.audit_events;
        DROP POLICY recipient_read ON intent.intent_events;
        DROP POLICY recipient_read ON intent.intents;
        ALTER TABLE intent.intents DROP COLUMN to_phone_hash;
        DROP SCHEMA identity CASCADE;
        DROP FUNCTION IF EXISTS platform.current_user_since();
        DROP FUNCTION platform.current_phone_hash();
        DROP FUNCTION platform.current_user_id();
        """
    )
