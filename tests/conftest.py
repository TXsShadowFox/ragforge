"""Fixtures shared by all tests."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.main import create_app
from shared.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings with fake addresses. Unit tests never connect to them."""
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        rabbitmq_url="amqp://test:test@localhost:5672/",
        qdrant_url="http://localhost:6333",
        s3_endpoint_url="http://localhost:9000",
        s3_access_key="test",
        s3_secret_key="test-secret",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP client that calls the app in memory: no network and no startup hooks."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
