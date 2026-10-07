"""API keys on a real Postgres: create, use, revoke, roles, public keys and tenant isolation."""

import hashlib
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from api.auth.passwords import hash_password
from tests.integration.helpers import PASSWORD, bearer, create_key, log_in, sign_up

pytestmark = pytest.mark.integration


async def test_an_owner_creates_a_secret_key_and_a_program_uses_it(api: AsyncClient) -> None:
    owner = await sign_up(api, "owner@example.com")

    created = await create_key(api, owner, name="backend")
    response = await api.get("/v1/me", headers=bearer(created["key"]))

    assert created["key"].startswith("rf_live_")
    assert created["kind"] == "secret"
    assert created["prefix"] == created["key"][:12]
    assert response.status_code == 200
    assert response.json()["auth_type"] == "api_key"
    assert response.json()["api_key_id"] == created["id"]
    assert response.json()["tenant_id"] == str(owner.tenant_id)


async def test_the_full_key_is_shown_once_and_only_its_hash_is_stored(
    api: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(api, "owner@example.com")
    created = await create_key(api, owner)

    listed = await api.get("/v1/api-keys", headers=owner.headers)
    async with db_engine.connect() as connection:
        row = (await connection.execute(text("SELECT * FROM api_keys"))).mappings().one()

    assert listed.status_code == 200
    assert "key" not in listed.json()["items"][0]
    assert created["key"] not in listed.text
    assert row["key_hash"] == hashlib.sha256(created["key"].encode()).hexdigest()
    assert created["key"] not in [str(value) for value in row.values()]


async def test_using_a_key_saves_when_it_was_last_used(api: AsyncClient) -> None:
    owner = await sign_up(api, "owner@example.com")
    created = await create_key(api, owner)
    assert created["last_used_at"] is None

    await api.get("/v1/me", headers=bearer(created["key"]))

    listed = await api.get("/v1/api-keys", headers=owner.headers)
    assert listed.json()["items"][0]["last_used_at"] is not None


async def test_a_wrong_key_gets_401(api: AsyncClient) -> None:
    response = await api.get("/v1/me", headers=bearer("rf_live_" + "x" * 40))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


async def test_a_revoked_key_stops_working_at_once(api: AsyncClient) -> None:
    owner = await sign_up(api, "owner@example.com")
    created = await create_key(api, owner)
    assert (await api.get("/v1/me", headers=bearer(created["key"]))).status_code == 200

    revoke = await api.delete(f"/v1/api-keys/{created['id']}", headers=owner.headers)
    after = await api.get("/v1/me", headers=bearer(created["key"]))
    revoke_again = await api.delete(f"/v1/api-keys/{created['id']}", headers=owner.headers)
    listed = await api.get("/v1/api-keys", headers=owner.headers)

    assert revoke.status_code == 204
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "invalid_api_key"
    assert revoke_again.status_code == 204  # revoking twice changes nothing
    assert listed.json()["items"][0]["revoked_at"] is not None


async def test_revoking_an_unknown_key_gets_404(api: AsyncClient) -> None:
    owner = await sign_up(api, "owner@example.com")

    response = await api.delete(f"/v1/api-keys/{uuid.uuid4()}", headers=owner.headers)

    assert response.status_code == 404


async def test_an_api_key_cannot_manage_api_keys(api: AsyncClient) -> None:
    owner = await sign_up(api, "owner@example.com")
    created = await create_key(api, owner)

    response = await api.post(
        "/v1/api-keys", json={"name": "sneaky"}, headers=bearer(created["key"])
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "login_required"


async def test_a_member_cannot_manage_api_keys(api: AsyncClient, db_engine: AsyncEngine) -> None:
    owner = await sign_up(api, "owner@example.com")
    async with db_engine.begin() as connection:  # no endpoint adds members yet
        await connection.execute(
            text(
                "INSERT INTO users (tenant_id, email, password_hash, role)"
                " VALUES (:tenant_id, :email, :password_hash, 'member')"
            ),
            {
                "tenant_id": owner.tenant_id,
                "email": "member@example.com",
                "password_hash": hash_password(PASSWORD),
            },
        )
    member_token = await log_in(api, "member@example.com")

    response = await api.get("/v1/api-keys", headers=bearer(member_token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_required"


async def test_a_public_key_needs_allowed_origins_and_a_secret_key_has_none(
    api: AsyncClient,
) -> None:
    owner = await sign_up(api, "owner@example.com")

    public_without_origins = await api.post(
        "/v1/api-keys", json={"name": "widget", "kind": "public"}, headers=owner.headers
    )
    secret_with_origins = await api.post(
        "/v1/api-keys",
        json={"name": "server", "allowed_origins": ["https://example.com"]},
        headers=owner.headers,
    )

    assert public_without_origins.status_code == 422
    assert secret_with_origins.status_code == 422


async def test_a_public_key_works_only_for_the_widget(api: AsyncClient) -> None:
    owner = await sign_up(api, "owner@example.com")
    created = await create_key(
        api,
        owner,
        name="widget",
        kind="public",
        allowed_origins=["https://Example.com/", "http://localhost:3000"],
    )

    response = await api.get("/v1/me", headers=bearer(created["key"]))

    assert created["key"].startswith("rf_pub_")
    assert created["allowed_origins"] == ["http://localhost:3000", "https://example.com"]
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "public_key_not_allowed"


async def test_tenants_cannot_see_or_revoke_each_others_keys(api: AsyncClient) -> None:
    alice = await sign_up(api, "alice@example.com", tenant_name="Alice Inc")
    bob = await sign_up(api, "bob@example.com", tenant_name="Bob Ltd")
    alice_key = await create_key(api, alice)

    bob_list = await api.get("/v1/api-keys", headers=bob.headers)
    bob_revoke = await api.delete(f"/v1/api-keys/{alice_key['id']}", headers=bob.headers)
    alice_key_check = await api.get("/v1/me", headers=bearer(alice_key["key"]))

    assert bob_list.json() == {"items": []}
    assert bob_revoke.status_code == 404  # the same answer as for a key that does not exist
    assert alice_key_check.status_code == 200  # still works
    assert alice_key_check.json()["tenant_id"] == str(alice.tenant_id)
