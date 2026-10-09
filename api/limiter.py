"""The rate limiter: a token bucket per name in Redis, checked by one atomic Lua script.

A bucket holds up to `limit` tokens and refills at `limit` tokens per period (a minute).
Each request takes one token; with no token left the API answers 429 + Retry-After.
The script reads and writes the bucket in one step and uses Redis's own clock, so
several API copies share the same limits safely.
If Redis is down, requests are allowed (and a warning is logged): availability first.

api/ratelimit.py uses it for each caller's limits; logins, sign-ups and wrong keys are
limited per IP address (`enforce` with `client_ip`).
"""

import logging
import math
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, status
from redis.asyncio import Redis

from api.errors import ApiError
from shared import metrics

logger = logging.getLogger(__name__)

# KEYS[1]: the bucket. ARGV[1]: its size (the limit). ARGV[2]: tokens added per millisecond.
# Returns {1 if allowed else 0, whole tokens left, milliseconds until one token is back}.
BUCKET_SCRIPT = """
local capacity = tonumber(ARGV[1])
local refill_per_ms = tonumber(ARGV[2])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local state = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local tokens = tonumber(state[1]) or capacity
local updated = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - updated) * refill_per_ms)
local allowed = 0
local wait_ms = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  wait_ms = math.ceil((1 - tokens) / refill_per_ms)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'updated', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / refill_per_ms) + 1000)
return {allowed, math.floor(tokens), wait_ms}
"""


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int  # 0 when allowed


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._script = redis.register_script(BUCKET_SCRIPT)

    async def hit(self, bucket: str, limit: int, period_seconds: float = 60.0) -> Decision | None:
        """Take one token from `bucket`, which holds `limit` tokens per `period_seconds`.

        None if Redis could not be asked: the caller then allows the request.
        """
        refill_per_ms = limit / (period_seconds * 1000)
        try:
            allowed, remaining, wait_ms = await self._script(
                keys=[f"rate:{bucket}"], args=[limit, refill_per_ms]
            )
        except Exception:
            logger.warning("The rate limit check failed; allowing the request", exc_info=True)
            return None
        if allowed:
            return Decision(True, limit, int(remaining), 0)
        return Decision(False, limit, 0, max(1, math.ceil(int(wait_ms) / 1000)))


def too_many_requests(decision: Decision) -> ApiError:
    seconds = decision.retry_after_seconds
    return ApiError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "rate_limited",
        f"Too many requests. Please try again in {seconds} seconds.",
        headers={"Retry-After": str(seconds)},
    )


async def enforce(limiter: RateLimiter, bucket: str, limit: int, *, name: str) -> None:
    """Take one token from `bucket`; 429 if it is empty. `name` labels the 429s in the
    metrics (ragforge_rate_limited_total{limit=name})."""
    decision = await limiter.hit(bucket, limit)
    if decision is not None and not decision.allowed:
        metrics.RATE_LIMITED.labels(limit=name).inc()
        raise too_many_requests(decision)


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def client_ip(request: Request) -> str:
    """The caller's IP address. Behind a proxy, uvicorn must trust its X-Forwarded-For
    (--proxy-headers), or every caller has the proxy's address."""
    return request.client.host if request.client else "unknown"


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
ClientIpDep = Annotated[str, Depends(client_ip)]
