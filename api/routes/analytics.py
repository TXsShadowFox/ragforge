"""Analytics: usage (questions, cache hits, tokens, cost, answer times) and answer quality
(the users' thumbs up and down), per day.

The daily usage comes from `usage_daily`; the answer times (p50 and p95) come from the
messages themselves, because percentiles cannot be added up day by day. Days are UTC.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Date, Select, SQLColumnExpression, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.auth.principal import PrivateAccess
from api.dependencies import SessionDep
from api.errors import ApiError
from api.ratelimit import limit_requests
from shared.db.models import Feedback, FeedbackRating, Message, MessageRole, UsageDaily

router = APIRouter(
    prefix="/v1/analytics", tags=["analytics"], dependencies=[Depends(limit_requests)]
)

DEFAULT_DAYS = 30
MAX_DAYS = 366
NEGATIVE_EXAMPLES = 10

From = Annotated[date | None, Query(alias="from", description="First day (UTC).")]
To = Annotated[date | None, Query(alias="to", description="Last day (UTC).")]


class DayUsage(BaseModel):
    day: date
    questions: int
    cache_hits: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_p50_ms: float | None = Field(description="Half of the day's answers were faster.")
    latency_p95_ms: float | None = Field(description="95% of the day's answers were faster.")


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


class DayRatings(BaseModel):
    day: date
    up: int
    down: int


class NegativeRating(BaseModel):
    message_id: uuid.UUID
    question: str | None
    answer: str
    comment: str | None
    rated_at: datetime


class QualityTotals(BaseModel):
    answers: int = Field(description="Answers given in the range.")
    rated: int = Field(description="Answers with a thumbs up or down.")
    up: int
    down: int
    satisfaction_rate: float | None = Field(
        description="Share of the ratings that are thumbs up. None without ratings."
    )


class QualityReport(BaseModel):
    start: date
    end: date
    days: list[DayRatings] = Field(description="Every day in the range, oldest first.")
    totals: QualityTotals
    recent_negative: list[NegativeRating] = Field(
        description="The latest thumbs-down answers, newest first, with their question."
    )


@router.get("/usage")
async def usage(
    principal: PrivateAccess, session: SessionDep, start: From = None, end: To = None
) -> UsageReport:
    """Your chat usage per day and in total. Default: the last 30 days."""
    start, end = _date_range(start, end)
    rows = await session.scalars(
        select(UsageDaily).where(
            UsageDaily.tenant_id == principal.tenant_id,
            UsageDaily.day >= start,
            UsageDaily.day <= end,
        )
    )
    by_day = {row.day: row for row in rows}
    times_by_day = await _answer_times_by_day(session, principal.tenant_id, start, end)
    days = [
        _day_usage(start + timedelta(days=n), by_day, times_by_day)
        for n in range((end - start).days + 1)
    ]
    p50, p95 = (await session.execute(_answer_times(principal.tenant_id, start, end))).one()
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
            latency_p50_ms=_ms(p50),
            latency_p95_ms=_ms(p95),
        ),
    )


@router.get("/quality")
async def quality(
    principal: PrivateAccess, session: SessionDep, start: From = None, end: To = None
) -> QualityReport:
    """What your users think of the answers: thumbs up and down per day, the share of
    good ratings, and the latest bad answers. Default: the last 30 days.

    (The platform's measured answer quality, from the evaluation, is in eval/RESULTS.md.)
    """
    start, end = _date_range(start, end)
    since, until = _utc_bounds(start, end)
    tenant = principal.tenant_id
    rated_day = _utc_day(Feedback.updated_at)
    in_range = (
        Feedback.tenant_id == tenant,
        Feedback.updated_at >= since,
        Feedback.updated_at < until,
    )
    rows = await session.execute(
        select(
            rated_day,
            func.count().filter(Feedback.rating == FeedbackRating.UP),
            func.count().filter(Feedback.rating == FeedbackRating.DOWN),
        )
        .where(*in_range)
        .group_by(rated_day)
    )
    by_day = {day: (up, down) for day, up, down in rows}
    days = [
        DayRatings(day=day, up=by_day.get(day, (0, 0))[0], down=by_day.get(day, (0, 0))[1])
        for day in (start + timedelta(days=n) for n in range((end - start).days + 1))
    ]
    answers = await session.scalar(
        select(func.count()).where(
            Message.tenant_id == tenant,
            Message.role == MessageRole.ASSISTANT,
            Message.created_at >= since,
            Message.created_at < until,
        )
    )
    up, down = sum(day.up for day in days), sum(day.down for day in days)
    return QualityReport(
        start=start,
        end=end,
        days=days,
        totals=QualityTotals(
            answers=answers or 0,
            rated=up + down,
            up=up,
            down=down,
            satisfaction_rate=round(up / (up + down), 4) if up + down else None,
        ),
        recent_negative=await _recent_negative(session, tenant, since, until),
    )


async def _recent_negative(
    session: AsyncSession, tenant_id: uuid.UUID, since: datetime, until: datetime
) -> list[NegativeRating]:
    """The latest thumbs-down answers with their question: the user's message just before
    the answer in the same conversation (IDs are uuidv7, so they sort by time)."""
    answer, asked = aliased(Message), aliased(Message)
    question = (
        select(asked.content)
        .where(
            asked.session_id == answer.session_id,
            asked.tenant_id == tenant_id,
            asked.role == MessageRole.USER,
            asked.id < answer.id,
        )
        .order_by(asked.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(Feedback.message_id, question, answer.content, Feedback.comment, Feedback.updated_at)
        .join(answer, answer.id == Feedback.message_id)
        .where(
            Feedback.tenant_id == tenant_id,
            answer.tenant_id == tenant_id,
            Feedback.rating == FeedbackRating.DOWN,
            Feedback.updated_at >= since,
            Feedback.updated_at < until,
        )
        .order_by(Feedback.updated_at.desc())
        .limit(NEGATIVE_EXAMPLES)
    )
    return [
        NegativeRating(
            message_id=message_id, question=asked_text, answer=text, comment=comment, rated_at=at
        )
        for message_id, asked_text, text, comment, at in rows
    ]


def _date_range(start: date | None, end: date | None) -> tuple[date, date]:
    """The requested days (default: the last 30), or 400 for a backwards or too long range."""
    end = end or datetime.now(UTC).date()
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end or (end - start).days >= MAX_DAYS:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "bad_date_range",
            f"'from' must not be after 'to', and the range can be at most {MAX_DAYS} days.",
        )
    return start, end


def _utc_day(moment: SQLColumnExpression[datetime]) -> ColumnElement[date]:
    """The UTC day of a timestamp column, in SQL."""
    return func.date(func.timezone("UTC", moment), type_=Date)


def _utc_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """From the start of `start` to the end of `end`, in UTC."""
    return (
        datetime.combine(start, time.min, UTC),
        datetime.combine(end + timedelta(days=1), time.min, UTC),
    )


Percentiles = tuple[float | None, float | None]


def _answer_times(
    tenant_id: uuid.UUID, start: date, end: date
) -> Select[float | None, float | None]:
    """p50 and p95 of the tenant's answer times, from the start of `start` to the end of
    `end` (UTC)."""
    since, until = _utc_bounds(start, end)
    return select(
        func.percentile_cont(0.5).within_group(Message.latency_ms),
        func.percentile_cont(0.95).within_group(Message.latency_ms),
    ).where(
        Message.tenant_id == tenant_id,
        Message.role == MessageRole.ASSISTANT,
        Message.created_at >= since,
        Message.created_at < until,
    )


async def _answer_times_by_day(
    session: AsyncSession, tenant_id: uuid.UUID, start: date, end: date
) -> dict[date, Percentiles]:
    utc_day = _utc_day(Message.created_at)
    rows = await session.execute(
        _answer_times(tenant_id, start, end).add_columns(utc_day).group_by(utc_day)
    )
    return {day: (p50, p95) for p50, p95, day in rows}


def _day_usage(
    day: date, by_day: dict[date, UsageDaily], times_by_day: dict[date, Percentiles]
) -> DayUsage:
    row = by_day.get(day)
    p50, p95 = times_by_day.get(day, (None, None))
    return DayUsage(
        day=day,
        questions=row.questions if row else 0,
        cache_hits=row.cache_hits if row else 0,
        tokens_in=row.tokens_in if row else 0,
        tokens_out=row.tokens_out if row else 0,
        cost_usd=float(row.cost_usd) if row else 0.0,
        latency_p50_ms=_ms(p50),
        latency_p95_ms=_ms(p95),
    )


def _ms(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
