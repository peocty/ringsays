"""No role needs SUPERUSER or BYPASSRLS (managed PostgreSQL); system access is an explicit policy."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.integration


def test_no_role_bypasses_row_level_security(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        rows = c.execute(
            text("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname LIKE 'ringsays_%'")
        ).all()
    assert {r.rolname for r in rows} >= {
        "ringsays_owner",
        "ringsays_app",
        "ringsays_worker",
        "ringsays_backoffice",
    }
    assert all(not r.rolsuper and not r.rolbypassrls for r in rows), rows


# Not forced on purpose: owner run SECURITY DEFINER lookups must find a row before the tenant or user is
# known (resolve_token 0002, user_for_phone 0003, client lookup 0001, portal sign in 0004). Any other
# table must force row level security; a new exception has to be added here deliberately.
NOT_FORCED = {"context.context_tokens", "identity.users", "enterprise.api_clients", "enterprise.portal_users"}


def test_every_rls_table_forced_and_has_system_policy_without_app(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        tables = c.execute(
            text(
                "SELECT n.nspname || '.' || c.relname AS tbl, c.relforcerowsecurity AS forced"
                " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE c.relrowsecurity AND c.relkind = 'r'"
            )
        ).all()
        policies = {
            r.tbl: r.roles
            for r in c.execute(
                text(
                    "SELECT schemaname || '.' || tablename AS tbl, roles::text[] AS roles"
                    " FROM pg_policies WHERE policyname = 'system_roles'"
                )
            )
        }
    assert len(tables) >= 20
    for t in tables:
        assert t.forced or t.tbl in NOT_FORCED, f"{t.tbl} must FORCE row level security"
        assert sorted(policies.get(t.tbl, [])) == ["ringsays_backoffice", "ringsays_worker"], t.tbl


def test_backoffice_still_cannot_read_intents_or_identity(database: dict[str, str]) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.exc import ProgrammingError

    eng = create_engine(database["backoffice"])
    try:
        for table in ("intent.intents", "identity.users", "context.context_tokens"):
            with eng.connect() as c, pytest.raises(ProgrammingError):
                c.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))  # noqa: S608 (fixed table names)
    finally:
        eng.dispose()
