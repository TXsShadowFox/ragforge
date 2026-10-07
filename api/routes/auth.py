"""Sign up and log in (dashboard users)."""

import asyncio
import uuid
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, EmailStr, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.auth.passwords import hash_password, needs_rehash, verify_dummy_password, verify_password
from api.auth.tokens import create_access_token
from api.dependencies import SessionDep, SettingsDep
from api.errors import ApiError, unauthorized
from shared.db.models import Tenant, User, UserRole

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class SignupRequest(BaseModel):
    tenant_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ] = Field(description="Your company name.")
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class SignupResponse(BaseModel):
    tenant_id: uuid.UUID
    user_id: uuid.UUID


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int = Field(description="Seconds until the token expires.")


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, session: SessionDep) -> SignupResponse:
    """Create a new tenant (your company) and its owner user."""
    password_hash = await asyncio.to_thread(hash_password, body.password)
    tenant = Tenant(name=body.tenant_name)
    session.add(tenant)
    await session.flush()  # Postgres creates the tenant ID
    user = User(
        tenant_id=tenant.id,
        email=_normalize_email(body.email),
        password_hash=password_hash,
        role=UserRole.OWNER,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:  # users.email is unique
        raise ApiError(
            status.HTTP_409_CONFLICT, "email_taken", "This email is already registered."
        ) from exc
    return SignupResponse(tenant_id=tenant.id, user_id=user.id)


@router.post("/login")
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    """Check the email and password, and return a login token for the dashboard."""
    user = await session.scalar(select(User).where(User.email == _normalize_email(body.email)))
    if user is None:
        await asyncio.to_thread(verify_dummy_password, body.password)
        raise _wrong_login()
    if not await asyncio.to_thread(verify_password, user.password_hash, body.password):
        raise _wrong_login()
    if needs_rehash(user.password_hash):
        user.password_hash = await asyncio.to_thread(hash_password, body.password)
        await session.commit()

    lifetime = timedelta(minutes=settings.jwt_expire_minutes)
    return TokenResponse(
        access_token=create_access_token(user.id, settings.jwt_secret, lifetime),
        token_type="bearer",  # noqa: S106 (the token type, not a password)
        expires_in=int(lifetime.total_seconds()),
    )


def _normalize_email(email: str) -> str:
    """Emails are stored in lower case, so "Ann@X.com" and "ann@x.com" are the same user."""
    return email.strip().lower()


def _wrong_login() -> ApiError:
    # The same answer for a wrong email and a wrong password: attackers cannot tell which.
    return unauthorized("invalid_login", "The email or the password is wrong.")
