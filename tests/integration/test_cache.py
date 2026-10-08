"""The answer cache on the whole stack: real Redis, Qdrant and Postgres; fake models.

The fake embedder gives texts with the same words the same vector. So the question with
its words in another order is a semantic cache hit (similarity 1.0), and a question about
another zone is not (similarity 0.75).
"""

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from shared.clients import Clients
from shared.config import Settings
from shared.db.models import Message, MessageRole, UsageDaily
from tests.fakes import FakeLLM
from tests.integration.helpers import (
    ask,
    chat_client,
    running_worker,
    sign_up,
    sse_events,
    upload_and_wait,
    upload_handbook,
    with_redis_down,
)

pytestmark = pytest.mark.integration

QUESTION = "Who may enter zone37?"


@pytest.fixture(autouse=True)
def _log_chat_answers(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="api.chat.service")


def _cache_kinds(caplog: pytest.LogCaptureFixture) -> list[str | None]:
    """Where each answer came from, in order: "exact", "semantic" or None (the LLM)."""
    return [
        record.__dict__["cache"]
        for record in caplog.records
        if record.getMessage() == "Chat answered"
    ]


async def test_a_repeated_question_is_answered_from_the_cache(
    chat_api: AsyncClient,
    chat_settings: Settings,
    fake_llm: FakeLLM,
    db_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)

    first = await ask(chat_api, owner, QUESTION)
    again = await ask(chat_api, owner, "  who may enter ZONE37 ")  # the same, written differently

    assert (first["cache_hit"], again["cache_hit"]) == (False, True)
    assert len(fake_llm.calls) == 1  # the second answer did not need the LLM
    assert again["answer"] == first["answer"]
    assert again["citations"] == first["citations"]
    assert again["citations"][0]["page"] == 37
    assert again["usage"] == {"prompt_tokens": 0, "completion_tokens": 0}
    assert _cache_kinds(caplog) == [None, "exact"]
    async with db_engine.connect() as connection:
        answers = (
            await connection.execute(
                select(Message.cache_hit, Message.cost_usd)
                .where(Message.role == MessageRole.ASSISTANT)
                .order_by(Message.id)
            )
        ).all()
        today = (
            await connection.execute(
                select(
                    UsageDaily.questions,
                    UsageDaily.cache_hits,
                    UsageDaily.tokens_in,
                    UsageDaily.cost_usd,
                )
            )
        ).one()
    assert [cache_hit for cache_hit, _ in answers] == [False, True]
    assert answers[0].cost_usd > 0
    assert answers[1].cost_usd == 0
    assert (today.questions, today.cache_hits) == (2, 1)
    assert today.tokens_in == first["usage"]["prompt_tokens"]
    assert today.cost_usd == answers[0].cost_usd


async def test_the_same_words_in_another_order_hit_the_semantic_cache(
    chat_api: AsyncClient,
    chat_settings: Settings,
    fake_llm: FakeLLM,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)

    first = await ask(chat_api, owner, QUESTION)
    again = await ask(chat_api, owner, "May who enter zone37?")

    assert again["cache_hit"] is True
    assert again["answer"] == first["answer"]
    assert len(fake_llm.calls) == 1
    assert _cache_kinds(caplog) == [None, "semantic"]


async def test_other_questions_or_options_are_not_answered_from_the_cache(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)
    await ask(chat_api, owner, QUESTION)

    other_zone = await ask(chat_api, owner, "Who may enter zone12?")
    fewer_sources = await ask(chat_api, owner, QUESTION, top_k=2)

    assert other_zone["cache_hit"] is False
    assert other_zone["citations"][0]["page"] == 12
    assert fewer_sources["cache_hit"] is False
    assert len(fake_llm.calls) == 3


async def test_a_document_change_makes_older_answers_miss(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM, stack_clients: Clients
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)
    await ask(chat_api, owner, QUESTION)
    assert (await ask(chat_api, owner, QUESTION))["cache_hit"] is True

    async with running_worker(chat_settings):
        notice = await upload_and_wait(
            chat_api, owner, "notice.txt", b"The library closes early on Fridays."
        )
    after_upload = await ask(chat_api, owner, QUESTION)
    deleted = await chat_api.delete(f"/v1/documents/{notice['id']}", headers=owner.headers)
    after_delete = await ask(chat_api, owner, QUESTION)

    assert deleted.status_code == 202
    assert after_upload["cache_hit"] is False
    assert after_delete["cache_hit"] is False
    assert len(fake_llm.calls) == 3
    # Answers made with older documents were also deleted from Qdrant: one answer is left.
    stored = await stack_clients.qdrant.count(chat_settings.answer_cache_collection, exact=True)
    assert stored.count == 1


async def test_tenants_never_get_each_others_cached_answers(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM
) -> None:
    alice = await sign_up(chat_api, "alice@example.com", tenant_name="Alice College")
    bob = await sign_up(chat_api, "bob@example.com", tenant_name="Bob School")
    await upload_handbook(chat_api, alice, chat_settings)
    async with running_worker(chat_settings):
        await upload_and_wait(
            chat_api, bob, "visitors.txt", b"Visitors may enter the library after signing in."
        )
    # Both tenants now have docs_version 1: only the tenant tells their cached answers apart.

    alices = await ask(chat_api, alice, QUESTION)
    bobs = await ask(chat_api, bob, QUESTION)

    assert bobs["cache_hit"] is False
    assert bobs["answer"] != alices["answer"]
    assert [citation["filename"] for citation in bobs["citations"]] == ["visitors.txt"]
    assert len(fake_llm.calls) == 2


async def test_a_streamed_answer_can_come_from_the_cache(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)
    first = await ask(chat_api, owner, QUESTION)

    response = await chat_api.post(
        "/v1/chat", json={"question": QUESTION, "stream": True}, headers=owner.headers
    )
    events = sse_events(response.text)

    assert [name for name, _ in events] == ["start", "token", "done"]  # all words at once
    done = events[-1][1]
    assert events[1][1]["text"] == done["answer"] == first["answer"]
    assert done["cache_hit"] is True
    assert done["citations"] == first["citations"]
    assert len(fake_llm.calls) == 1


async def test_chat_still_works_when_redis_is_down(
    chat_settings: Settings, fake_llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    settings = with_redis_down(chat_settings)
    async with chat_client(settings, fake_llm) as client:
        owner = await sign_up(client, "owner@example.com")
        await upload_handbook(client, owner, settings)

        first = await ask(client, owner, QUESTION)
        again = await ask(client, owner, QUESTION)

    assert first["citations"][0]["page"] == 37
    # No exact cache without Redis, but the semantic cache (Qdrant) still answers.
    assert again["cache_hit"] is True
    assert _cache_kinds(caplog) == [None, "semantic"]
    assert len(fake_llm.calls) == 1
