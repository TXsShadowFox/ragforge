"""Who am I? Shows your tenant and how you are logged in. Handy to test an API key."""

import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from api.auth.principal import PrivateAccess, UserPrincipal
from api.dependencies import SessionDep
from shared.db.models import Tenant, TenantPlan, UserRole

router = APIRouter(prefix="/v1", tags=["auth"])


class MeResponse(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    plan: TenantPlan
    auth_type: Literal["user", "api_key"]
    user_id: uuid.UUID | None = None
    email: str | None = None
    role: UserRole | None = None
    api_key_id: uuid.UUID | None = None


@router.get("/me")
async def me(principal: PrivateAccess, session: SessionDep) -> MeResponse:
    """Works with a login token or a secret API key."""
    tenant = await session.get_one(Tenant, principal.tenant_id)
    if isinstance(principal, UserPrincipal):
        return MeResponse(
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            plan=tenant.plan,
            auth_type="user",
            user_id=principal.user_id,
            email=principal.email,
            role=principal.role,
        )
    return MeResponse(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        plan=tenant.plan,
        auth_type="api_key",
        api_key_id=principal.api_key_id,
    )
