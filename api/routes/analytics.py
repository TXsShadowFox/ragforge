"""Usage analytics: questions, cache hits, tokens, cost and answer times, per day.

The daily numbers come from `usage_daily`; the answer times (p50 and p95) come from the
messages themselves, because percentiles cannot be added up day by day. Days are UTC.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.auth.principal import PrivateAccess
from api.dependencies import SessionDep
from api.errors import ApiError
from api.ratelimit import limit_requests
from shared.db.models import Message, MessageRole, UsageDaily

router = APIRouter(
    prefix="/v1/analytics", tags=["analytics"], dependencies=[Depends(limit_requests)]
)

DEFAULT_DAYS = 30
MAX_DAYS = 366


class DayUsage(BaseModel):
    day: date
    questions: int
    cache_hits: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


class UsageTotals(BaseModel):
    questions: int
    cache_hits: int
    cache_hit_rate: float = Field(description="Share of questions answered from the cache.")
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_p50_ms: float | None = Field(description="Half of the answers were faster.")
    latency_p95_ms: float | None = Field(description="95% of the answers were faster.")


class UsageReport(BaseModel):
    start: date
    end: date
    days: list[DayUsage] = Field(description="Every day in the range, oldest first.")
    totals: UsageTotals


@router.get("/usage")
async def usage(
    principal: PrivateAccess,
    session: SessionDep,
    start: Annotated[date | None, Query(alias="from", description="First day (UTC).")] = None,
    end: Annotated[date | None, Query(alias="to", description="Last day (UTC).")] = None,
) -> UsageReport:
    """Your chat usage per day and in total. Default: the last 30 days."""
    end = end or datetime.now(UTC).date()
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end or (end - start).days >= MAX_DAYS:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "bad_date_range",
            f"'from' must not be after 'to', and the range can be at most {MAX_DAYS} days.",
        )
    rows = await session.scalars(
        select(UsageDaily).where(
            UsageDaily.tenant_id == principal.tenant_id,
            UsageDaily.day >= start,
            UsageDaily.day <= end,
        )
    )
    by_day = {row.day: row for row in rows}
    days = [_day_usage(start + timedelta(days=n), by_day) for n in range((end - start).days + 1)]
    p50, p95 = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(Message.latency_ms),
                func.percentile_cont(0.95).within_group(Message.latency_ms),
            ).where(
                Message.tenant_id == principal.tenant_id,
                Message.role == MessageRole.ASSISTANT,
                Message.created_at >= datetime.combine(start, time.min, UTC),
                Message.created_at < datetime.combine(end + timedelta(days=1), time.min, UTC),
            )
        )
    ).one()
    questions = sum(day.questions for day in days)
    cache_hits = sum(day.cache_hits for day in days)
    return UsageReport(
        start=start,
        end=end,
        days=days,
        totals=UsageTotals(
            questions=questions,
            cache_hits=cache_hits,
            cache_hit_rate=round(cache_hits / questions, 4) if questions else 0.0,
            tokens_in=sum(day.tokens_in for day in days),
            tokens_out=sum(day.tokens_out for day in days),
            cost_usd=round(sum(day.cost_usd for day in days), 6),
            latency_p50_ms=None if p50 is None else round(p50, 1),
            latency_p95_ms=None if p95 is None else round(p95, 1),
        ),
    )


def _day_usage(day: date, by_day: dict[date, UsageDaily]) -> DayUsage:
    row = by_day.get(day)
    if row is None:
        return DayUsage(day=day, questions=0, cache_hits=0, tokens_in=0, tokens_out=0, cost_usd=0)
    return DayUsage(
        day=day,
        questions=row.questions,
        cache_hits=row.cache_hits,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        cost_usd=float(row.cost_usd),
    )
