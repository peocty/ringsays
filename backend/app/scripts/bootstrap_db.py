"""Prepare a managed PostgreSQL server (Cloud SQL, Amazon RDS, Oracle, Alibaba) for RingSays.

Runs once per environment, and again whenever a password is rotated, as the service's admin user.
That user has CREATEROLE and CREATEDB but is not a superuser, so nothing here needs superuser.
It is idempotent:

1. Create the four roles if missing (none is SUPERUSER, none has BYPASSRLS) and set their passwords.
2. Create the database if missing and hand it to ringsays_owner (migrations then create everything).
3. Only the four roles may connect; PUBLIC loses CONNECT and CREATE.

Passwords come from the environment (Kubernetes Secret, filled from the cloud secret manager):
RINGSAYS_BOOTSTRAP_ADMIN_URL (admin, any database on the server), RINGSAYS_BOOTSTRAP_DATABASE (default
ringsays) and RINGSAYS_DB_PASSWORD_{OWNER,APP,WORKER,BACKOFFICE}. Nothing secret is printed.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROLES = {
    "OWNER": "ringsays_owner",
    "APP": "ringsays_app",
    "WORKER": "ringsays_worker",
    "BACKOFFICE": "ringsays_backoffice",
}
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MIN_PASSWORD = 24


def _q(s: str) -> str:
    """Quote a literal for statements that take no bind parameters (CREATE/ALTER ROLE ... PASSWORD)."""
    return "'" + s.replace("'", "''") + "'"


def bootstrap(
    admin_url: str, database: str, passwords: Mapping[str, str], log: list[str] | None = None
) -> list[str]:
    out = log if log is not None else []
    if not _IDENT.fullmatch(database):
        raise ValueError("database name must be a plain identifier")
    missing = [k for k in ROLES if len(passwords.get(k, "")) < MIN_PASSWORD]
    if missing:
        raise ValueError(f"passwords missing or shorter than {MIN_PASSWORD} characters: {missing}")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as c:
            admin = c.execute(text("SELECT current_user")).scalar_one()
            for key, role in ROLES.items():
                exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
                verb = "ALTER" if exists else "CREATE"
                c.execute(
                    text(
                        f"{verb} ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS "
                        f"PASSWORD {_q(passwords[key])}"
                    )
                )
                out.append(f"role {role}: {'updated' if exists else 'created'}")
            # Admin needs membership to hand the database over (PostgreSQL 16 rules for CREATEROLE).
            c.execute(text(f"GRANT {ROLES['OWNER']} TO {admin}"))
            if not c.execute(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": database}).first():
                c.execute(text(f"CREATE DATABASE {database}"))
                out.append(f"database {database}: created")
            c.execute(text(f"ALTER DATABASE {database} OWNER TO {ROLES['OWNER']}"))
            c.execute(text(f"REVOKE CONNECT, TEMPORARY ON DATABASE {database} FROM PUBLIC"))
            c.execute(text(f"GRANT CONNECT ON DATABASE {database} TO {', '.join(ROLES.values())}"))
            out.append(f"database {database}: owned by {ROLES['OWNER']}, connect limited to RingSays roles")
        db_engine = create_engine(make_url(admin_url).set(database=database), isolation_level="AUTOCOMMIT")
        try:
            with db_engine.connect() as c:
                c.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
        finally:
            db_engine.dispose()
    finally:
        engine.dispose()
    return out


def _value(name: str, default: str = "") -> str:
    """Environment variable, or a file of that name in RINGSAYS_SETTINGS_DIR (secret volume)."""
    secrets_dir = os.environ.get("RINGSAYS_SETTINGS_DIR")
    if secrets_dir:
        path = os.path.join(secrets_dir, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
    return os.environ.get(name, default)


def main() -> None:
    admin_url = _value("RINGSAYS_BOOTSTRAP_ADMIN_URL")
    if not admin_url:
        raise SystemExit("RINGSAYS_BOOTSTRAP_ADMIN_URL is not set")
    database = _value("RINGSAYS_BOOTSTRAP_DATABASE", "ringsays")
    passwords = {k: _value(f"RINGSAYS_DB_PASSWORD_{k}") for k in ROLES}
    try:
        for line in bootstrap(admin_url, database, passwords):
            print(line)
    except ValueError as exc:
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
