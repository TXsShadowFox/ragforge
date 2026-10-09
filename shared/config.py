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
    # Connections per process: kept open, extra ones under load, and how long a request
    # waits for one before it fails.
    db_pool_size: PositiveInt = 5
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout_seconds: PositiveFloat = 30.0

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

    # --- Requests and documents ---
    max_upload_mb: PositiveInt = 25
    # Any other request body (JSON) may be at most this big; bigger ones get 413 at once.
    max_request_kb: PositiveInt = 1024
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
    search_candidates: int = Field(default=20, ge=1, le=100)  # from each search, then fused
    # The reranker scores only the best of the fused candidates: it is the slowest step
    # (loadtests/RESULTS.md), and the evaluation found no loss with 10 (eval/RESULTS.md).
    rerank_candidates: int = Field(default=10, ge=1, le=100)
    # Reranker runs at the same time in one API process; more questions wait their turn.
    # Each run needs ~0.2 GB for 10 long chunks: without a limit, 10 users at once ran
    # the API out of memory.
    rerank_concurrency: PositiveInt = 2
    # Below this reranker score a chunk is "not relevant". Measured for the default reranker
    # (make eval): off-topic questions scored -11.0 to -11.1, answerable ones -9.7 to 6.7
    # (CLAUDE.md, D33). On-topic questions without an answer are left to the LLM.
    min_rerank_score: float = -10.0
    # A source must also score at most this much below the best one (the reranker's
    # scale): weaker chunks only cost tokens. Measured (make eval): with 5, an answer gets
    # 1.7 sources instead of 3.4, and no question lost the chunk with its answer.
    source_score_margin: PositiveFloat = 5.0
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
    # Per IP address: logins (any email), sign-ups, and requests with a wrong API key or
    # login token (after that, 429 instead of 401).
    login_attempts_per_ip_per_minute: PositiveInt = 30
    signups_per_ip_per_minute: PositiveInt = 5
    auth_failures_per_ip_per_minute: PositiveInt = 30
    # Questions per minute from one widget visitor (IP address) with a public key. The
    # key's own limit (the whole website's) applies too.
    rate_limit_visitor_questions: PositiveInt = 5

    # --- Chat widget: its JavaScript is served at GET /widget.js (path from the working dir) ---
    widget_file: Path = Path("widget/widget.js")

    # --- Monitoring ---
    # Where traces go (OTLP over HTTP, like http://jaeger:4318/v1/traces). Empty: no tracing.
    otlp_traces_endpoint: str | None = None
    # Share of requests that get a trace: 1.0 = all (fine here), lower for heavy traffic.
    trace_sample_ratio: float = Field(default=1.0, ge=0, le=1)
    # The worker serves its Prometheus metrics on this port (the API serves them at /metrics).
    worker_metrics_port: int = Field(default=8001, ge=1, le=65535)

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
