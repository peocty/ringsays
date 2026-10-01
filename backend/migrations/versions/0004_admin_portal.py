"""Admin portal and back office: portal users, staff, verification evidence, review fields, guards.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01

Two kinds of write protection work together:

- Column grants: the API role may change only the columns a tenant is allowed to edit.
- Guard triggers: where a tenant may change a status column at all (retire a purpose code, revoke a
  calling number), the trigger allows only that one transition, and any new row must start in review.

So even a defect in the tenant facing API cannot approve a purpose code, verify a calling number or
verify a tenant. Only the back office role, used by the internal back office deployment, can.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = r"""
-- tenants: sector drives receiver rules; suspension reason shown to tenant
ALTER TABLE enterprise.tenants
    ADD COLUMN sector text NOT NULL DEFAULT 'BANK'
        CHECK (sector IN ('BANK','INSURANCE','FINANCE','GOVERNMENT')),
    ADD COLUMN suspended_reason text,
    ADD COLUMN status_before_suspension text CHECK (status_before_suspension IN ('PENDING','VERIFIED'));

ALTER TABLE enterprise.purpose_codes
    ADD COLUMN proposed_at   timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN reviewed_at   timestamptz,
    ADD COLUMN reviewed_by   text,
    ADD COLUMN review_reason text;

ALTER TABLE enterprise.calling_numbers
    ADD COLUMN created_at    timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN reviewed_at   timestamptz,
    ADD COLUMN reviewed_by   text,
    ADD COLUMN review_reason text;
-- A business number can be verified for one organisation only.
CREATE UNIQUE INDEX calling_numbers_one_verified_owner ON enterprise.calling_numbers (phone)
    WHERE status = 'VERIFIED';

ALTER TABLE enterprise.api_clients
    ADD COLUMN label      text NOT NULL DEFAULT 'default' CHECK (length(label) BETWEEN 1 AND 80),
    ADD COLUMN created_by text,
    ADD COLUMN revoked_at timestamptz;

-- portal users: people at a tenant. Identity provider proves who they are; roles live here.
CREATE TABLE enterprise.portal_users (
    id                uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL REFERENCES enterprise.tenants(id),
    email             text NOT NULL CHECK (email = lower(email) AND length(email) <= 254),
    display_name      text CHECK (length(display_name) <= 120),
    oidc_issuer       text,
    oidc_subject      text,
    roles             text[] NOT NULL CHECK (
        cardinality(roles) > 0 AND
        roles <@ ARRAY['TENANT_ADMIN','INTEGRATION_ADMIN','SUPERVISOR','AGENT','COMPLIANCE']::text[]),
    agent_id          text,
    status            text NOT NULL CHECK (status IN ('INVITED','ACTIVE','DISABLED')),
    invited_by        text NOT NULL,
    invite_expires_at timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_sign_in_at   timestamptz,
    UNIQUE (tenant_id, email),
    UNIQUE (tenant_id, oidc_issuer, oidc_subject),
    FOREIGN KEY (tenant_id, agent_id) REFERENCES enterprise.agents (tenant_id, agent_id),
    CHECK (status <> 'ACTIVE' OR oidc_subject IS NOT NULL),
    CHECK (NOT ('AGENT' = ANY(roles)) OR agent_id IS NOT NULL)
);
CREATE INDEX portal_users_subject_idx ON enterprise.portal_users (oidc_issuer, oidc_subject);
CREATE INDEX portal_users_invite_idx ON enterprise.portal_users (email) WHERE status = 'INVITED';

-- RingSays staff (back office). No tenant; readable only by back office role.
CREATE TABLE platform.staff_users (
    id              uuid PRIMARY KEY,
    email           text NOT NULL UNIQUE CHECK (email = lower(email)),
    display_name    text,
    oidc_issuer     text,
    oidc_subject    text,
    roles           text[] NOT NULL CHECK (
        cardinality(roles) > 0 AND roles <@ ARRAY['RS_REVIEWER','RS_ADMIN']::text[]),
    status          text NOT NULL CHECK (status IN ('ACTIVE','DISABLED')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_sign_in_at timestamptz,
    UNIQUE (oidc_issuer, oidc_subject)
);

-- verification evidence: files live in object storage; rows hold metadata and checksum
CREATE TABLE enterprise.verification_documents (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES enterprise.tenants(id),
    kind         text NOT NULL CHECK (kind IN ('COMMERCIAL_REGISTRATION','REGULATOR_LICENCE',
                                               'AUTHORISATION_LETTER','DOMAIN_PROOF','OTHER')),
    reference    text CHECK (length(reference) <= 80),
    file_name    text NOT NULL CHECK (length(file_name) <= 200),
    content_type text NOT NULL CHECK (content_type IN ('application/pdf','image/png','image/jpeg')),
    size_bytes   integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 5242880),
    sha256       text NOT NULL,
    blob_key     text NOT NULL UNIQUE,
    uploaded_by  text NOT NULL,
    uploaded_at  timestamptz NOT NULL
);
CREATE INDEX ON enterprise.verification_documents (tenant_id);

CREATE TABLE enterprise.verification_requests (
    id              uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL REFERENCES enterprise.tenants(id),
    status          text NOT NULL CHECK (status IN ('SUBMITTED','APPROVED','REJECTED')),
    document_ids    uuid[] NOT NULL,
    submitted_by    text NOT NULL,
    submitted_at    timestamptz NOT NULL,
    decided_by      text,
    decided_at      timestamptz,
    decision_reason text,
    CHECK ((status = 'SUBMITTED') = (decided_at IS NULL))
);
CREATE UNIQUE INDEX verification_one_open ON enterprise.verification_requests (tenant_id)
    WHERE status = 'SUBMITTED';
CREATE INDEX verification_queue_idx ON enterprise.verification_requests (submitted_at)
    WHERE status = 'SUBMITTED';

-- Guards: what any role other than the owner (migrations, seed) and the back office may do.
-- They raise SQLSTATE RSG01, which the API maps to 409; a real permission error stays a 500.
-- Allow list, not deny list: a login role that is a member of ringsays_app is guarded too.
CREATE FUNCTION enterprise.guard_trusted_role() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT current_user IN ('ringsays_owner', 'ringsays_backoffice')
$$;

CREATE FUNCTION enterprise.guard_reviewed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF enterprise.guard_trusted_role() THEN
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME = 'purpose_codes' THEN
        IF TG_OP = 'INSERT' AND (NEW.status <> 'PENDING_REVIEW' OR NEW.reviewed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'new purpose codes must enter review' USING ERRCODE = 'RSG01';
        END IF;
        IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status AND NEW.status <> 'RETIRED' THEN
            RAISE EXCEPTION 'tenant may only retire a purpose code' USING ERRCODE = 'RSG01';
        END IF;
    ELSIF TG_TABLE_NAME = 'calling_numbers' THEN
        IF TG_OP = 'INSERT' AND (NEW.status <> 'PENDING_VERIFICATION' OR NEW.reviewed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'new calling numbers must enter verification' USING ERRCODE = 'RSG01';
        END IF;
        IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status AND NEW.status <> 'REVOKED' THEN
            RAISE EXCEPTION 'tenant may only revoke a calling number' USING ERRCODE = 'RSG01';
        END IF;
    ELSIF TG_TABLE_NAME = 'verification_requests' THEN
        IF TG_OP = 'INSERT' AND (NEW.status <> 'SUBMITTED' OR NEW.decided_by IS NOT NULL) THEN
            RAISE EXCEPTION 'verification requests start as submitted' USING ERRCODE = 'RSG01';
        END IF;
    ELSIF TG_TABLE_NAME = 'tenants' THEN
        -- Profile is what RingSays verified; it may change only while pending and not under review.
        IF (NEW.legal_name_en, NEW.legal_name_ar, NEW.cr_number, NEW.domain)
           IS DISTINCT FROM (OLD.legal_name_en, OLD.legal_name_ar, OLD.cr_number, OLD.domain)
           AND (OLD.verification_status <> 'PENDING' OR EXISTS (
                SELECT 1 FROM enterprise.verification_requests r
                 WHERE r.tenant_id = OLD.id AND r.status = 'SUBMITTED')) THEN
            RAISE EXCEPTION 'profile is locked' USING ERRCODE = 'RSG01';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER guard_reviewed BEFORE INSERT OR UPDATE ON enterprise.purpose_codes
    FOR EACH ROW EXECUTE FUNCTION enterprise.guard_reviewed();
CREATE TRIGGER guard_reviewed BEFORE INSERT OR UPDATE ON enterprise.calling_numbers
    FOR EACH ROW EXECUTE FUNCTION enterprise.guard_reviewed();
CREATE TRIGGER guard_reviewed BEFORE INSERT ON enterprise.verification_requests
    FOR EACH ROW EXECUTE FUNCTION enterprise.guard_reviewed();
CREATE TRIGGER guard_reviewed BEFORE UPDATE ON enterprise.tenants
    FOR EACH ROW EXECUTE FUNCTION enterprise.guard_reviewed();

-- Evidence may be added or removed only while the tenant is pending and nothing is under review,
-- and evidence a request refers to is never removed (the reviewer decided on it).
CREATE FUNCTION enterprise.guard_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    t uuid := coalesce(NEW.tenant_id, OLD.tenant_id);
BEGIN
    IF enterprise.guard_trusted_role() THEN
        RETURN coalesce(NEW, OLD);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM enterprise.tenants x WHERE x.id = t AND x.verification_status = 'PENDING')
       OR EXISTS (SELECT 1 FROM enterprise.verification_requests r
                   WHERE r.tenant_id = t AND r.status = 'SUBMITTED') THEN
        RAISE EXCEPTION 'evidence is locked' USING ERRCODE = 'RSG01';
    END IF;
    IF TG_OP = 'DELETE' AND EXISTS (
        SELECT 1 FROM enterprise.verification_requests r
         WHERE r.tenant_id = t AND r.status IN ('SUBMITTED', 'APPROVED') AND OLD.id = ANY (r.document_ids)) THEN
        RAISE EXCEPTION 'evidence is referenced by a request' USING ERRCODE = 'RSG01';
    END IF;
    RETURN coalesce(NEW, OLD);
END $$;
CREATE TRIGGER guard_evidence BEFORE INSERT OR DELETE ON enterprise.verification_documents
    FOR EACH ROW EXECUTE FUNCTION enterprise.guard_evidence();

-- Sign in: bind invitations for a verified email, then return memberships for this identity.
-- Runs before any tenant is known, so it is a security definer function returning only rows that
-- belong to the caller's own identity (issuer and subject from a verified token).
CREATE FUNCTION enterprise.portal_sign_in(
    p_issuer text, p_subject text, p_email text, p_email_verified boolean, p_now timestamptz)
RETURNS TABLE (user_id uuid, tenant_id uuid, roles text[], agent_id text, display_name text,
               newly_bound boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = enterprise, pg_temp AS $$
DECLARE
    bound uuid[] := '{}';
BEGIN
    IF p_issuer IS NULL OR p_subject IS NULL OR length(p_subject) = 0 THEN
        RETURN;
    END IF;
    IF p_email_verified AND p_email IS NOT NULL THEN
        WITH b AS (
            UPDATE enterprise.portal_users u
               SET oidc_issuer = p_issuer, oidc_subject = p_subject, status = 'ACTIVE'
             WHERE u.email = lower(p_email) AND u.status = 'INVITED' AND u.oidc_subject IS NULL
               AND (u.invite_expires_at IS NULL OR u.invite_expires_at > p_now)
               AND NOT EXISTS (SELECT 1 FROM enterprise.portal_users o
                                WHERE o.tenant_id = u.tenant_id AND o.oidc_issuer = p_issuer
                                  AND o.oidc_subject = p_subject)
            RETURNING u.id)
        SELECT coalesce(array_agg(b.id), '{}') INTO bound FROM b;
    END IF;
    UPDATE enterprise.portal_users u SET last_sign_in_at = p_now
     WHERE u.oidc_issuer = p_issuer AND u.oidc_subject = p_subject AND u.status = 'ACTIVE';
    RETURN QUERY
        SELECT u.id, u.tenant_id, u.roles, u.agent_id, u.display_name, u.id = ANY (bound)
          FROM enterprise.portal_users u
         WHERE u.oidc_issuer = p_issuer AND u.oidc_subject = p_subject AND u.status = 'ACTIVE'
         ORDER BY u.created_at;
END $$;
REVOKE EXECUTE ON FUNCTION enterprise.portal_sign_in(text, text, text, boolean, timestamptz) FROM PUBLIC;

-- API credentials, managed by tenant through functions scoped to the transaction's tenant.
-- The API role still cannot read api_clients (secret hashes) directly.
CREATE FUNCTION enterprise.tenant_api_clients()
RETURNS TABLE (client_id text, label text, scopes text[], active boolean,
               created_at timestamptz, revoked_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = enterprise, pg_temp AS $$
    SELECT c.client_id, c.label, c.scopes, c.active, c.created_at, c.revoked_at
      FROM enterprise.api_clients c
     WHERE c.tenant_id = platform.current_tenant()
     ORDER BY c.created_at, c.client_id
$$;

CREATE FUNCTION enterprise.create_tenant_api_client(
    p_client_id text, p_secret_hash text, p_scopes text[], p_label text, p_created_by text,
    p_max_active integer)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = enterprise, pg_temp AS $$
DECLARE
    t uuid := platform.current_tenant();
    n integer;
BEGIN
    IF t IS NULL THEN
        RAISE EXCEPTION 'tenant not set' USING ERRCODE = '42501';
    END IF;
    IF cardinality(p_scopes) = 0 OR NOT p_scopes <@
       ARRAY['intents:write','intents:read','catalogue:read','catalogue:write']::text[] THEN
        RAISE EXCEPTION 'invalid scopes' USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('api_clients:' || t::text));
    SELECT count(*) INTO n FROM enterprise.api_clients c WHERE c.tenant_id = t AND c.active;
    IF n >= p_max_active THEN
        RETURN false;
    END IF;
    INSERT INTO enterprise.api_clients (client_id, tenant_id, secret_hash, scopes, label, created_by)
    VALUES (p_client_id, t, p_secret_hash, p_scopes, p_label, p_created_by);
    RETURN true;
END $$;

CREATE FUNCTION enterprise.revoke_tenant_api_client(p_client_id text, p_now timestamptz)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = enterprise, pg_temp AS $$
BEGIN
    UPDATE enterprise.api_clients c
       SET active = false, revoked_at = coalesce(c.revoked_at, p_now)
     WHERE c.client_id = p_client_id AND c.tenant_id = platform.current_tenant();
    RETURN FOUND;
END $$;
REVOKE EXECUTE ON FUNCTION enterprise.tenant_api_clients() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION enterprise.create_tenant_api_client(text, text, text[], text, text, integer)
    FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION enterprise.revoke_tenant_api_client(text, timestamptz) FROM PUBLIC;

-- row level security on new tenant tables
ALTER TABLE enterprise.verification_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE enterprise.verification_documents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON enterprise.verification_documents
    USING (tenant_id = platform.current_tenant()) WITH CHECK (tenant_id = platform.current_tenant());
ALTER TABLE enterprise.verification_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE enterprise.verification_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON enterprise.verification_requests
    USING (tenant_id = platform.current_tenant()) WITH CHECK (tenant_id = platform.current_tenant());
-- portal_users: enabled, not forced, so the owner's sign in function can bind across tenants;
-- the API role (not owner) is still limited to the transaction's tenant.
ALTER TABLE enterprise.portal_users ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON enterprise.portal_users
    USING (tenant_id = platform.current_tenant()) WITH CHECK (tenant_id = platform.current_tenant());

-- API role grants (tenant facing)
GRANT UPDATE (legal_name_en, legal_name_ar, cr_number, domain) ON enterprise.tenants TO ringsays_app;
GRANT INSERT ON enterprise.departments TO ringsays_app;
GRANT UPDATE (name_en, name_ar) ON enterprise.departments TO ringsays_app;
GRANT INSERT ON enterprise.agents TO ringsays_app;
GRANT UPDATE (department_id, display_name_en, display_name_ar, employee_ref, active)
    ON enterprise.agents TO ringsays_app;
GRANT INSERT ON enterprise.calling_numbers TO ringsays_app;
GRANT UPDATE (status) ON enterprise.calling_numbers TO ringsays_app;
REVOKE UPDATE ON enterprise.purpose_codes FROM ringsays_app;
GRANT UPDATE (status) ON enterprise.purpose_codes TO ringsays_app;
GRANT SELECT, INSERT ON enterprise.portal_users TO ringsays_app;
GRANT UPDATE (roles, agent_id, status, display_name, invited_by, invite_expires_at)
    ON enterprise.portal_users TO ringsays_app;
GRANT SELECT, INSERT, DELETE ON enterprise.verification_documents TO ringsays_app;
GRANT SELECT, INSERT ON enterprise.verification_requests TO ringsays_app;
GRANT SELECT ON platform.webhook_deliveries TO ringsays_app;
GRANT EXECUTE ON FUNCTION enterprise.portal_sign_in(text, text, text, boolean, timestamptz) TO ringsays_app;
GRANT EXECUTE ON FUNCTION enterprise.tenant_api_clients() TO ringsays_app;
GRANT EXECUTE ON FUNCTION enterprise.create_tenant_api_client(text, text, text[], text, text, integer)
    TO ringsays_app;
GRANT EXECUTE ON FUNCTION enterprise.revoke_tenant_api_client(text, timestamptz) TO ringsays_app;

-- Worker reads tenant sector for receiver rules
GRANT SELECT ON enterprise.tenants TO ringsays_worker;

-- Back office role: organisation, catalogue, verification and audit only. No intents, no identity.
GRANT USAGE ON SCHEMA enterprise, platform, audit TO ringsays_backoffice;
GRANT SELECT, INSERT ON enterprise.tenants TO ringsays_backoffice;
GRANT UPDATE (verification_status, suspended_reason, status_before_suspension) ON enterprise.tenants
    TO ringsays_backoffice;
GRANT SELECT ON enterprise.departments TO ringsays_backoffice;
GRANT SELECT ON enterprise.purpose_codes TO ringsays_backoffice;
GRANT UPDATE (status, reviewed_at, reviewed_by, review_reason) ON enterprise.purpose_codes
    TO ringsays_backoffice;
GRANT SELECT ON enterprise.calling_numbers TO ringsays_backoffice;
GRANT UPDATE (status, reviewed_at, reviewed_by, review_reason) ON enterprise.calling_numbers
    TO ringsays_backoffice;
GRANT SELECT, INSERT ON enterprise.portal_users TO ringsays_backoffice;
GRANT SELECT ON enterprise.verification_documents TO ringsays_backoffice;
GRANT SELECT ON enterprise.verification_requests TO ringsays_backoffice;
GRANT UPDATE (status, decided_by, decided_at, decision_reason) ON enterprise.verification_requests
    TO ringsays_backoffice;
GRANT SELECT ON platform.staff_users TO ringsays_backoffice;
GRANT UPDATE (oidc_issuer, oidc_subject, last_sign_in_at) ON platform.staff_users TO ringsays_backoffice;
GRANT SELECT, INSERT ON audit.audit_events TO ringsays_backoffice;
GRANT USAGE ON SEQUENCE audit.audit_events_id_seq TO ringsays_backoffice;
"""

DOWNGRADE = r"""
REVOKE ALL ON ALL TABLES IN SCHEMA enterprise, platform, audit FROM ringsays_backoffice;
REVOKE ALL ON SCHEMA enterprise, platform, audit FROM ringsays_backoffice;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA audit FROM ringsays_backoffice;
DROP FUNCTION enterprise.revoke_tenant_api_client(text, timestamptz);
DROP FUNCTION enterprise.create_tenant_api_client(text, text, text[], text, text, integer);
DROP FUNCTION enterprise.tenant_api_clients();
DROP FUNCTION enterprise.portal_sign_in(text, text, text, boolean, timestamptz);
DROP TRIGGER guard_evidence ON enterprise.verification_documents;
DROP FUNCTION enterprise.guard_evidence();
DROP TRIGGER guard_reviewed ON enterprise.tenants;
DROP TRIGGER guard_reviewed ON enterprise.verification_requests;
DROP TRIGGER guard_reviewed ON enterprise.calling_numbers;
DROP TRIGGER guard_reviewed ON enterprise.purpose_codes;
DROP FUNCTION enterprise.guard_reviewed();
DROP FUNCTION enterprise.guard_trusted_role();
REVOKE UPDATE (legal_name_en, legal_name_ar, cr_number, domain) ON enterprise.tenants FROM ringsays_app;
REVOKE INSERT, UPDATE ON enterprise.departments FROM ringsays_app;
REVOKE INSERT, UPDATE ON enterprise.agents FROM ringsays_app;
REVOKE INSERT, UPDATE ON enterprise.calling_numbers FROM ringsays_app;
REVOKE SELECT ON enterprise.tenants FROM ringsays_worker;
DROP TABLE enterprise.verification_requests;
DROP TABLE enterprise.verification_documents;
DROP TABLE platform.staff_users;
DROP TABLE enterprise.portal_users;
GRANT UPDATE ON enterprise.purpose_codes TO ringsays_app;
ALTER TABLE enterprise.api_clients DROP COLUMN revoked_at, DROP COLUMN created_by, DROP COLUMN label;
DROP INDEX enterprise.calling_numbers_one_verified_owner;
ALTER TABLE enterprise.calling_numbers DROP COLUMN review_reason, DROP COLUMN reviewed_by,
    DROP COLUMN reviewed_at, DROP COLUMN created_at;
ALTER TABLE enterprise.purpose_codes DROP COLUMN review_reason, DROP COLUMN reviewed_by,
    DROP COLUMN reviewed_at, DROP COLUMN proposed_at;
ALTER TABLE enterprise.tenants DROP COLUMN status_before_suspension, DROP COLUMN suspended_reason,
    DROP COLUMN sector;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
