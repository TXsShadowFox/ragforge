"""Tests for the answer cache keys and for the cost of an answer."""

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from api.chat.usage import answer_cost
from shared.answer_cache import CacheKey, answer_setup, normalize_question
from shared.config import Settings
from shared.llm import Usage

TENANT = uuid.UUID("0199a000-0000-7000-8000-000000000001")
KEY = CacheKey(tenant_id=TENANT, docs_version=3, question="what is the fee", setup="top5:model")
# Groq's prices for gpt-oss-20b, in dollars per million tokens (the defaults).
PRICES = Settings.model_construct(
    llm_price_input_per_million=0.075, llm_price_output_per_million=0.3
)


@pytest.mark.parametrize(
    "question",
    ["what is the fee", "What is the FEE?", "  What  is the fee ?! ", "what is the fee."],
)
def test_small_differences_give_the_same_question(question: str) -> None:
    assert normalize_question(question) == "what is the fee"


def test_other_words_give_another_question() -> None:
    assert normalize_question("What is the late fee?") != normalize_question("What is the fee?")


def test_the_same_question_always_gets_the_same_keys() -> None:
    again = CacheKey(
        tenant_id=TENANT, docs_version=3, question="what is the fee", setup="top5:model"
    )

    assert again.redis_key == KEY.redis_key
    assert again.point_id == KEY.point_id


@pytest.mark.parametrize(
    "other",
    [
        replace(KEY, tenant_id=uuid.UUID("0199a000-0000-7000-8000-000000000002")),
        replace(KEY, docs_version=4),
        replace(KEY, question="what is the late fee"),
        replace(KEY, setup="top3:model"),
        replace(KEY, setup="top5:another-model"),
    ],
)
def test_anything_that_changes_the_answer_changes_the_keys(other: CacheKey) -> None:
    assert other.redis_key != KEY.redis_key
    assert other.point_id != KEY.point_id


@pytest.mark.parametrize(
    "change", [{"min_rerank_score": -5.0}, {"rerank_model": "BAAI/bge-reranker-base"}]
)
def test_search_settings_that_change_answers_are_in_the_setup(
    settings: Settings, change: dict[str, object]
) -> None:
    # Found in Phase 6: with a new MIN_RERANK_SCORE, old "I don't know" answers were reused.
    setup = answer_setup(settings, 5, "model")

    assert answer_setup(settings, 5, "model") == setup
    assert answer_setup(settings.model_copy(update=change), 5, "model") != setup
    assert answer_setup(settings, 3, "model") != setup
    assert answer_setup(settings, 5, "another-model") != setup


def test_the_redis_key_has_the_tenant_and_version_but_not_the_question_text() -> None:
    assert KEY.redis_key.startswith(f"answer:{TENANT}:3:")
    assert "fee" not in KEY.redis_key  # a hash: user text never becomes part of a key


def test_the_cost_adds_the_input_and_the_output_price() -> None:
    usage = Usage(prompt_tokens=2000, completion_tokens=500)

    # 2000 * 0.075 / 1M + 500 * 0.30 / 1M = 0.00015 + 0.00015
    assert answer_cost(usage, PRICES) == Decimal("0.000300")


def test_the_cost_is_rounded_to_millionths_of_a_dollar() -> None:
    usage = Usage(prompt_tokens=3, completion_tokens=1)  # 0.000000525 dollars

    assert answer_cost(usage, PRICES) == Decimal("0.000001")


def test_an_answer_without_llm_tokens_costs_nothing() -> None:
    assert answer_cost(Usage(), PRICES) == 0  # e.g. from the cache
