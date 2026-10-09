"""Chat on the whole stack: real Postgres and Qdrant; fake embedder, reranker and LLM.

The handbook has 50 pages; only page N mentions "zoneN". The fake reranker counts shared
words, and MIN_RERANK_SCORE=1 here, so a chunk needs a word in common to be "relevant".
"""

from collections.abc import Sequence

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from api.ai import AIServices
from api.chat.prompts import NO_ANSWER
from api.main import create_app
from shared.config import Settings
from shared.db.models import Chunk, Feedback, Message, MessageRole
from tests.fakes import FailingLLM, FakeEmbedder, FakeLLM, FakeReranker
from tests.integration.helpers import (
    ask,
    chat_client,
    running_worker,
    sign_up,
    sse_events,
    upload_and_wait,
    upload_handbook,
)

pytestmark = pytest.mark.integration


async def test_an_answer_cites_the_right_document_and_page(
    chat_api: AsyncClient, chat_settings: Settings, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)

    answer = await ask(chat_api, owner, "Who may enter zone37?")

    assert answer["answer"].endswith("[1].")
    [citation] = answer["citations"]
    assert citation["number"] == 1
    assert citation["filename"] == "handbook.pdf"
    assert citation["page"] == 37
    async with db_engine.connect() as connection:
        cited_text = await connection.scalar(
            select(Chunk.text).where(Chunk.id == citation["chunk_id"])
        )
    assert cited_text is not None
    assert "zone37 " in cited_text  # the cited chunk really holds the answer
    assert answer["usage"]["prompt_tokens"] > 0
    assert answer["latency_ms"] >= 0


async def test_a_question_not_in_the_documents_gets_i_dont_know(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)

    answer = await ask(chat_api, owner, "What is the capital of France?")

    assert answer["answer"] == NO_ANSWER
    assert answer["citations"] == []
    assert fake_llm.calls == []  # nothing relevant was found, so the LLM was not asked


async def test_the_llm_reads_at_most_top_k_sources(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)

    await ask(chat_api, owner, "Who may enter after dark?", top_k=2)

    [call] = fake_llm.calls
    assert '<source id="1" location="handbook.pdf' in call[-1].content
    assert '<source id="2" location="handbook.pdf' in call[-1].content
    assert '<source id="3"' not in call[-1].content


async def test_tenants_never_get_each_others_text(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM
) -> None:
    alice = await sign_up(chat_api, "alice@example.com", tenant_name="Alice College")
    bob = await sign_up(chat_api, "bob@example.com", tenant_name="Bob School")
    await upload_handbook(chat_api, alice, chat_settings)
    async with running_worker(chat_settings):
        await upload_and_wait(
            chat_api, bob, "visitors.txt", b"Visitors may enter the library after signing in."
        )

    answer = await ask(chat_api, bob, "Who may enter zone37?")

    # Bob's question shares "enter" with his own file, so the LLM is asked, but only
    # with Bob's text: Alice's handbook never reaches the prompt or the citations.
    assert "Visitors may enter the library" in fake_llm.prompts
    assert "handbook" not in fake_llm.prompts
    assert "blue pass" not in fake_llm.prompts
    assert [citation["filename"] for citation in answer["citations"]] == ["visitors.txt"]


async def test_documents_that_are_not_ready_are_not_used(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)
    [document] = (await chat_api.get("/v1/documents", headers=owner.headers)).json()["items"]
    # No worker runs now, so the document stays "deleting": its chunks are still stored.
    await chat_api.delete(f"/v1/documents/{document['id']}", headers=owner.headers)

    answer = await ask(chat_api, owner, "Who may enter zone37?")

    assert answer["answer"] == NO_ANSWER
    assert fake_llm.calls == []


async def test_a_streamed_answer_sends_the_words_then_the_citations(
    chat_api: AsyncClient, chat_settings: Settings, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)

    response = await chat_api.post(
        "/v1/chat",
        json={"question": "Who may enter zone21?", "stream": True},
        headers=owner.headers,
    )
    events = sse_events(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    names = [name for name, _ in events]
    assert names[0] == "start"
    assert names[-1] == "done"
    assert set(names[1:-1]) == {"token"}
    done = events[-1][1]
    assert "".join(data["text"] for name, data in events if name == "token") == done["answer"]
    assert done["citations"][0]["page"] == 21
    assert events[0][1]["message_id"] == done["message_id"]
    async with db_engine.connect() as connection:
        saved = await connection.scalar(
            select(Message.content).where(Message.id == done["message_id"])
        )
    assert saved == done["answer"]


async def test_a_follow_up_question_uses_the_conversation(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)
    first = await ask(chat_api, owner, "Who may enter zone12?")

    # Alone, "after dark" fits every page. With the conversation, it is about zone12.
    follow_up = await ask(chat_api, owner, "What about after dark?", session_id=first["session_id"])

    assert follow_up["session_id"] == first["session_id"]
    rewrite_call = fake_llm.calls[1]
    assert "User: Who may enter zone12?" in rewrite_call[-1].content
    assert follow_up["citations"][0]["page"] == 12
    async with db_engine.connect() as connection:
        roles = (
            await connection.scalars(
                select(Message.role)
                .where(Message.session_id == first["session_id"])
                .order_by(Message.id)
            )
        ).all()
    assert roles == [MessageRole.USER, MessageRole.ASSISTANT] * 2


async def test_an_unknown_or_foreign_session_gets_404(chat_api: AsyncClient) -> None:
    alice = await sign_up(chat_api, "alice@example.com")
    bob = await sign_up(chat_api, "bob@example.com")
    alices_turn = await ask(chat_api, alice, "Hello?")

    for session_id in [alices_turn["session_id"], "01990000-0000-7000-8000-000000000000"]:
        response = await chat_api.post(
            "/v1/chat", json={"question": "Hi?", "session_id": session_id}, headers=bob.headers
        )
        assert response.status_code == 404


async def test_feedback_is_saved_and_can_be_changed(
    chat_api: AsyncClient, chat_settings: Settings, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    stranger = await sign_up(chat_api, "stranger@example.com")
    await upload_handbook(chat_api, owner, chat_settings)
    answer = await ask(chat_api, owner, "Who may enter zone5?")
    path = f"/v1/messages/{answer['message_id']}/feedback"

    first = await chat_api.post(path, json={"rating": "up"}, headers=owner.headers)
    second = await chat_api.post(
        path, json={"rating": "down", "comment": "Wrong page"}, headers=owner.headers
    )
    foreign = await chat_api.post(path, json={"rating": "up"}, headers=stranger.headers)

    assert first.status_code == second.status_code == 200
    assert second.json()["rating"] == "down"
    assert foreign.status_code == 404
    async with db_engine.connect() as connection:
        rows = (await connection.execute(select(Feedback.rating, Feedback.comment))).all()
    assert [tuple(row) for row in rows] == [("down", "Wrong page")]


async def test_feedback_is_only_for_answers(chat_api: AsyncClient, db_engine: AsyncEngine) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    answer = await ask(chat_api, owner, "Hello?")
    async with db_engine.connect() as connection:
        question_id = await connection.scalar(
            select(Message.id).where(
                Message.session_id == answer["session_id"], Message.role == MessageRole.USER
            )
        )

    response = await chat_api.post(
        f"/v1/messages/{question_id}/feedback", json={"rating": "up"}, headers=owner.headers
    )

    assert response.status_code == 404


async def test_an_llm_failure_gives_503_or_an_error_event(
    chat_settings: Settings, db_engine: AsyncEngine
) -> None:
    async with chat_client(chat_settings, FakeLLM()) as client:
        owner = await sign_up(client, "owner@example.com")
        await upload_handbook(client, owner, chat_settings)
    async with chat_client(chat_settings, FailingLLM()) as client:
        plain = await client.post(
            "/v1/chat", json={"question": "Who may enter zone3?"}, headers=owner.headers
        )
        streamed = await client.post(
            "/v1/chat",
            json={"question": "Who may enter zone3?", "stream": True},
            headers=owner.headers,
        )

    assert plain.status_code == 503
    assert plain.json()["error"]["code"] == "llm_unavailable"
    assert [name for name, _ in sse_events(streamed.text)] == ["start", "error"]
    assert sse_events(streamed.text)[-1][1]["code"] == "llm_unavailable"
    async with db_engine.connect() as connection:  # failed turns are not saved
        assert await connection.scalar(select(func.count()).select_from(Message)) == 0


async def test_a_chat_answer_never_needs_two_database_connections(chat_settings: Settings) -> None:
    # Found by the load test: the login check kept its connection while the answer's steps
    # needed another one, so under load each request held one and waited for a second
    # (50 users: 92% errors). With one pooled connection, that waited until the timeout.
    one_connection = chat_settings.model_copy(
        update={"db_pool_size": 1, "db_max_overflow": 0, "db_pool_timeout_seconds": 2.0}
    )
    async with chat_client(one_connection) as client:
        owner = await sign_up(client, "owner@example.com")
        async with running_worker(chat_settings):  # the worker has its own, normal pool
            document = await upload_and_wait(
                client, owner, "zones.txt", b"Only staff may enter zone37."
            )
        assert document["status"] == "ready"

        answer = await ask(client, owner, "Who may enter zone37?")

    assert answer["citations"][0]["filename"] == "zones.txt"


class RecordingReranker(FakeReranker):
    """Remembers how many chunks each run scored."""

    def __init__(self) -> None:
        self.batches: list[int] = []

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        self.batches.append(len(texts))
        return super().rerank(query, texts)


async def test_the_reranker_scores_only_the_best_candidates(chat_settings: Settings) -> None:
    settings = chat_settings.model_copy(update={"rerank_candidates": 3})
    reranker = RecordingReranker()
    app = create_app(settings, ai=AIServices(FakeEmbedder(), reranker, FakeLLM()))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        owner = await sign_up(client, "owner@example.com")
        await upload_handbook(client, owner, settings)  # 50 pages: each search finds 20

        answer = await ask(client, owner, "Who may enter zone37?")

    assert reranker.batches == [3]
    assert answer["citations"][0]["page"] == 37  # the fusion had it near the top
