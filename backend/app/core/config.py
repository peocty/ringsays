"""Runtime settings, read from environment. Defaults are for local development with mocks only."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_JWT_SECRET = "local-development-only-secret-change-me-0123456789"  # noqa: S105
LOCAL_WEBHOOK_KEY = "bG9jYWwtZGV2LW9ubHktd2ViaG9vay1rZXktMDAwMDA="  # Fernet key, local only
LOCAL_PHONE_PEPPER = "local-dev-only-pepper"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RINGSAYS_", env_file=".env", extra="ignore")

    environment: str = "local"
    residency_region: str = "KSA"

    # API runtime connects as ringsays_app (row level security enforced).
    database_url: str = "postgresql+psycopg://ringsays_app:ringsays_app@127.0.0.1:5432/ringsays"
    # Background jobs connect as ringsays_worker (bypasses row level security; never used by API).
    worker_database_url: str = "postgresql+psycopg://ringsays_worker:ringsays_worker@127.0.0.1:5432/ringsays"
    # Migrations connect as ringsays_owner.
    migration_database_url: str = "postgresql+psycopg://ringsays_owner:ringsays_owner@127.0.0.1:5432/ringsays"

    redis_url: str = "redis://127.0.0.1:6379/0"
    nats_url: str = "nats://127.0.0.1:4222"
    use_mock_adapters: bool = True

    # Local tokens use HS256 with a static secret. Production must use an asymmetric key held in KMS.
    jwt_secret: str = LOCAL_JWT_SECRET
    jwt_issuer: str = "https://auth.ringsays.local"
    access_token_ttl_s: int = 900

    # Encrypts webhook secrets at rest. Production: key from KMS, rotated.
    webhook_secret_key: str = LOCAL_WEBHOOK_KEY
    # Keyed hash for phone numbers in Redis counters, so Redis never holds a phone number.
    phone_pepper: str = LOCAL_PHONE_PEPPER

    # Rate limits and contact policy (per tenant defaults; tenant overrides come with admin API).
    tenant_creates_per_minute: int = 600
    tenant_urgent_per_day: int = 50
    recipient_intents_per_tenant_per_day: int = 3

    def assert_safe_for_environment(self) -> None:
        if self.environment == "local":
            return
        for name, value, default in [
            ("RINGSAYS_JWT_SECRET", self.jwt_secret, LOCAL_JWT_SECRET),
            ("RINGSAYS_WEBHOOK_SECRET_KEY", self.webhook_secret_key, LOCAL_WEBHOOK_KEY),
            ("RINGSAYS_PHONE_PEPPER", self.phone_pepper, LOCAL_PHONE_PEPPER),
        ]:
            if value == default:
                raise RuntimeError(f"{name} must be set outside local environment")
        if self.environment == "production" and self.use_mock_adapters:
            raise RuntimeError("mock adapters are not allowed in production")


settings = Settings()
