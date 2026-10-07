"""Tests for the request ID, the access log line and the JSON error format."""

import logging
import re

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel, Field

from api.errors import unauthorized


async def test_every_response_has_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


async def test_a_safe_request_id_from_the_caller_is_kept(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "trace-123.abc"})

    assert response.headers["x-request-id"] == "trace-123.abc"


async def test_an_unsafe_request_id_from_the_caller_is_replaced(client: AsyncClient) -> None:
    unsafe = "x" * 65  # too long

    response = await client.get("/health", headers={"X-Request-ID": unsafe})

    assert response.headers["x-request-id"] != unsafe


async def test_each_request_writes_one_access_log_line(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="api.middleware")

    await client.get("/no-such-page")

    records = [record for record in caplog.records if record.name == "api.middleware"]
    assert len(records) == 1
    fields = vars(records[0])
    assert (fields["method"], fields["path"], fields["status"]) == ("GET", "/no-such-page", 404)
    assert fields["duration_ms"] >= 0


async def test_health_checks_are_logged_only_at_debug_level(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="api.middleware")

    await client.get("/health")

    assert not [record for record in caplog.records if record.name == "api.middleware"]


async def test_an_unknown_url_gets_a_404_in_the_error_format(client: AsyncClient) -> None:
    response = await client.get("/no-such-page")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "request_id": response.headers["x-request-id"],
        }
    }


async def test_a_wrong_method_gets_a_405_in_the_error_format(client: AsyncClient) -> None:
    response = await client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


class _Body(BaseModel):
    password: str = Field(min_length=8)


async def test_validation_errors_name_the_fields_but_never_echo_the_values(
    app: FastAPI, client: AsyncClient
) -> None:
    @app.post("/test-validation")
    async def _route(body: _Body) -> dict[str, str]:
        return {}

    response = await client.post("/test-validation", json={"password": "secret1"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"] == [
        {
            "loc": ["body", "password"],
            "message": "String should have at least 8 characters",
            "type": "string_too_short",
        }
    ]
    assert "secret1" not in response.text


async def test_api_errors_keep_their_status_code_and_headers(
    app: FastAPI, client: AsyncClient
) -> None:
    @app.get("/test-api-error")
    async def _route() -> None:
        raise unauthorized("invalid_token", "Bad token.")

    response = await client.get("/test-api-error")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "invalid_token",
        "message": "Bad token.",
        "request_id": response.headers["x-request-id"],
    }


async def test_an_unexpected_error_becomes_a_500_without_details(
    app: FastAPI, client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    @app.get("/test-crash")
    async def _route() -> None:
        raise RuntimeError("database password is hunter2")

    response = await client.get("/test-crash")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text  # internal details stay in our logs
    assert response.headers["x-request-id"]
    crash_logs = [record for record in caplog.records if record.message == "Unhandled error"]
    assert len(crash_logs) == 1
    assert crash_logs[0].exc_info is not None
