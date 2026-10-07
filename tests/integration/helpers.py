"""Helpers for the API tests: sign up, log in, auth headers, create keys."""

import uuid
from dataclasses import dataclass
from typing import Any

from httpx import AsyncClient

PASSWORD = "correct-horse-battery-staple"


@dataclass(frozen=True)
class Account:
    """A tenant owner who signed up and logged in."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return bearer(self.token)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def sign_up(client: AsyncClient, email: str, tenant_name: str = "Acme") -> Account:
    response = await client.post(
        "/v1/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    ids = response.json()
    return Account(
        tenant_id=uuid.UUID(ids["tenant_id"]),
        user_id=uuid.UUID(ids["user_id"]),
        token=await log_in(client, email),
    )


async def log_in(client: AsyncClient, email: str, password: str = PASSWORD) -> str:
    response = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


async def create_key(client: AsyncClient, account: Account, **body: Any) -> dict[str, Any]:
    response = await client.post(
        "/v1/api-keys", json={"name": "test key", **body}, headers=account.headers
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created
