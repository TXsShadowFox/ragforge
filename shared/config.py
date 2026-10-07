"""App settings, read from environment variables and the `.env` file.

Every service address and secret comes from here, so the same code runs on a
laptop, inside Docker and in the cloud. Secrets have no default value: the app
stops at startup with a clear error if one is missing.
"""

from functools import lru_cache
from typing import Literal

from pydantic import AmqpDsn, PositiveFloat, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings. The env var name is the field name in upper case (e.g. `DATABASE_URL`)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # `QDRANT_API_KEY=` (empty) means "not set"
        extra="ignore",  # .env also holds Docker-only values such as POSTGRES_PASSWORD
    )

    # --- App ---
    app_env: Literal["local", "test", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    ready_check_timeout_seconds: PositiveFloat = 2.0

    # --- PostgreSQL: main database ---
    database_url: PostgresDsn

    # --- Redis: cache and rate limits ---
    redis_url: RedisDsn

    # --- RabbitMQ: job queue for ingestion ---
    rabbitmq_url: AmqpDsn

    # --- Qdrant: vector database ---
    qdrant_url: str
    qdrant_api_key: SecretStr | None = None

    # --- Object storage: any S3-compatible service (RustFS locally) ---
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_bucket: str = "ragforge-documents"
    s3_region: str = "us-east-1"


@lru_cache
def get_settings() -> Settings:
    """Load the settings once and reuse them."""
    return Settings()
