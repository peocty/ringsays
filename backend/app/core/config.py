"""Runtime settings, read from environment. Defaults are for local development with mocks only."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RINGSAYS_", env_file=".env", extra="ignore")

    environment: str = "local"
    residency_region: str = "KSA"
    database_url: str = "postgresql+psycopg://ringsays:ringsays@localhost:5432/ringsays"
    redis_url: str = "redis://localhost:6379/0"
    nats_url: str = "nats://localhost:4222"
    use_mock_adapters: bool = True


settings = Settings()
