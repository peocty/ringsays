"""Bootstrap input checks (no database needed)."""

from __future__ import annotations

import pytest

from app.scripts.bootstrap_db import ROLES, bootstrap


def test_refuses_short_or_missing_passwords() -> None:
    with pytest.raises(ValueError, match="OWNER"):
        bootstrap(
            "postgresql+psycopg://x@127.0.0.1:1/postgres",
            "ringsays",
            {k: "x" * 30 for k in ROLES if k != "OWNER"},
        )
    with pytest.raises(ValueError, match="shorter"):
        bootstrap("postgresql+psycopg://x@127.0.0.1:1/postgres", "ringsays", {k: "short" for k in ROLES})


def test_refuses_odd_database_names() -> None:
    with pytest.raises(ValueError, match="identifier"):
        bootstrap(
            "postgresql+psycopg://x@127.0.0.1:1/postgres", "ringsays; DROP", {k: "x" * 30 for k in ROLES}
        )
