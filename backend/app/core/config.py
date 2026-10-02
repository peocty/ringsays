"""Runtime settings, read from environment. Defaults are for local development with mocks only."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_JWT_SECRET = "local-development-only-secret-change-me-0123456789"  # noqa: S105
LOCAL_WEBHOOK_KEY = "bG9jYWwtZGV2LW9ubHktd2ViaG9vay1rZXktMDAwMDA="  # Fernet key, local only
LOCAL_PHONE_PEPPER = "local-dev-only-pepper"
DEV_OIDC_ISSUER = "http://127.0.0.1:8000/dev/oidc"
LOCAL_BACKOFFICE_PASSWORD = "ringsays_backoffice:ringsays_backoffice@"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RINGSAYS_", env_file=".env", extra="ignore")

    # Fails closed: without RINGSAYS_ENVIRONMENT=local the safety checks below run, so a forgotten
    # variable can never expose MOCK sign in or local secrets.
    environment: str = "production"
    residency_region: str = "KSA"

    # API runtime connects as ringsays_app (row level security enforced).
    database_url: str = "postgresql+psycopg://ringsays_app:ringsays_app@127.0.0.1:5432/ringsays"
    # Background jobs connect as ringsays_worker (bypasses row level security; never used by API).
    worker_database_url: str = "postgresql+psycopg://ringsays_worker:ringsays_worker@127.0.0.1:5432/ringsays"
    # Internal back office deployment only (RingSays reviewers). Tenant facing deployments set
    # RINGSAYS_BACKOFFICE_ENABLED=false and are not given this credential.
    backoffice_database_url: str = (
        "postgresql+psycopg://ringsays_backoffice:ringsays_backoffice@127.0.0.1:5432/ringsays"
    )
    backoffice_enabled: bool = True
    # Migrations connect as ringsays_owner.
    migration_database_url: str = "postgresql+psycopg://ringsays_owner:ringsays_owner@127.0.0.1:5432/ringsays"

    redis_url: str = "redis://127.0.0.1:6379/0"
    nats_url: str = "nats://127.0.0.1:4222"
    nats_stream_replicas: int = 1  # 3 on a three node production cluster
    nats_stream_max_age_days: int = 7
    use_mock_adapters: bool = True

    # Real providers (used when use_mock_adapters is false). SMS: a provider with a CST registered
    # sender ID in the Kingdom. Push: Firebase project for Android, APNs signing key for iOS.
    sms_provider: str = "mock"  # mock | taqnyat | unifonic
    sms_sender_id: str = ""
    sms_api_key: str = ""  # Taqnyat bearer token or Unifonic AppSid (secret)
    fcm_project_id: str = ""
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_private_key: str = ""  # .p8 contents (secret)
    apns_topic: str = "com.peocit.ringsays"
    apns_sandbox: bool = False

    # Local tokens use HS256 with a static secret. Production must use an asymmetric key held in KMS.
    jwt_secret: str = LOCAL_JWT_SECRET
    jwt_issuer: str = "https://auth.ringsays.local"
    access_token_ttl_s: int = 900

    # Encrypts webhook secrets at rest. Production: key from KMS, rotated.
    webhook_secret_key: str = LOCAL_WEBHOOK_KEY
    # Keyed hash for phone numbers in Redis counters, so Redis never holds a phone number.
    phone_pepper: str = LOCAL_PHONE_PEPPER

    # Portal and back office sign in (OpenID Connect). Tenant people and RingSays staff may use different
    # identity providers. JWKS URL None means: discover from issuer. Local uses the built in MOCK issuer.
    admin_oidc_issuer: str = DEV_OIDC_ISSUER
    admin_oidc_jwks_url: str | None = None
    staff_oidc_issuer: str = DEV_OIDC_ISSUER
    staff_oidc_jwks_url: str | None = None
    admin_oidc_audience: str = "ringsays-admin-api"
    # Identity providers that do not send email_verified but only issue tokens for accounts whose email
    # they control (for example a bank's own Microsoft Entra ID tenant). Their emails count as verified.
    oidc_email_trusted_issuers: list[str] = []
    # MOCK sign in. Unset means: on in local environment only.
    dev_oidc_enabled: bool | None = None
    dev_oidc_token_ttl_s: int = 900  # MOCK issuer access token lifetime (short, like bank identity providers)
    portal_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        # Mobile app web preview (Expo), local development and browser tests only.
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        # Mock bank customer app web preview (SDK sample, stage 7).
        "http://localhost:8082",
        "http://127.0.0.1:8082",
    ]
    portal_redirect_uris: list[str] = [
        "http://localhost:5173/auth/callback",
        "http://127.0.0.1:5173/auth/callback",
        "http://localhost:4173/auth/callback",
        "http://127.0.0.1:4173/auth/callback",
    ]
    # Worker liveness file, touched after every pass (Kubernetes liveness probe reads its age).
    worker_heartbeat_file: str | None = "/tmp/worker-alive"  # noqa: S108
    # CIDRs of load balancer proxies whose X-Forwarded-For is believed (GKE: the proxy only subnet).
    trusted_proxies: list[str] = []
    # Verification evidence. Local: files on disk (MOCK object storage). Production: S3 compatible
    # storage in region with server side encryption.
    blob_backend: str = "local"  # local (MOCK) | gcs | s3
    blob_dir: str = ".local/blobs"
    blob_bucket: str = ""
    blob_prefix: str = "evidence/"
    # GCS: regional endpoint keeps data in Kingdom (https://storage.me-central2.rep.googleapis.com).
    # S3 compatible (OCI, Alibaba, AWS): endpoint URL and region of the in Kingdom bucket.
    blob_endpoint: str | None = None
    blob_region: str | None = None
    api_clients_max_active: int = 10
    webhook_endpoints_max_active: int = 5
    invite_ttl_days: int = 14

    # Rate limits and contact policy (per tenant defaults; tenant overrides come with admin API).
    tenant_creates_per_minute: int = 600
    tenant_urgent_per_day: int = 50
    recipient_intents_per_tenant_per_day: int = 3
    otp_per_phone_per_hour: int = 5
    otp_per_ip_per_hour: int = 30
    otp_global_per_minute: int = 600
    otp_failures_per_phone_per_day: int = 10

    def model_post_init(self, __context: object) -> None:
        if self.dev_oidc_enabled is None:
            self.dev_oidc_enabled = self.environment == "local"

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
        if self.dev_oidc_enabled:
            raise RuntimeError(
                "MOCK sign in (RINGSAYS_DEV_OIDC_ENABLED) is allowed only in local environment"
            )
        if DEV_OIDC_ISSUER in (self.admin_oidc_issuer, self.staff_oidc_issuer):
            raise RuntimeError("RINGSAYS_ADMIN_OIDC_ISSUER and RINGSAYS_STAFF_OIDC_ISSUER must be set")
        if self.environment == "production" and self.blob_backend == "local":
            raise RuntimeError("RINGSAYS_BLOB_BACKEND must be object storage in production, not local disk")
        if self.blob_backend in ("gcs", "s3") and not self.blob_bucket:
            raise RuntimeError("RINGSAYS_BLOB_BUCKET must be set for object storage")
        if self.backoffice_enabled and LOCAL_BACKOFFICE_PASSWORD in self.backoffice_database_url:
            raise RuntimeError("RINGSAYS_BACKOFFICE_DATABASE_URL must be set when back office is enabled")
        if self.environment == "production" and self.use_mock_adapters:
            raise RuntimeError("mock adapters are not allowed in production")
        if not self.use_mock_adapters:
            missing = [
                name
                for name, ok in [
                    (
                        "RINGSAYS_SMS_PROVIDER (taqnyat or unifonic)",
                        self.sms_provider in ("taqnyat", "unifonic"),
                    ),
                    ("RINGSAYS_SMS_SENDER_ID", bool(self.sms_sender_id)),
                    ("RINGSAYS_SMS_API_KEY", bool(self.sms_api_key)),
                    ("RINGSAYS_FCM_PROJECT_ID", bool(self.fcm_project_id)),
                    (
                        "RINGSAYS_APNS_KEY_ID, _TEAM_ID, _PRIVATE_KEY",
                        bool(self.apns_key_id and self.apns_team_id and self.apns_private_key),
                    ),
                ]
                if not ok
            ]
            if missing:
                raise RuntimeError(f"real providers not configured: {', '.join(missing)}")
        insecure = [o for o in self.portal_origins if not o.startswith("https://")]
        if insecure:
            raise RuntimeError(f"RINGSAYS_PORTAL_ORIGINS must be https outside local: {insecure}")


def _load() -> Settings:
    """Secrets may come as files (Kubernetes secret volume): RINGSAYS_SETTINGS_DIR holds one file per
    setting, named like the variable (RINGSAYS_DATABASE_URL). Keeps them out of the process environment."""
    secrets_dir = os.environ.get("RINGSAYS_SETTINGS_DIR")
    if secrets_dir:
        return Settings(_secrets_dir=secrets_dir)  # type: ignore[call-arg]
    return Settings()


settings = _load()
