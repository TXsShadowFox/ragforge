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

    # --- LLM: any OpenAI-compatible chat API (Groq, Ollama, OpenAI, ...) ---
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-20b"
    llm_api_key: SecretStr | None = None  # not needed for a local Ollama
    llm_timeout_seconds: PositiveFloat = 60.0
    llm_max_output_tokens: PositiveInt = 1024
    llm_temperature: float = Field(default=0.1, ge=0, le=2)
    # How long "reasoning" models (like gpt-oss) think first. "none": do not send it.
    llm_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"

    # --- Search and chat ---
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    search_candidates: int = Field(default=20, ge=1, le=100)  # from each search, then reranked
    # Below this reranker score a chunk is "not relevant". Measured for the default reranker:
    # relevant chunks scored -0.4 to 9.7, unrelated ones about -11 (CLAUDE.md, D33).
    min_rerank_score: float = -5.0
    chat_history_messages: int = Field(default=6, ge=0, le=50)

    # --- Answer cache ---
    cache_ttl_seconds: PositiveInt = 24 * 60 * 60
    answer_cache_collection: str = "answer_cache"
    # Reuse an answer for a question at least this similar (cosine). 0.95 is NOT safe with
    # bge-small: "...on weekends?" vs "...on weekdays?" scored 0.965 (CLAUDE.md, D40).
    semantic_cache_threshold: float = Field(default=0.98, gt=0, le=1)

    # --- Rate limits: per API key or logged-in user, per minute, by the tenant's plan ---
    rate_limit_free_requests: PositiveInt = 60
    rate_limit_free_questions: PositiveInt = 10
    rate_limit_pro_requests: PositiveInt = 600
    rate_limit_pro_questions: PositiveInt = 100
    login_attempts_per_minute: PositiveInt = 5

    # --- Cost: the LLM's price in US dollars per million tokens (Groq gpt-oss-20b) ---
    llm_price_input_per_million: float = Field(default=0.075, ge=0)
    llm_price_output_per_million: float = Field(default=0.30, ge=0)

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
