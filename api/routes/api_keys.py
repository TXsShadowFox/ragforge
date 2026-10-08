"""API keys: create, list and revoke. Only a logged-in owner or admin can do this."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Self

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import select

from api.auth.keys import generate_api_key, normalize_origin
from api.auth.principal import AdminUser
from api.dependencies import SessionDep
from api.errors import not_found
from api.ratelimit import limit_requests
from shared.db.models import ApiKey, ApiKeyKind

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"], dependencies=[Depends(limit_requests)])

MAX_ORIGINS = 20


class CreateApiKeyRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    kind: ApiKeyKind = ApiKeyKind.SECRET
    allowed_origins: list[str] = Field(
        default_factory=list,
        max_length=MAX_ORIGINS,
        description="Public keys only: websites that may use the key, like https://example.com.",
    )

    @field_validator("allowed_origins")
    @classmethod
    def _normalize_origins(cls, origins: list[str]) -> list[str]:
        return sorted({normalize_origin(origin) for origin in origins})

    @model_validator(mode="after")
    def _origins_only_for_public_keys(self) -> Self:
        if self.kind is ApiKeyKind.PUBLIC and not self.allowed_origins:
            raise ValueError("A public key needs at least one allowed origin.")
        if self.kind is ApiKeyKind.SECRET and self.allowed_origins:
            raise ValueError("Only public keys have allowed origins.")
        return self


class ApiKeyInfo(BaseModel):
    id: uuid.UUID
    name: str
    kind: ApiKeyKind
    prefix: str = Field(description="The first characters of the key, to tell keys apart.")
    allowed_origins: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    @classmethod
    def from_row(cls, api_key: ApiKey, **extra: str) -> Self:
        return cls(
            id=api_key.id,
            name=api_key.name,
            kind=api_key.kind,
            prefix=api_key.key_prefix,
            allowed_origins=api_key.allowed_origins,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
            revoked_at=api_key.revoked_at,
            **extra,
        )


class CreatedApiKey(ApiKeyInfo):
    key: str = Field(description="The full key. It is shown only now: store it safely.")


class ApiKeyList(BaseModel):
    items: list[ApiKeyInfo]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateApiKeyRequest, admin: AdminUser, session: SessionDep
) -> CreatedApiKey:
    """Create a key. The full key is only in this response: we store just its hash."""
    generated = generate_api_key(body.kind)
    api_key = ApiKey(
        tenant_id=admin.tenant_id,
        name=body.name,
        kind=body.kind,
        key_prefix=generated.display_prefix,
        key_hash=generated.key_hash,
        allowed_origins=body.allowed_origins,
    )
    session.add(api_key)
    await session.commit()
    return CreatedApiKey.from_row(api_key, key=generated.key)


@router.get("")
async def list_api_keys(admin: AdminUser, session: SessionDep) -> ApiKeyList:
    """All keys of your tenant, newest first, revoked keys too. Full keys are never shown."""
    rows = await session.scalars(
        select(ApiKey)
        .where(ApiKey.tenant_id == admin.tenant_id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
    )
    return ApiKeyList(items=[ApiKeyInfo.from_row(row) for row in rows])


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: uuid.UUID, admin: AdminUser, session: SessionDep) -> None:
    """Revoke a key: it stops working at once. Revoking it again changes nothing."""
    api_key = await session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == admin.tenant_id)
    )
    if api_key is None:  # also for another tenant's key: we do not say that it exists
        raise not_found("There is no API key with this ID.")
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        await session.commit()
