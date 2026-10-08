"""Rate limits: a token bucket per caller in Redis, checked by one atomic Lua script.

A bucket holds up to `limit` tokens and refills at `limit` tokens per period (a minute).
Each request takes one token; with no token left the API answers 429 + Retry-After.
The script reads and writes the bucket in one step and uses Redis's own clock, so
several API copies share the same limits safely.
If Redis is down, requests are allowed (and a warning is logged): availability first.
Responses show the caller's limit in X-RateLimit-Limit and X-RateLimit-Remaining.
"""

import logging
import math
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, Response, status
from redis.asyncio import Redis

from api.auth.principal import Principal, get_principal, is_public_key
from api.dependencies import SettingsDep
from api.errors import ApiError
from shared import metrics
from shared.config import Settings
from shared.db.models import TenantPlan

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


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def requests_per_minute(plan: TenantPlan, settings: Settings) -> int:
    if plan is TenantPlan.PRO:
        return settings.rate_limit_pro_requests
    return settings.rate_limit_free_requests


def questions_per_minute(plan: TenantPlan, settings: Settings) -> int:
    if plan is TenantPlan.PRO:
        return settings.rate_limit_pro_questions
    return settings.rate_limit_free_questions


async def limit_requests(
    principal: Annotated[Principal, Depends(get_principal)],
    limiter: RateLimiterDep,
    settings: SettingsDep,
    response: Response,
) -> None:
    """Every API request of a caller (API key or user) counts against its plan's limit."""
    limit = requests_per_minute(principal.plan, settings)
    decision = await limiter.hit(f"requests:{principal.rate_key}", limit)
    _check(decision, response, limit="requests")


async def limit_questions(
    principal: Annotated[Principal, Depends(get_principal)],
    limiter: RateLimiterDep,
    settings: SettingsDep,
    request: Request,
    response: Response,
) -> None:
    """Chat questions have their own, lower limit: each one can cost an LLM call.

    A public key is shared by every visitor of a website, so each visitor (IP address)
    also has a small limit: one visitor cannot use up the whole website's questions.
    Its numbers are the ones in the headers, as they are what the visitor can do.
    """
    show_key_numbers = True
    if is_public_key(principal):
        visitor = request.client.host if request.client else "unknown"
        bucket = f"questions:{principal.rate_key}:visitor:{visitor}"
        decision = await limiter.hit(bucket, settings.rate_limit_visitor_questions)
        _check(decision, response, limit="visitor")
        show_key_numbers = False
    limit = questions_per_minute(principal.plan, settings)
    decision = await limiter.hit(f"questions:{principal.rate_key}", limit)
    _check(decision, response, limit="questions", set_headers=show_key_numbers)


def _check(
    decision: Decision | None, response: Response, *, limit: str, set_headers: bool = True
) -> None:
    if decision is None:  # Redis is down: allow, and send no numbers that we do not know
        return
    if not decision.allowed:
        metrics.RATE_LIMITED.labels(limit=limit).inc()
        raise too_many_requests(decision)
    if set_headers:
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
