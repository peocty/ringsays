"""Webhook URL check also accepts a receiver on the same machine (http://127.0.0.1 or localhost).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01

Used only by the local environment (mock bank, stage 7). Outside local, the API refuses such URLs
(`validate_url`) and the production sender refuses any non public address, so a loopback row can
never be delivered there.
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE = r"""
ALTER TABLE enterprise.webhook_endpoints DROP CONSTRAINT webhook_endpoints_url_check;
ALTER TABLE enterprise.webhook_endpoints ADD CONSTRAINT webhook_endpoints_url_check
    CHECK (url ~ '^https://' OR url ~* '^http://(127\.0\.0\.1|localhost)(:[0-9]{1,5})?/');
"""

DOWNGRADE = r"""
-- Loopback endpoints exist only in local databases; remove them (and their deliveries) across tenants.
ALTER TABLE platform.webhook_deliveries NO FORCE ROW LEVEL SECURITY;
ALTER TABLE enterprise.webhook_endpoints NO FORCE ROW LEVEL SECURITY;
DELETE FROM platform.webhook_deliveries d USING enterprise.webhook_endpoints e
 WHERE d.endpoint_id = e.id AND e.url !~ '^https://';
DELETE FROM enterprise.webhook_endpoints WHERE url !~ '^https://';
ALTER TABLE platform.webhook_deliveries FORCE ROW LEVEL SECURITY;
ALTER TABLE enterprise.webhook_endpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE enterprise.webhook_endpoints DROP CONSTRAINT webhook_endpoints_url_check;
ALTER TABLE enterprise.webhook_endpoints ADD CONSTRAINT webhook_endpoints_url_check CHECK (url ~ '^https://');
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
