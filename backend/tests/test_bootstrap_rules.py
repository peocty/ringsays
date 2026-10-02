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


def test_scram_verifier_matches_postgres_format() -> None:
    import base64

    from app.scripts.bootstrap_db import scram_verifier

    v = scram_verifier("correct horse battery staple", salt=b"0123456789abcdef")
    head, keys = v.split("$", 1)[1].split("$")
    _iterations, salt = head.split(":")
    stored, server = keys.split(":")
    assert v.startswith("SCRAM-SHA-256$4096:")
    assert base64.b64decode(salt) == b"0123456789abcdef"
    assert len(base64.b64decode(stored)) == 32 and len(base64.b64decode(server)) == 32
    assert "correct horse" not in v
    assert scram_verifier("x" * 30) != scram_verifier("x" * 30)  # random salt
