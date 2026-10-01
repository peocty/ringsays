"""SQLAlchemy Core table metadata used for queries. Migrations own the schema; tests check they agree."""

from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

tenants = Table(
    "tenants",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("legal_name_en", Text, nullable=False),
    Column("legal_name_ar", Text, nullable=False),
    Column("cr_number", Text),
    Column("domain", Text),
    Column("residency_region", Text, nullable=False),
    Column("verification_status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    schema="enterprise",
)

departments = Table(
    "departments",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("name_en", Text, nullable=False),
    Column("name_ar", Text, nullable=False),
    schema="enterprise",
)

agents = Table(
    "agents",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("agent_id", Text, primary_key=True),
    Column("department_id", UUID(as_uuid=True), nullable=False),
    Column("display_name_en", Text, nullable=False),
    Column("display_name_ar", Text, nullable=False),
    Column("employee_ref", Text),
    Column("active", Boolean, nullable=False),
    schema="enterprise",
)

calling_numbers = Table(
    "calling_numbers",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("department_id", UUID(as_uuid=True)),
    Column("phone", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("cst_registered", Boolean, nullable=False),
    schema="enterprise",
)

purpose_codes = Table(
    "purpose_codes",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("code", Text, primary_key=True),
    Column("version", Integer, nullable=False),
    Column("display_en", Text, nullable=False),
    Column("display_ar", Text, nullable=False),
    Column("max_priority", Text, nullable=False),
    Column("max_duration_min", Integer, nullable=False),
    Column("allowed_channels", ARRAY(Text), nullable=False),
    Column("status", Text, nullable=False),
    schema="enterprise",
)

intents = Table(
    "intents",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("agent_id", Text),
    Column("department_id", UUID(as_uuid=True)),
    Column("to_phone", Text, nullable=False),
    Column("purpose_code", Text),
    Column("masked_reference", Text),
    Column("subject", Text),
    Column("priority", Text, nullable=False),
    Column("expected_duration_min", Integer, nullable=False),
    Column("intent_source", Text, nullable=False),
    Column("verification_level", Text, nullable=False),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("valid_until", DateTime(timezone=True), nullable=False),
    Column("deadline", DateTime(timezone=True)),
    Column("preferred_channels", ARRAY(Text), nullable=False),
    Column("channel_used", Text),
    Column("consent_ref", Text),
    Column("parent_intent_id", UUID(as_uuid=True)),
    Column("attempt_count", Integer, nullable=False),
    Column("language", Text, nullable=False),
    Column("proposed_slots", JSONB, nullable=False),
    Column("scheduled_slot", JSONB),
    Column("decline_reason", Text),
    Column("outcome_code", Text),
    Column("status", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema="intent",
)

intent_events = Table(
    "intent_events",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("intent_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("from_status", Text, nullable=False),
    Column("to_status", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("reason", Text),
    schema="intent",
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("key", UUID(as_uuid=True), primary_key=True),
    Column("route", Text, nullable=False),
    Column("request_hash", Text, nullable=False),
    Column("response_status", Integer, nullable=False),
    Column("response_body", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    schema="platform",
)

outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("subject", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("published_at", DateTime(timezone=True)),
    Column("attempts", Integer, nullable=False),
    Column("last_error", Text),
    Column("dead_at", DateTime(timezone=True)),
    schema="platform",
    # API role may insert but not read outbox, so INSERT must not use RETURNING.
    implicit_returning=False,
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("event_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("object_type", Text, nullable=False),
    Column("object_id", Text, nullable=False),
    Column("reason", Text),
    Column("prev_hash", Text, nullable=False),
    Column("hash", Text, nullable=False),
    schema="audit",
)
