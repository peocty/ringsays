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
    Column("sector", Text, nullable=False),
    Column("suspended_reason", Text),
    Column("status_before_suspension", Text),
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
    Column("created_at", DateTime(timezone=True)),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("reviewed_by", Text),
    Column("review_reason", Text),
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
    Column("proposed_at", DateTime(timezone=True)),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("reviewed_by", Text),
    Column("review_reason", Text),
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
    Column("delivery_next_check_at", DateTime(timezone=True)),
    Column("to_phone_hash", Text),
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

context_tokens = Table(
    "context_tokens",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("intent_id", UUID(as_uuid=True), nullable=False),
    Column("device_id", UUID(as_uuid=True)),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("resolve_count", Integer, nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    schema="context",
)

delivery_attempts = Table(
    "delivery_attempts",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("intent_id", UUID(as_uuid=True), nullable=False),
    Column("channel", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("reason", Text),
    Column("hold_until", DateTime(timezone=True)),
    Column("at", DateTime(timezone=True), nullable=False),
    schema="intent",
)

webhook_endpoints = Table(
    "webhook_endpoints",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("url", Text, nullable=False),
    Column("secret_ciphertext", Text, nullable=False),
    Column("events", ARRAY(Text), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    schema="enterprise",
)

webhook_deliveries = Table(
    "webhook_deliveries",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("endpoint_id", UUID(as_uuid=True), nullable=False),
    Column("event_id", UUID(as_uuid=True), nullable=False),
    Column("event_type", Text, nullable=False),
    Column("intent_id", UUID(as_uuid=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("last_status_code", Integer),
    Column("last_error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("delivered_at", DateTime(timezone=True)),
    schema="platform",
)


users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("phone_hash", Text, nullable=False),
    Column("phone_ciphertext", Text),
    Column("display_name", Text),
    Column("locale", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("erased_at", DateTime(timezone=True)),
    schema="identity",
)

devices = Table(
    "devices",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("platform", Text, nullable=False),
    Column("public_key", Text, nullable=False),
    Column("app_version", Text, nullable=False),
    Column("apns_token", Text),
    Column("pushkit_token", Text),
    Column("fcm_token", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    schema="identity",
)

otp_challenges = Table(
    "otp_challenges",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("phone_hash", Text, nullable=False),
    Column("phone_ciphertext", Text, nullable=False),
    Column("code_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
    schema="identity",
)

refresh_tokens = Table(
    "refresh_tokens",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("device_id", UUID(as_uuid=True), nullable=False),
    Column("family_id", UUID(as_uuid=True), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("rotated_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
    schema="identity",
)

preferences = Table(
    "preferences",
    metadata,
    Column("user_id", UUID(as_uuid=True), primary_key=True),
    Column("document", JSONB, nullable=False),
    Column("version", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema="identity",
)

consents = Table(
    "consents",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("purpose", Text, nullable=False),
    Column("basis_ref", Text),
    Column("granted_at", DateTime(timezone=True), nullable=False),
    Column("withdrawn_at", DateTime(timezone=True)),
    schema="identity",
)


portal_users = Table(
    "portal_users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("email", Text, nullable=False),
    Column("display_name", Text),
    Column("oidc_issuer", Text),
    Column("oidc_subject", Text),
    Column("roles", ARRAY(Text), nullable=False),
    Column("agent_id", Text),
    Column("status", Text, nullable=False),
    Column("invited_by", Text, nullable=False),
    Column("invite_expires_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("last_sign_in_at", DateTime(timezone=True)),
    schema="enterprise",
)

staff_users = Table(
    "staff_users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email", Text, nullable=False),
    Column("display_name", Text),
    Column("oidc_issuer", Text),
    Column("oidc_subject", Text),
    Column("roles", ARRAY(Text), nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", DateTime(timezone=True)),
    Column("last_sign_in_at", DateTime(timezone=True)),
    schema="platform",
)

verification_documents = Table(
    "verification_documents",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("kind", Text, nullable=False),
    Column("reference", Text),
    Column("file_name", Text, nullable=False),
    Column("content_type", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("blob_key", Text, nullable=False),
    Column("uploaded_by", Text, nullable=False),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    schema="enterprise",
)

verification_requests = Table(
    "verification_requests",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("document_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("submitted_by", Text, nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=False),
    Column("decided_by", Text),
    Column("decided_at", DateTime(timezone=True)),
    Column("decision_reason", Text),
    schema="enterprise",
)
