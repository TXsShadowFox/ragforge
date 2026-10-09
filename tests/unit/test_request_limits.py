"""Hardening of every request: a body that is too big gets 413 before it is read, and every
response carries security headers."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient

from api.errors import install_error_handlers
from api.main import create_app
from api.middleware import (
    MULTIPART_OVERHEAD_BYTES,
    SECURITY_HEADERS,
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
)
from shared.config import Settings
from tests.fakes import fake_ai

KB = 1024
MB = 1024 * KB


def _small_app() -> FastAPI:
    """Like the API's setup, with a JSON route and an upload route; limits 1 KB and 1 MB."""
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=1 * KB, max_upload_bytes=1 * MB)
    app.add_middleware(RequestContextMiddleware)
    app.state.route_ran = False

    @app.post("/echo")
    async def echo(body: dict[str, str]) -> dict[str, int]:
        app.state.route_ran = True
        return {"fields": len(body)}

    @app.post("/v1/documents")
    async def upload(file: UploadFile) -> dict[str, int]:
        return {"size": len(await file.read())}

    return app


@pytest.fixture
def small_app() -> FastAPI:
    return _small_app()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_a_small_body_passes(small_app: FastAPI) -> None:
    async with _client(small_app) as client:
        response = await client.post("/echo", json={"question": "When are fees due?"})

    assert response.status_code == 200


async def test_a_body_that_says_it_is_too_big_is_refused_before_it_is_read(
    small_app: FastAPI,
) -> None:
    async with _client(small_app) as client:
        response = await client.post("/echo", json={"text": "x" * 2 * KB})

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert response.json()["error"]["message"] == "The request is bigger than the limit of 1 KB."
    assert response.headers["Connection"] == "close"
    assert response.headers["X-Request-ID"]
    assert small_app.state.route_ran is False


async def test_a_body_sent_in_chunks_is_stopped_once_it_is_too_big(small_app: FastAPI) -> None:
    async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: the size is unknown
        yield b'{"text": "'
        for _ in range(4):
            yield b"x" * 512
        yield b'"}'

    async with _client(small_app) as client:
        response = await client.post(
            "/echo", content=chunks(), headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert small_app.state.route_ran is False


async def test_uploads_have_their_own_bigger_limit(small_app: FastAPI) -> None:
    async with _client(small_app) as client:
        fits = await client.post("/v1/documents", files={"file": ("a.txt", b"x" * 512 * KB)})
        too_big = await client.post(
            "/v1/documents",
            files={"file": ("b.txt", b"x" * (MB + MULTIPART_OVERHEAD_BYTES + 1))},
        )

    assert fits.status_code == 200
    assert fits.json() == {"size": 512 * KB}
    assert too_big.status_code == 413


async def test_the_api_uses_the_limit_from_its_settings(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"max_request_kb": 1}), ai=fake_ai())

    async with _client(app) as client:
        response = await client.post(
            "/v1/auth/login", json={"email": "a@example.com", "password": "x" * 2 * KB}
        )

    assert response.status_code == 413  # before the route, so no database is needed


async def test_every_response_has_the_security_headers(settings: Settings) -> None:
    async with _client(create_app(settings, ai=fake_ai())) as client:
        health = await client.get("/health")
        widget = await client.get("/widget.js")

    for name, value in SECURITY_HEADERS.items():
        assert health.headers[name] == value
    assert widget.headers["Cache-Control"] != "no-store"  # a route's own value stays
    assert widget.headers["X-Content-Type-Options"] == "nosniff"
