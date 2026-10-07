"""Sign up, log in and login tokens, on a real Postgres."""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from api.auth.tokens import create_access_token
from shared.config import Settings
from tests.integration.helpers import PASSWORD, bearer, log_in, sign_up

pytestmark = pytest.mark.integration


async def test_signup_creates_a_tenant_and_its_owner(api: AsyncClient) -> None:
    account = await sign_up(api, "Owner@Example.com", tenant_name="Acme College")

    response = await api.get("/v1/me", headers=account.headers)

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": str(account.tenant_id),
        "tenant_name": "Acme College",
        "plan": "free",
        "auth_type": "user",
        "user_id": str(account.user_id),
        "email": "owner@example.com",  # stored in lower case
        "role": "owner",
        "api_key_id": None,
    }


async def test_an_email_can_sign_up_only_once(api: AsyncClient) -> None:
    await sign_up(api, "owner@example.com")

    response = await api.post(
        "/v1/auth/signup",
        json={"tenant_name": "Other", "email": "OWNER@example.com", "password": PASSWORD},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


async def test_a_failed_signup_leaves_no_empty_tenant(
    api: AsyncClient, db_engine: AsyncEngine
) -> None:
    await sign_up(api, "owner@example.com")
    await api.post(
        "/v1/auth/signup",
        json={"tenant_name": "Other", "email": "owner@example.com", "password": PASSWORD},
    )

    async with db_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM tenants")) == 1


async def test_signup_needs_a_password_of_at_least_8_characters(api: AsyncClient) -> None:
    response = await api.post(
        "/v1/auth/signup",
        json={"tenant_name": "Acme", "email": "a@example.com", "password": "short"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["loc"] == ["body", "password"]


async def test_passwords_are_stored_as_argon2_hashes(
    api: AsyncClient, db_engine: AsyncEngine
) -> None:
    await sign_up(api, "owner@example.com")

    async with db_engine.connect() as connection:
        stored = await connection.scalar(text("SELECT password_hash FROM users"))

    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


async def test_login_ignores_the_case_of_the_email(api: AsyncClient) -> None:
    await sign_up(api, "owner@example.com")

    assert await log_in(api, "OWNER@Example.com")


async def test_a_wrong_password_and_an_unknown_email_get_the_same_401(api: AsyncClient) -> None:
    await sign_up(api, "owner@example.com")

    wrong_password = await api.post(
        "/v1/auth/login", json={"email": "owner@example.com", "password": "wrong-password"}
    )
    unknown_email = await api.post(
        "/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    wrong_password_error = wrong_password.json()["error"]
    unknown_email_error = unknown_email.json()["error"]
    assert wrong_password_error["code"] == unknown_email_error["code"] == "invalid_login"
    assert wrong_password_error["message"] == unknown_email_error["message"]


async def test_a_request_without_a_token_gets_401(api: AsyncClient) -> None:
    response = await api.get("/v1/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "missing_credentials"


async def test_a_wrong_token_gets_401(api: AsyncClient) -> None:
    response = await api.get("/v1/me", headers=bearer("not-a-real-token"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_an_expired_token_gets_401(api: AsyncClient, db_settings: Settings) -> None:
    account = await sign_up(api, "owner@example.com")
    expired = create_access_token(account.user_id, db_settings.jwt_secret, timedelta(seconds=-1))

    response = await api.get("/v1/me", headers=bearer(expired))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_the_token_of_a_deleted_user_stops_working(
    api: AsyncClient, db_engine: AsyncEngine
) -> None:
    account = await sign_up(api, "owner@example.com")
    async with db_engine.begin() as connection:
        await connection.execute(text("DELETE FROM users"))

    response = await api.get("/v1/me", headers=account.headers)

    assert response.status_code == 401
