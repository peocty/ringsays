"""Alembic environment. Runs as ringsays_owner using RINGSAYS_MIGRATION_DATABASE_URL."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from app.core.config import settings

url = context.config.get_main_option("sqlalchemy.url") or settings.migration_database_url


def run() -> None:
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, version_table_schema="public")
        with context.begin_transaction():
            context.run_migrations()


run()
