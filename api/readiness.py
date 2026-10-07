"""Readiness checks: can the API reach every service it needs?

Each check is a small async "probe" that raises if its service is not usable.
`/ready` runs all probes at the same time, each with a timeout.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Literal

from shared.clients import Clients, postgres, qdrant, redis, storage
from shared.config import Settings

logger = logging.getLogger(__name__)

CheckStatus = Literal["ok", "error"]


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    """A named probe. `probe()` must raise if the service is not usable."""

    name: str
    probe: Callable[[], Awaitable[object]]


def build_checks(clients: Clients, settings: Settings) -> list[DependencyCheck]:
    """The probes for every service the API depends on.

    Not RabbitMQ: the API saves jobs in the outbox (Postgres), and the worker sends them.
    So uploads keep working while RabbitMQ is down (CLAUDE.md, D4).
    """
    return [
        DependencyCheck("postgres", partial(postgres.ping, clients.db)),
        DependencyCheck("redis", partial(redis.ping, clients.redis)),
        DependencyCheck("qdrant", partial(qdrant.ping, clients.qdrant)),
        DependencyCheck("storage", partial(storage.ping, clients.s3)),
    ]


async def run_checks(
    checks: Sequence[DependencyCheck], timeout_seconds: float
) -> dict[str, CheckStatus]:
    """Run all probes at the same time. A probe that raises or is too slow is an error."""
    statuses = await asyncio.gather(*(_run_one(check, timeout_seconds) for check in checks))
    return {check.name: status for check, status in zip(checks, statuses, strict=True)}


async def _run_one(check: DependencyCheck, timeout_seconds: float) -> CheckStatus:
    try:
        async with asyncio.timeout(timeout_seconds):
            await check.probe()
    except Exception as exc:
        # Log the reason for us; the HTTP response only says "error".
        logger.warning("Readiness check %r failed: %r", check.name, exc)
        return "error"
    return "ok"
