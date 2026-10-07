"""/ready against real services running in Docker."""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import RedisDsn

from api.main import create_app
from shared.config import Settings

pytestmark = pytest.mark.integration

ALL_SERVICES = ["postgres", "redis", "qdrant", "storage"]  # the API does not use RabbitMQ


async def _get_ready(settings: Settings) -> tuple[int, dict[str, Any]]:
    """Start the app (with its startup hooks), call /ready, then shut it down."""
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/ready")
    return response.status_code, response.json()


async def test_ready_is_ok_when_every_service_is_up(live_settings: Settings) -> None:
    status_code, body = await _get_ready(live_settings)

    assert status_code == 200, body
    assert body == {"status": "ok", "checks": dict.fromkeys(ALL_SERVICES, "ok")}


async def test_ready_reports_the_service_that_is_down(live_settings: Settings) -> None:
    # Nothing listens on port 1, so Redis looks "down".
    broken = live_settings.model_copy(update={"redis_url": RedisDsn("redis://127.0.0.1:1/0")})

    status_code, body = await _get_ready(broken)

    assert status_code == 503
    assert body["checks"]["redis"] == "error"
    assert body["checks"]["postgres"] == "ok"
