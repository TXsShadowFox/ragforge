"""Usage analytics: numbers from real chat turns, then exact sums and percentiles from
rows written by the test."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from api.chat.usage import answer_cost
from shared.config import Settings
from shared.db.models import ChatSession, Message, MessageRole, UsageDaily
from shared.llm import Usage
from tests.integration.helpers import Account, ask, chat_client, sign_up, upload_handbook

pytestmark = pytest.mark.integration


async def _report(client: AsyncClient, account: Account, **params: str) -> dict[str, Any]:
    response = await client.get("/v1/analytics/usage", params=params, headers=account.headers)
    assert response.status_code == 200, response.text
    report: dict[str, Any] = response.json()
    return report


def _usage_row(account: Account, day: date, questions: int, cache_hits: int) -> UsageDaily:
    """A day with 250 tokens in and 50 out per question, at $0.00003 per question."""
    return UsageDaily(
        tenant_id=account.tenant_id,
        day=day,
        questions=questions,
        cache_hits=cache_hits,
        tokens_in=250 * questions,
        tokens_out=50 * questions,
        cost_usd=Decimal("0.00003") * questions,
    )


def _answer(account: Account, chat_id: uuid.UUID, latency_ms: int, at: datetime) -> Message:
    return Message(
        tenant_id=account.tenant_id,
        session_id=chat_id,
        role=MessageRole.ASSISTANT,
        content="An answer.",
        latency_ms=latency_ms,
        created_at=at,
    )


async def test_chat_turns_show_up_in_the_report(clean_stack: Settings) -> None:
    async with chat_client(clean_stack) as client:
        owner = await sign_up(client, "owner@example.com")
        await upload_handbook(client, owner, clean_stack)
        first = await ask(client, owner, "Who may enter zone37?")
        await ask(client, owner, "Who may enter zone37?")  # from the cache: no tokens
        report = await _report(client, owner)

    usage = Usage(**first["usage"])
    totals = report["totals"]
    assert (totals["questions"], totals["cache_hits"], totals["cache_hit_rate"]) == (2, 1, 0.5)
    assert totals["tokens_in"] == usage.prompt_tokens > 0
    assert totals["tokens_out"] == usage.completion_tokens > 0
    assert totals["cost_usd"] == pytest.approx(float(answer_cost(usage, clean_stack)))
    assert totals["latency_p50_ms"] is not None
    assert len(report["days"]) == 30  # the default: the last 30 days
    [active_day] = [day for day in report["days"] if day["questions"]]
    assert (active_day["questions"], active_day["cache_hits"]) == (2, 1)


async def test_every_day_of_the_range_is_listed_and_only_your_numbers_count(
    api: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(api, "owner@example.com")
    other = await sign_up(api, "other@example.com", tenant_name="Other")
    async with AsyncSession(db_engine) as session, session.begin():
        session.add_all(
            [
                _usage_row(owner, date(2026, 1, 2), questions=4, cache_hits=1),
                _usage_row(owner, date(2026, 1, 4), questions=6, cache_hits=3),
                _usage_row(owner, date(2026, 1, 9), questions=50, cache_hits=0),  # after "to"
                _usage_row(other, date(2026, 1, 3), questions=99, cache_hits=99),
            ]
        )

    report = await _report(api, owner, **{"from": "2026-01-01", "to": "2026-01-05"})

    assert [day["day"] for day in report["days"]] == [f"2026-01-0{n}" for n in range(1, 6)]
    assert [day["questions"] for day in report["days"]] == [0, 4, 0, 6, 0]
    assert report["totals"] == {
        "questions": 10,
        "cache_hits": 4,
        "cache_hit_rate": 0.4,
        "tokens_in": 2500,
        "tokens_out": 500,
        "cost_usd": 0.0003,
        "latency_p50_ms": None,  # no answers in these days
        "latency_p95_ms": None,
    }


async def test_answer_times_are_percentiles_of_your_answers_in_the_range(
    api: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(api, "owner@example.com")
    other = await sign_up(api, "other@example.com", tenant_name="Other")
    now = datetime.now(UTC)
    async with AsyncSession(db_engine) as session, session.begin():
        owners_chat = ChatSession(tenant_id=owner.tenant_id)
        others_chat = ChatSession(tenant_id=other.tenant_id)
        session.add_all([owners_chat, others_chat])
        await session.flush()  # creates the IDs
        session.add_all(
            [_answer(owner, owners_chat.id, latency, now) for latency in range(100, 1001, 100)]
            + [
                _answer(owner, owners_chat.id, 99_999, now - timedelta(days=40)),  # too old
                _answer(other, others_chat.id, 99_999, now),  # another tenant
            ]
        )

    report = await _report(api, owner)

    # Ten answers: 100, 200, ..., 1000 ms. p50 lies between 500 and 600, p95 between 900 and 1000.
    assert report["totals"]["latency_p50_ms"] == 550.0
    assert report["totals"]["latency_p95_ms"] == 955.0
    # The same per day: all ten answers are from today (UTC); other days have none.
    by_day = {day["day"]: (day["latency_p50_ms"], day["latency_p95_ms"]) for day in report["days"]}
    assert by_day.pop(now.date().isoformat()) == (550.0, 955.0)
    assert set(by_day.values()) == {(None, None)}


async def test_the_longest_range_is_366_days(api: AsyncClient) -> None:
    owner = await sign_up(api, "owner@example.com")

    report = await _report(api, owner, **{"from": "2025-01-01", "to": "2026-01-01"})

    assert len(report["days"]) == 366


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-02-01", "to": "2026-01-31"},  # backwards
        {"from": "2025-01-01", "to": "2026-01-02"},  # 367 days
    ],
)
async def test_a_bad_date_range_gets_400(api: AsyncClient, params: dict[str, str]) -> None:
    owner = await sign_up(api, "owner@example.com")

    response = await api.get("/v1/analytics/usage", params=params, headers=owner.headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_date_range"
