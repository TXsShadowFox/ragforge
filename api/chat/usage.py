"""Usage and cost: what each answer cost, and each tenant's daily totals."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import Settings
from shared.db.models import UsageDaily
from shared.llm import Usage

MILLION = Decimal(1_000_000)
CENT_FRACTIONS = Decimal("0.000001")  # we store dollars with 6 decimals


def answer_cost(usage: Usage, settings: Settings) -> Decimal:
    """The LLM's price for these tokens, in US dollars."""
    price_in = Decimal(str(settings.llm_price_input_per_million))
    price_out = Decimal(str(settings.llm_price_output_per_million))
    cost = (usage.prompt_tokens * price_in + usage.completion_tokens * price_out) / MILLION
    return cost.quantize(CENT_FRACTIONS)


async def add_daily_usage(
    session: AsyncSession, tenant_id: uuid.UUID, *, cache_hit: bool, usage: Usage, cost: Decimal
) -> None:
    """Add one answered question to today's totals (UTC), in the caller's transaction.

    One INSERT ... ON CONFLICT DO UPDATE: the first answer of the day creates the row,
    the next ones add to it, and two answers at the same moment cannot lose a count.
    """
    row = insert(UsageDaily).values(
        tenant_id=tenant_id,
        day=datetime.now(UTC).date(),
        questions=1,
        cache_hits=int(cache_hit),
        tokens_in=usage.prompt_tokens,
        tokens_out=usage.completion_tokens,
        cost_usd=cost,
    )
    await session.execute(
        row.on_conflict_do_update(
            index_elements=[UsageDaily.tenant_id, UsageDaily.day],
            set_={
                "questions": UsageDaily.questions + row.excluded.questions,
                "cache_hits": UsageDaily.cache_hits + row.excluded.cache_hits,
                "tokens_in": UsageDaily.tokens_in + row.excluded.tokens_in,
                "tokens_out": UsageDaily.tokens_out + row.excluded.tokens_out,
                "cost_usd": UsageDaily.cost_usd + row.excluded.cost_usd,
            },
        )
    )
