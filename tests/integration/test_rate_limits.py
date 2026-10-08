"""Rate limits with a real Redis. The tests use small limits: 5 requests and 2 questions a
minute (pro: 10 and 4), and 3 logins a minute per email."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from api.ratelimit import Decision, RateLimiter
from shared.clients import Clients
from shared.config import Settings
from shared.db.models import Tenant, TenantPlan
from tests.integration.helpers import (
    PASSWORD,
    bearer,
    chat_client,
    create_key,
    sign_up,
    with_redis_down,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def limited_settings(clean_stack: Settings) -> Settings:
    return clean_stack.model_copy(
        update={
            "rate_limit_free_requests": 5,
            "rate_limit_free_questions": 2,
            "rate_limit_pro_requests": 10,
            "rate_limit_pro_questions": 4,
            "login_attempts_per_minute": 3,
        }
    )


@pytest.fixture
async def limited_api(limited_settings: Settings) -> AsyncIterator[AsyncClient]:
    async with chat_client(limited_settings) as client:
        yield client


async def test_too_many_questions_get_429_with_retry_after(limited_api: AsyncClient) -> None:
    owner = await sign_up(limited_api, "owner@example.com")

    asked = [
        await limited_api.post("/v1/chat", json={"question": "Hello?"}, headers=owner.headers)
        for _ in range(3)
    ]
    other_request = await limited_api.get("/v1/documents", headers=owner.headers)

    assert [response.status_code for response in asked] == [200, 200, 429]
    assert asked[0].headers["X-RateLimit-Limit"] == "2"  # chat shows the questions limit
    assert [response.headers["X-RateLimit-Remaining"] for response in asked[:2]] == ["1", "0"]
    refused = asked[2]
    assert refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 30  # 2 a minute: one every 30 s
    assert other_request.status_code == 200  # other requests have their own, higher limit


async def test_too_many_requests_get_429(limited_api: AsyncClient) -> None:
    owner = await sign_up(limited_api, "owner@example.com")

    responses = [await limited_api.get("/v1/documents", headers=owner.headers) for _ in range(6)]

    assert [response.status_code for response in responses] == [200] * 5 + [429]
    remaining = [response.headers["X-RateLimit-Remaining"] for response in responses[:5]]
    assert remaining == ["4", "3", "2", "1", "0"]
    assert 1 <= int(responses[-1].headers["Retry-After"]) <= 12  # 5 a minute: one every 12 s


async def test_each_user_and_api_key_has_its_own_limit(limited_api: AsyncClient) -> None:
    owner = await sign_up(limited_api, "owner@example.com")
    key = await create_key(limited_api, owner)  # 1 of the owner's 5 requests
    for _ in range(4):
        await limited_api.get("/v1/documents", headers=owner.headers)

    owner_again = await limited_api.get("/v1/documents", headers=owner.headers)
    with_key = await limited_api.get("/v1/documents", headers=bearer(key["key"]))
    other = await sign_up(limited_api, "other@example.com", tenant_name="Other")
    other_user = await limited_api.get("/v1/documents", headers=other.headers)

    assert owner_again.status_code == 429
    assert with_key.status_code == 200
    assert other_user.status_code == 200


async def test_too_many_logins_for_one_email_get_429(limited_api: AsyncClient) -> None:
    await sign_up(limited_api, "owner@example.com")  # logs in: 1 of 3 tries

    wrong = [
        await limited_api.post(
            "/v1/auth/login", json={"email": "owner@example.com", "password": "not the password"}
        )
        for _ in range(2)
    ]
    right = await limited_api.post(
        "/v1/auth/login", json={"email": "Owner@Example.com", "password": PASSWORD}
    )

    assert [response.status_code for response in wrong] == [401, 401]
    assert right.status_code == 429  # even the right password waits, so guessing stops
    assert int(right.headers["Retry-After"]) >= 1
    await sign_up(limited_api, "other@example.com", tenant_name="Other")  # its own limit


async def test_pro_tenants_get_higher_limits(
    limited_api: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(limited_api, "owner@example.com")
    async with db_engine.begin() as connection:
        await connection.execute(
            update(Tenant).where(Tenant.id == owner.tenant_id).values(plan=TenantPlan.PRO)
        )

    responses = [await limited_api.get("/v1/documents", headers=owner.headers) for _ in range(11)]

    assert [response.status_code for response in responses] == [200] * 10 + [429]
    assert responses[0].headers["X-RateLimit-Limit"] == "10"


async def test_a_used_up_limit_fills_up_again_over_time(stack_clients: Clients) -> None:
    limiter = RateLimiter(stack_clients.redis)

    async def hit() -> Decision:
        decision = await limiter.hit("refill-test", limit=2, period_seconds=1)
        assert decision is not None  # Redis is up
        return decision

    first, second, third = [await hit() for _ in range(3)]
    await asyncio.sleep(0.6)  # one token comes back every 0.5 s
    later = await hit()

    assert [first.allowed, second.allowed, third.allowed] == [True, True, False]
    assert third.retry_after_seconds == 1
    assert later.allowed


async def test_requests_are_allowed_when_redis_is_down(limited_settings: Settings) -> None:
    async with chat_client(with_redis_down(limited_settings)) as client:
        owner = await sign_up(client, "owner@example.com")
        responses = [await client.get("/v1/documents", headers=owner.headers) for _ in range(6)]

    assert [response.status_code for response in responses] == [200] * 6  # the limit is 5
    assert "X-RateLimit-Limit" not in responses[0].headers  # unknown without Redis
