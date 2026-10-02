"""Row level security without BYPASSRLS, so RingSays runs on managed PostgreSQL.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02

Managed services (Cloud SQL, Amazon RDS, Azure, Oracle, Alibaba) give no superuser, and the admin role
there cannot always grant BYPASSRLS. Instead, each row level security table gets one explicit policy
for the two system roles:

- ringsays_worker      background jobs (delivery, expiry, outbox, webhooks) across tenants
- ringsays_backoffice  RingSays reviewers; table grants still keep it away from intents and identity

The API role ringsays_app stays limited to tenant_isolation and own row policies. Table grants, not
these policies, decide what each role may touch at all.
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

SCHEMAS = "('audit','context','enterprise','identity','intent','platform')"

UPGRADE = rf"""
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT n.nspname AS s, c.relname AS t
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relrowsecurity AND c.relkind = 'r' AND n.nspname IN {SCHEMAS}
  LOOP
    EXECUTE format(
      'CREATE POLICY system_roles ON %I.%I TO ringsays_worker, ringsays_backoffice USING (true) WITH CHECK (true)',
      r.s, r.t);
  END LOOP;
END $$;
"""

# Downgrade restores the old model, which needs BYPASSRLS on both system roles again (DBA task).
DOWNGRADE = rf"""
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT schemaname AS s, tablename AS t FROM pg_policies
     WHERE policyname = 'system_roles' AND schemaname IN {SCHEMAS}
  LOOP
    EXECUTE format('DROP POLICY system_roles ON %I.%I', r.s, r.t);
  END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
