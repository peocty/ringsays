"""Seed a demo tenant for local development and tests. MOCK DATA ONLY: never run against production.

Usage: python -m app.scripts.seed
Prints client id and secret for the demo tenant once.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import Engine, create_engine, insert, text

from app.core.config import settings
from app.core.secrets import generate_secret, hash_secret
from app.core.tables import agents, calling_numbers, departments, purpose_codes, tenants

SEED_FILE = Path(__file__).resolve().parents[3] / "contracts/purpose-codes/seed.yaml"
ALL_SCOPES = ["intents:write", "intents:read", "catalogue:read", "catalogue:write"]


@dataclass(frozen=True, slots=True)
class SeededTenant:
    tenant_id: UUID
    department_id: UUID
    agent_id: str
    client_id: str
    client_secret: str


def seed_tenant(
    owner_engine: Engine,
    *,
    name_en: str = "Mock Bank (demo tenant)",
    name_ar: str = "بنك تجريبي",
    verification_status: str = "VERIFIED",
    verified_number: bool = True,
    scopes: list[str] | None = None,
) -> SeededTenant:
    tenant_id, dept_id = uuid4(), uuid4()
    agent_id = "agt_demo_01"
    client_id = f"cli_{tenant_id.hex[:12]}"
    secret = generate_secret()
    codes = yaml.safe_load(SEED_FILE.read_text())["codes"]
    with owner_engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
        conn.execute(
            insert(tenants).values(
                id=tenant_id,
                legal_name_en=name_en,
                legal_name_ar=name_ar,
                residency_region="KSA",
                verification_status=verification_status,
            )
        )
        conn.execute(
            insert(departments).values(
                id=dept_id, tenant_id=tenant_id, name_en="Home Finance", name_ar="التمويل العقاري"
            )
        )
        conn.execute(
            insert(agents).values(
                tenant_id=tenant_id,
                agent_id=agent_id,
                department_id=dept_id,
                display_name_en="Demo Agent",
                display_name_ar="موظف تجريبي",
                active=True,
            )
        )
        conn.execute(
            insert(calling_numbers).values(
                id=uuid4(),
                tenant_id=tenant_id,
                department_id=dept_id,
                phone="+966110000000",
                status="VERIFIED" if verified_number else "PENDING_VERIFICATION",
                cst_registered=True,
            )
        )
        for c in codes:
            conn.execute(
                insert(purpose_codes).values(
                    tenant_id=tenant_id,
                    code=c["code"],
                    version=1,
                    display_en=c["display_text"]["en"],
                    display_ar=c["display_text"]["ar"],
                    max_priority=c["max_priority"],
                    max_duration_min=c["max_duration_min"],
                    allowed_channels=c["allowed_channels"],
                    status="APPROVED",
                )
            )
        conn.execute(
            text(
                "INSERT INTO enterprise.api_clients (client_id, tenant_id, secret_hash, scopes) "
                "VALUES (:c, :t, :h, :s)"
            ),
            {"c": client_id, "t": tenant_id, "h": hash_secret(secret), "s": scopes or ALL_SCOPES},
        )
    return SeededTenant(tenant_id, dept_id, agent_id, client_id, secret)


def main() -> None:
    seeded = seed_tenant(create_engine(settings.migration_database_url))
    print(f"MOCK tenant {seeded.tenant_id}")
    print(f"client_id={seeded.client_id}")
    print(f"client_secret={seeded.client_secret}  (shown once)")


if __name__ == "__main__":
    main()
