"""App settings, read from environment variables and the `.env` file.

Every service address and secret comes from here, so the same code runs on a
laptop, inside Docker and in the cloud. Secrets have no default value: the app
stops at startup with a clear error if one is missing.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    AmqpDsn,
    Field,
    PositiveFloat,
    PositiveInt,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    model_validator,
)
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
    qdrant_collection: str = "chunks"

    # --- Object storage: any S3-compatible service (RustFS locally) ---
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: SecretStr
    s3_bucket: str = "ragforge-documents"
    s3_region: str = "us-east-1"

    # --- Auth ---
    # Signs the login tokens (JWT). At least 32 characters, as HS256 needs a 32-byte key.
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_expire_minutes: int = Field(default=60, ge=1, le=24 * 60)

    # --- Documents and ingestion ---
    max_upload_mb: PositiveInt = 25
    chunk_size_tokens: int = Field(default=500, ge=50)
    chunk_overlap_tokens: int = Field(default=50, ge=0)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Measured on a 50-page PDF (CLAUDE.md, D26): 8 is faster than 32 and needs 37% less RAM.
    embedding_batch_size: PositiveInt = 8
    model_cache_dir: Path = Path(".cache/models")
    # Wait this many seconds before each retry of a failed job. After the last one: dead.
    ingest_retry_delays_seconds: list[PositiveInt] = Field(default_factory=lambda: [10, 60, 300])
    outbox_poll_seconds: PositiveFloat = 1.0

    @model_validator(mode="after")
    def _overlap_is_smaller_than_a_chunk(self) -> Self:
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Load the settings once and reuse them."""
    return Settings()
