"""Worker may clear a push token the provider reports as dead (app removed, token rotated).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02

Column grant only: the worker can set apns_token or fcm_token, nothing else on devices.
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT UPDATE (apns_token, fcm_token) ON identity.devices TO ringsays_worker")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (apns_token, fcm_token) ON identity.devices FROM ringsays_worker")
