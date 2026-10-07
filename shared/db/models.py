"""Database tables (SQLAlchemy 2.0 models), shared by the API and the worker.

Rules:
- Every table except `tenants` has `tenant_id`, and every query filters by it.
- IDs come from Postgres 18's `uuidv7()`: unique and ordered by creation time.
  (Chunk IDs are the exception: they are computed, so a repeated job writes the same rows.)
- After changing a model, create a migration (see CLAUDE.md, "Database migrations").
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Predictable names for indexes and constraints, so migrations can refer to them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Always store times with their time zone.
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


# Column types used by many tables.
UuidPk = Annotated[uuid.UUID, mapped_column(primary_key=True, server_default=text("uuidv7()"))]
TenantId = Annotated[
    uuid.UUID, mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
]
CreatedAt = Annotated[datetime, mapped_column(server_default=func.now())]
UpdatedAt = Annotated[datetime, mapped_column(server_default=func.now(), onupdate=func.now())]


class TenantPlan(StrEnum):
    FREE = "free"
    PRO = "pro"


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class ApiKeyKind(StrEnum):
    SECRET = "secret"  # noqa: S105 (a kind name, not a password). For servers: never in a web page.
    PUBLIC = "public"  # for the chat widget: works only from its allowed origins


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"  # stored, waiting for the worker
    PROCESSING = "processing"
    READY = "ready"  # searchable
    FAILED = "failed"  # see `error`
    DELETING = "deleting"  # the worker is removing it from all stores


class JobType(StrEnum):
    INGEST = "ingest"  # read, chunk and embed a document
    DELETE = "delete"  # remove a document from Postgres, Qdrant and storage


def _text_enum(enum_class: type[StrEnum], name: str) -> Enum:
    """Store a StrEnum as text with a CHECK constraint.

    Easier to change later than a Postgres ENUM type: a new value is a simple migration.
    """
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=16,
        values_callable=lambda members: [member.value for member in members],
    )


class Tenant(Base):
    """A customer company. All other data belongs to exactly one tenant."""

    __tablename__ = "tenants"

    id: Mapped[UuidPk]
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[TenantPlan] = mapped_column(
        _text_enum(TenantPlan, "tenant_plan"), server_default=TenantPlan.FREE.value
    )
    created_at: Mapped[CreatedAt]


class User(Base):
    """A person who logs in to the dashboard."""

    __tablename__ = "users"

    id: Mapped[UuidPk]
    tenant_id: Mapped[TenantId]
    # Lower case, and unique across all tenants: login needs only the email and the password.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))  # argon2id
    role: Mapped[UserRole] = mapped_column(_text_enum(UserRole, "user_role"))
    created_at: Mapped[CreatedAt]


class ApiKey(Base):
    """A key that programs send to call the API. We store only its SHA-256 hash."""

    __tablename__ = "api_keys"

    id: Mapped[UuidPk]
    tenant_id: Mapped[TenantId]
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[ApiKeyKind] = mapped_column(_text_enum(ApiKeyKind, "api_key_kind"))
    key_prefix: Mapped[str] = mapped_column(String(16))  # first characters, to tell keys apart
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256, hex
    # Websites that may use a public key, like "https://example.com". Empty for secret keys.
    allowed_origins: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    created_at: Mapped[CreatedAt]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class Document(Base):
    """An uploaded file. The file itself is in object storage under `storage_key`."""

    __tablename__ = "documents"
    # The same file uploaded twice by one tenant is one document.
    __table_args__ = (UniqueConstraint("tenant_id", "sha256"),)
    # Read back values that Postgres sets (like `updated_at`) right after each write.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 (SQLAlchemy reads it once)

    id: Mapped[UuidPk]
    tenant_id: Mapped[TenantId]
    filename: Mapped[str] = mapped_column(String(255))  # as uploaded; only for showing
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(300))
    status: Mapped[DocumentStatus] = mapped_column(
        _text_enum(DocumentStatus, "document_status"),
        server_default=DocumentStatus.UPLOADED.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(server_default=text("0"))
    page_count: Mapped[int | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class Chunk(Base):
    """A piece of a document's text, with its place in the document.

    Its vector is in Qdrant under the same ID. `tsv` is for keyword search (Postgres
    full-text search): Postgres fills it in from `text`.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # computed: see worker/chunking.py
    tenant_id: Mapped[TenantId]
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int]  # 0, 1, 2, ... in reading order
    text: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int | None]  # the PDF page where the chunk starts; None for other files
    token_count: Mapped[int]
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )


class OutboxMessage(Base):
    """A job waiting to be sent to RabbitMQ (the "outbox pattern").

    The API saves it in the same transaction as the change it belongs to. So a job is
    never lost when RabbitMQ is down, and never sent for a change that was rolled back.
    The worker sends waiting messages to RabbitMQ, then deletes them.
    """

    __tablename__ = "outbox"

    id: Mapped[UuidPk]
    tenant_id: Mapped[TenantId]
    job_type: Mapped[JobType] = mapped_column(_text_enum(JobType, "job_type"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]
