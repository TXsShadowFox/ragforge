"""Rate limits per caller (API key or user), by the tenant's plan: dependencies for routes.

Every request counts against the requests limit; chat questions also against the lower
questions limit. The buckets live in Redis (api/limiter.py). Responses show the caller's
limit in X-RateLimit-Limit and X-RateLimit-Remaining.
"""

from typing import Annotated

from fastapi import Depends, Response

from api.auth.principal import Principal, get_principal, is_public_key
from api.dependencies import SettingsDep
from api.limiter import ClientIpDep, Decision, RateLimiterDep, too_many_requests
from shared import metrics
from shared.config import Settings
from shared.db.models import TenantPlan


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
    visitor: ClientIpDep,
    response: Response,
) -> None:
    """Chat questions have their own, lower limit: each one can cost an LLM call.

    A public key is shared by every visitor of a website, so each visitor (IP address)
    also has a small limit: one visitor cannot use up the whole website's questions.
    Its numbers are the ones in the headers, as they are what the visitor can do.
    """
    show_key_numbers = True
    if is_public_key(principal):
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
