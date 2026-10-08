"""One chat turn: question -> (rewrite) -> cache or (sources -> LLM answer) -> saved.

`prepare()` does everything before the LLM writes, so its errors (an unknown session, the
LLM being down) become normal HTTP errors. Then `answer()` or `answer_stream()` finish.
Each step uses its own short database session: no connection waits while the LLM writes.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Self

from sqlalchemy import Uuid, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.ai import AIServices
from api.chat.prompts import (
    NO_ANSWER,
    answer_messages,
    cited_numbers,
    is_no_answer,
    rewrite_messages,
)
from api.chat.retrieval import Source, find_sources
from api.chat.usage import add_daily_usage, answer_cost
from shared.answer_cache import AnswerCache, CachedAnswer, CacheKey, normalize_question
from shared.clients import Clients
from shared.config import Settings
from shared.db.models import ChatSession, Message, MessageRole, Tenant
from shared.llm import ChatMessage, Usage

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 300
# Reasoning models count their thinking in this limit too, so it is not tiny.
REWRITE_MAX_TOKENS = 300


class ChatSessionNotFoundError(Exception):
    """There is no chat session with this ID in the caller's tenant."""


@dataclass(frozen=True, slots=True)
class Citation:
    number: int  # the [n] in the answer
    document_id: uuid.UUID
    filename: str
    page: int | None
    snippet: str
    chunk_id: uuid.UUID

    @classmethod
    def from_source(cls, number: int, source: Source) -> Self:
        return cls(
            number=number,
            document_id=source.document_id,
            filename=source.filename,
            page=source.page_number,
            snippet=source.text[:SNIPPET_CHARS],
            chunk_id=source.chunk_id,
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Self:
        return cls(
            number=int(data["number"]),
            document_id=uuid.UUID(data["document_id"]),
            filename=str(data["filename"]),
            page=data["page"],
            snippet=str(data["snippet"]),
            chunk_id=uuid.UUID(data["chunk_id"]),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "document_id": str(self.document_id),
            "filename": self.filename,
            "page": self.page,
            "snippet": self.snippet,
            "chunk_id": str(self.chunk_id),
        }


@dataclass(slots=True)
class PreparedTurn:
    """Everything known before the LLM writes the answer."""

    tenant_id: uuid.UUID
    session_id: uuid.UUID
    is_new_session: bool
    question_id: uuid.UUID
    answer_id: uuid.UUID
    question: str
    cache_key: CacheKey
    vector: list[float]  # the question's vector: for the search and the semantic cache
    cached: CachedAnswer | None  # an answer from the cache: no search and no LLM call
    sources: list[Source]
    prompt: list[ChatMessage] | None  # None: cached, or nothing relevant was found
    usage: Usage
    started_at: float


@dataclass(frozen=True, slots=True)
class TurnResult:
    session_id: uuid.UUID
    message_id: uuid.UUID
    answer: str
    citations: list[Citation]
    usage: Usage
    cost_usd: Decimal
    cache_hit: bool
    latency_ms: int

    def to_json(self) -> dict[str, Any]:
        return {
            "message_id": str(self.message_id),
            "session_id": str(self.session_id),
            "answer": self.answer,
            "citations": [citation.to_json() for citation in self.citations],
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
            },
            "cache_hit": self.cache_hit,
            "latency_ms": self.latency_ms,
        }


class ChatService:
    def __init__(
        self, settings: Settings, clients: Clients, ai: AIServices, cache: AnswerCache
    ) -> None:
        self._settings = settings
        self._clients = clients
        self._ai = ai
        self._cache = cache

    async def prepare(
        self, tenant_id: uuid.UUID, question: str, session_id: uuid.UUID | None, top_k: int
    ) -> PreparedTurn:
        started_at = time.perf_counter()
        history: list[ChatMessage] = []
        async with self._clients.sessions() as session:
            new_session_id, question_id, answer_id = await _new_ids(session)
            docs_version = await session.scalar(
                select(Tenant.docs_version).where(Tenant.id == tenant_id)
            )
            if session_id is not None:
                history = await self._history(session, tenant_id, session_id)

        usage = Usage()
        search_question = question
        if history:  # a follow-up: make it a full question first ("and the fee?")
            rewritten = await self._ai.llm.complete(
                rewrite_messages(question, history), usage, max_tokens=REWRITE_MAX_TOKENS
            )
            search_question = rewritten.strip() or question

        # One vector for both the semantic cache and the search.
        vector = await asyncio.to_thread(self._ai.embedder.embed_query, search_question)
        cache_key = CacheKey(
            tenant_id=tenant_id,
            docs_version=docs_version or 0,
            question=normalize_question(search_question),
            setup=f"top{top_k}:{self._ai.llm.model}",
        )
        cached = await self._cache.get(cache_key, vector)
        sources = (
            []
            if cached
            else await find_sources(
                search_question,
                vector,
                tenant_id,
                top_k,
                settings=self._settings,
                clients=self._clients,
                ai=self._ai,
            )
        )
        return PreparedTurn(
            tenant_id=tenant_id,
            session_id=session_id or new_session_id,
            is_new_session=session_id is None,
            question_id=question_id,
            answer_id=answer_id,
            question=question,
            cache_key=cache_key,
            vector=vector,
            cached=cached,
            sources=sources,
            prompt=answer_messages(search_question, sources) if sources else None,
            usage=usage,
            started_at=started_at,
        )

    async def answer(self, turn: PreparedTurn) -> TurnResult:
        if turn.cached is not None:
            return await self._save(turn, turn.cached.answer)
        if turn.prompt is None:
            return await self._save(turn, NO_ANSWER)
        return await self._save(turn, await self._ai.llm.complete(turn.prompt, turn.usage))

    async def answer_stream(self, turn: PreparedTurn) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Events: "start" (IDs), "token" (pieces of the answer), "done" (the full result)."""
        yield "start", {"session_id": str(turn.session_id), "message_id": str(turn.answer_id)}
        if turn.prompt is None:  # a cached answer, or "I don't know": all at once
            text = turn.cached.answer if turn.cached is not None else NO_ANSWER
            yield "token", {"text": text}
        else:
            pieces: list[str] = []
            async for piece in self._ai.llm.stream(turn.prompt, turn.usage):
                pieces.append(piece)
                yield "token", {"text": piece}
            text = "".join(pieces)
        result = await self._save(turn, text)
        yield "done", result.to_json()

    async def _history(
        self, session: AsyncSession, tenant_id: uuid.UUID, session_id: uuid.UUID
    ) -> list[ChatMessage]:
        """The last messages of the conversation, oldest first."""
        found = await session.scalar(
            select(ChatSession.id).where(
                ChatSession.id == session_id, ChatSession.tenant_id == tenant_id
            )
        )
        if found is None:
            raise ChatSessionNotFoundError
        newest_first = await session.scalars(
            select(Message)
            .where(Message.session_id == session_id, Message.tenant_id == tenant_id)
            .order_by(Message.id.desc())  # uuidv7: newer messages have bigger IDs
            .limit(self._settings.chat_history_messages)
        )
        return [
            ChatMessage(
                "user" if message.role is MessageRole.USER else "assistant", message.content
            )
            for message in reversed(list(newest_first))
        ]

    async def _save(self, turn: PreparedTurn, text: str) -> TurnResult:
        text = text.strip() or NO_ANSWER
        cache_hit = turn.cached is not None
        citations = _citations(turn, text)
        cost = answer_cost(turn.usage, self._settings)
        latency_ms = round((time.perf_counter() - turn.started_at) * 1000)
        async with self._clients.sessions() as session, session.begin():
            if turn.is_new_session:
                session.add(ChatSession(id=turn.session_id, tenant_id=turn.tenant_id))
                await session.flush()  # the session row must exist before its messages
            session.add_all(
                [
                    Message(
                        id=turn.question_id,
                        tenant_id=turn.tenant_id,
                        session_id=turn.session_id,
                        role=MessageRole.USER,
                        content=turn.question,
                    ),
                    Message(
                        id=turn.answer_id,
                        tenant_id=turn.tenant_id,
                        session_id=turn.session_id,
                        role=MessageRole.ASSISTANT,
                        content=text,
                        citations=[citation.to_json() for citation in citations],
                        latency_ms=latency_ms,
                        tokens_in=turn.usage.prompt_tokens,
                        tokens_out=turn.usage.completion_tokens,
                        cost_usd=cost,
                        cache_hit=cache_hit,
                    ),
                ]
            )
            await add_daily_usage(
                session, turn.tenant_id, cache_hit=cache_hit, usage=turn.usage, cost=cost
            )
        if not cache_hit:
            await self._cache.put(
                turn.cache_key, turn.vector, text, [citation.to_json() for citation in citations]
            )
        logger.info(
            "Chat answered",
            extra={
                "cache_hit": cache_hit,
                "cache": turn.cached.kind if turn.cached else None,
                "sources": len(turn.sources),
                "latency_ms": latency_ms,
                "tokens_in": turn.usage.prompt_tokens,
                "tokens_out": turn.usage.completion_tokens,
                "cost_usd": str(cost),
            },
        )
        return TurnResult(
            session_id=turn.session_id,
            message_id=turn.answer_id,
            answer=text,
            citations=citations,
            usage=turn.usage,
            cost_usd=cost,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
        )


def _citations(turn: PreparedTurn, text: str) -> list[Citation]:
    if turn.cached is not None:
        return [Citation.from_json(data) for data in turn.cached.citations]
    if is_no_answer(text):
        return []
    return [
        Citation.from_source(number, turn.sources[number - 1])
        for number in cited_numbers(text, len(turn.sources))
    ]


async def _new_ids(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Three new uuidv7 IDs, smallest first, so the question sorts before its answer."""
    new_id = func.uuidv7(type_=Uuid())
    row = (await session.execute(select(new_id, new_id, new_id))).one()
    first, second, third = sorted(row)
    return first, second, third
