"""Who is calling? Dependencies that read the header `Authorization: Bearer <...>`.

The header holds either the login token (JWT) of a dashboard user, or an API key.
Only two queries skip the tenant filter, on purpose, because they find the tenant:
the API key lookup here (by hash) and login (by email).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.keys import hash_api_key, is_api_key
from api.auth.tokens import read_access_token
from api.dependencies import SessionDep, SettingsDep
from api.errors import ApiError, forbidden, unauthorized
from shared.db.models import ApiKey, ApiKeyKind, User, UserRole
from shared.logging import bind_log_context

# Save `last_used_at` at most this often, so a busy key does not write on every request.
LAST_USED_RESOLUTION = timedelta(minutes=1)

bearer_scheme = HTTPBearer(
    auto_error=False,  # we send our own 401 in our error format
    description="A login token from /v1/auth/login, or an API key (rf_live_...).",
)


@dataclass(frozen=True, slots=True)
class UserPrincipal:
    """A dashboard user who logged in."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: UserRole


@dataclass(frozen=True, slots=True)
class ApiKeyPrincipal:
    """A program that sent an API key."""

    tenant_id: uuid.UUID
    api_key_id: uuid.UUID
    kind: ApiKeyKind


Principal = UserPrincipal | ApiKeyPrincipal


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
    settings: SettingsDep,
) -> Principal:
    """Who is calling, from the Authorization header. 401 if we cannot tell."""
    if credentials is None:
        raise unauthorized(
            "missing_credentials",
            "Send the header 'Authorization: Bearer <login token or API key>'.",
        )
    token = credentials.credentials
    principal: Principal
    if is_api_key(token):
        principal = await _from_api_key(session, token)
    else:
        principal = await _from_login_token(session, token, settings.jwt_secret)
    bind_log_context(tenant_id=str(principal.tenant_id))
    return principal


async def require_private_access(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """A user or a secret key. Public keys only work for the chat widget (Phase 5)."""
    if isinstance(principal, ApiKeyPrincipal) and principal.kind is ApiKeyKind.PUBLIC:
        raise forbidden(
            "public_key_not_allowed", "Public keys can only be used by the chat widget."
        )
    return principal


async def require_admin(principal: Annotated[Principal, Depends(get_principal)]) -> UserPrincipal:
    """A logged-in owner or admin. API keys cannot manage API keys."""
    if not isinstance(principal, UserPrincipal):
        raise forbidden("login_required", "Log in as an owner or admin to do this.")
    if principal.role not in (UserRole.OWNER, UserRole.ADMIN):
        raise forbidden("admin_required", "Only owners and admins can do this.")
    return principal


PrivateAccess = Annotated[Principal, Depends(require_private_access)]
AdminUser = Annotated[UserPrincipal, Depends(require_admin)]


async def _from_api_key(session: AsyncSession, key: str) -> ApiKeyPrincipal:
    api_key = await session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(key)))
    if api_key is None or api_key.revoked_at is not None:
        raise unauthorized("invalid_api_key", "This API key is wrong or was revoked.")
    await _save_last_used(session, api_key)
    return ApiKeyPrincipal(tenant_id=api_key.tenant_id, api_key_id=api_key.id, kind=api_key.kind)


async def _save_last_used(session: AsyncSession, api_key: ApiKey) -> None:
    """Remember when the key was used, at most once per LAST_USED_RESOLUTION."""
    now = datetime.now(UTC)
    if api_key.last_used_at is not None and now - api_key.last_used_at < LAST_USED_RESOLUTION:
        return
    api_key.last_used_at = now
    await session.commit()


async def _from_login_token(session: AsyncSession, token: str, secret: SecretStr) -> UserPrincipal:
    try:
        user_id = read_access_token(token, secret)
    except jwt.InvalidTokenError as exc:
        raise _bad_login_token() from exc
    user = await session.get(User, user_id)
    if user is None:  # the user was deleted after logging in
        raise _bad_login_token()
    return UserPrincipal(
        tenant_id=user.tenant_id, user_id=user.id, email=user.email, role=user.role
    )


def _bad_login_token() -> ApiError:
    return unauthorized(
        "invalid_token", "The login token is wrong or expired. Please log in again."
    )
