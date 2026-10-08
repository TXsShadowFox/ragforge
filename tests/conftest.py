"""Fixtures shared by all tests."""

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from api.main import create_app
from shared.config import Settings

TEST_JWT_SECRET = "test-jwt-secret-that-is-at-least-32-characters"


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
        jwt_secret=TEST_JWT_SECRET,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP client that calls the app in memory: no network and no startup hooks."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


_installed_exporter: list[InMemorySpanExporter] = []  # set by _span_exporter


@pytest.fixture(scope="session")
def _span_exporter() -> InMemorySpanExporter:
    """Spans kept in memory. OpenTelemetry allows one global tracer provider per process,
    so it is installed once, for the rest of the test run (the app installs none in tests)."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _installed_exporter.append(exporter)
    return exporter


@pytest.fixture(autouse=True)
def _forget_spans() -> Iterator[None]:
    """Once tracing is on, every request makes spans: drop them after each test."""
    yield
    for exporter in _installed_exporter:
        exporter.clear()


@pytest.fixture
def spans(_span_exporter: InMemorySpanExporter) -> InMemorySpanExporter:
    """The finished spans of this test (`spans.get_finished_spans()`)."""
    _span_exporter.clear()
    return _span_exporter
