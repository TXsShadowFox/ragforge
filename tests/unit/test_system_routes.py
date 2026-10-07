"""Tests for /health, /ready and /metrics. Fake probes, so no real services are needed."""

from fastapi import FastAPI
from httpx import AsyncClient

from api.dependencies import get_ready_checks
from api.readiness import DependencyCheck


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("service is down")


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_returns_200_when_every_service_is_up(
    app: FastAPI, client: AsyncClient
) -> None:
    app.dependency_overrides[get_ready_checks] = lambda: [
        DependencyCheck("postgres", _ok),
        DependencyCheck("redis", _ok),
    ]

    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


async def test_ready_returns_503_and_names_the_broken_service(
    app: FastAPI, client: AsyncClient
) -> None:
    app.dependency_overrides[get_ready_checks] = lambda: [
        DependencyCheck("postgres", _ok),
        DependencyCheck("redis", _fail),
    ]

    response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"postgres": "ok", "redis": "error"},
    }


async def test_metrics_uses_prometheus_text_format(client: AsyncClient) -> None:
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "python_info" in response.text
