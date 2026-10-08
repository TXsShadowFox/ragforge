"""Public keys (the chat widget): the chat and ratings only, and only from allowed websites.

Each visitor (IP address) also has a small question limit, next to the key's own limit.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import pytest
from httpx import ASGITransport, AsyncClient

from api.chat.prompts import NO_ANSWER
from api.main import create_app
from shared.config import Settings
from tests.fakes import fake_ai
from tests.integration.helpers import Account, create_key, sign_up, upload_handbook

pytestmark = pytest.mark.integration

SITE = "https://college.example"


async def _public_key(client: AsyncClient, account: Account) -> str:
    created = await create_key(client, account, kind="public", allowed_origins=[SITE])
    key: str = created["key"]
    return key


def _widget(key: str, origin: str | None = SITE) -> dict[str, str]:
    """The headers a browser sends for the widget on `origin` (None: no Origin header)."""
    headers = {"Authorization": f"Bearer {key}"}
    if origin is not None:
        headers["Origin"] = origin
    return headers


async def test_a_public_key_can_chat_from_an_allowed_website(
    chat_api: AsyncClient, chat_settings: Settings
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)
    key = await _public_key(chat_api, owner)

    # Browsers may write the origin a bit differently: it is normalized first.
    response = await chat_api.post(
        "/v1/chat",
        json={"question": "Who may enter zone37?"},
        headers=_widget(key, "https://College.example:443"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["citations"][0]["page"] == 37
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize("origin", ["https://evil.example", "http://college.example", "null", None])
async def test_a_public_key_is_refused_on_other_websites(
    chat_api: AsyncClient, origin: str | None
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    key = await _public_key(chat_api, owner)

    response = await chat_api.post(
        "/v1/chat", json={"question": "Hello?"}, headers=_widget(key, origin)
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"


async def test_a_public_key_can_rate_answers_from_an_allowed_website(
    chat_api: AsyncClient,
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    key = await _public_key(chat_api, owner)
    answer = await chat_api.post("/v1/chat", json={"question": "Hello?"}, headers=_widget(key))
    path = f"/v1/messages/{answer.json()['message_id']}/feedback"

    rated = await chat_api.post(path, json={"rating": "up"}, headers=_widget(key))
    elsewhere = await chat_api.post(
        path, json={"rating": "down"}, headers=_widget(key, "https://evil.example")
    )

    assert answer.json()["answer"] == NO_ANSWER  # no documents yet
    assert rated.status_code == 200
    assert elsewhere.status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "code"),
    [
        ("GET", "/v1/documents", "public_key_not_allowed"),
        ("GET", "/v1/me", "public_key_not_allowed"),
        ("GET", "/v1/analytics/usage", "public_key_not_allowed"),
        ("GET", "/v1/api-keys", "login_required"),
    ],
)
async def test_a_public_key_cannot_use_other_endpoints(
    chat_api: AsyncClient, method: str, path: str, code: str
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    key = await _public_key(chat_api, owner)

    response = await chat_api.request(method, path, headers=_widget(key))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == code


@pytest.fixture
async def visitors(clean_stack: Settings) -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
    """Two browsers with different IP addresses, on one app. The website (the key) may ask
    3 questions a minute, and each visitor 2."""
    settings = clean_stack.model_copy(
        update={"rate_limit_free_questions": 3, "rate_limit_visitor_questions": 2}
    )
    app = create_app(settings, ai=fake_ai())
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.router.lifespan_context(app))
        first, second = [
            await stack.enter_async_context(
                AsyncClient(
                    transport=ASGITransport(app=app, client=(ip, 50000)), base_url="http://t"
                )
            )
            for ip in ["10.0.0.1", "10.0.0.2"]
        ]
        yield first, second


async def test_each_visitor_and_the_whole_website_have_question_limits(
    visitors: tuple[AsyncClient, AsyncClient],
) -> None:
    alice, bob = visitors
    owner = await sign_up(alice, "owner@example.com")
    key = await _public_key(alice, owner)

    async def ask(visitor: AsyncClient) -> int:
        response = await visitor.post("/v1/chat", json={"question": "Hi?"}, headers=_widget(key))
        return response.status_code

    alices = [await ask(alice) for _ in range(3)]  # her third is over her own limit
    bobs = [await ask(bob) for _ in range(2)]  # his second is over the website's limit

    assert alices == [200, 200, 429]
    assert bobs == [200, 429]


async def test_a_visitor_sees_their_own_limit_in_the_headers(
    visitors: tuple[AsyncClient, AsyncClient],
) -> None:
    alice, _ = visitors
    owner = await sign_up(alice, "owner@example.com")
    key = await _public_key(alice, owner)

    response = await alice.post("/v1/chat", json={"question": "Hi?"}, headers=_widget(key))

    assert response.headers["X-RateLimit-Limit"] == "2"
    assert response.headers["X-RateLimit-Remaining"] == "1"
