"""Tests for the readiness logic: failures, timeouts and running probes together."""

import asyncio

from api.readiness import DependencyCheck, build_checks, run_checks
from shared.clients import Clients
from shared.config import Settings


async def _ok() -> None:
    return None


async def _fail() -> None:
    raise ConnectionError("service is down")


async def _hang() -> None:
    await asyncio.sleep(10)


async def test_every_probe_ok() -> None:
    checks = [DependencyCheck("a", _ok), DependencyCheck("b", _ok)]

    assert await run_checks(checks, timeout_seconds=1) == {"a": "ok", "b": "ok"}


async def test_a_failing_probe_does_not_hide_the_others() -> None:
    checks = [DependencyCheck("a", _fail), DependencyCheck("b", _ok)]

    assert await run_checks(checks, timeout_seconds=1) == {"a": "error", "b": "ok"}


async def test_a_slow_probe_times_out() -> None:
    checks = [DependencyCheck("slow", _hang)]

    assert await run_checks(checks, timeout_seconds=0.05) == {"slow": "error"}


async def test_probes_run_at_the_same_time() -> None:
    # "first" waits until "second" has started. If the probes ran one by one,
    # "first" would wait until the timeout and fail.
    second_started = asyncio.Event()

    async def first() -> None:
        await second_started.wait()

    async def second() -> None:
        second_started.set()

    checks = [DependencyCheck("first", first), DependencyCheck("second", second)]

    assert await run_checks(checks, timeout_seconds=1) == {"first": "ok", "second": "ok"}


async def test_build_checks_covers_every_service_the_api_uses(settings: Settings) -> None:
    clients = Clients.create(settings)  # creating clients does not connect
    try:
        names = [check.name for check in build_checks(clients, settings)]
    finally:
        await clients.aclose()

    # Not RabbitMQ: the API writes jobs to the outbox in Postgres; the worker sends them.
    assert names == ["postgres", "redis", "qdrant", "storage"]
