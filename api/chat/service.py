"""One chat turn: question -> (rewrite) -> sources -> LLM answer -> citations -> saved.

`prepare()` does everything before the LLM writes, so its errors (an unknown session, the
LLM being down) become normal HTTP errors. Then `answer()` or `answer_stream()` finish.
Each step uses its own short database session: no connection waits while the LLM writes.
"""

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
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
from shared.clients import Clients
from shared.config import Settings
from shared.db.models import ChatSession, Message, MessageRole
from shared.llm import ChatMessage, Usage

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
    sources: list[Source]
    prompt: list[ChatMessage] | None  # None: nothing relevant was found; no LLM call
    usage: Usage
    started_at: float


@dataclass(frozen=True, slots=True)
class TurnResult:
    session_id: uuid.UUID
    message_id: uuid.UUID
    answer: str
    citations: list[Citation]
    usage: Usage
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
            "latency_ms": self.latency_ms,
        }


class ChatService:
    def __init__(self, settings: Settings, clients: Clients, ai: AIServices) -> None:
        self._settings = settings
        self._clients = clients
        self._ai = ai

    async def prepare(
        self, tenant_id: uuid.UUID, question: str, session_id: uuid.UUID | None, top_k: int
    ) -> PreparedTurn:
        started_at = time.perf_counter()
        history: list[ChatMessage] = []
        async with self._clients.sessions() as session:
            new_session_id, question_id, answer_id = await _new_ids(session)
            if session_id is not None:
                history = await self._history(session, tenant_id, session_id)

        usage = Usage()
        search_question = question
        if history:  # a follow-up: make it a full question first ("and the fee?")
            rewritten = await self._ai.llm.complete(
                rewrite_messages(question, history), usage, max_tokens=REWRITE_MAX_TOKENS
            )
            search_question = rewritten.strip() or question

        sources = await find_sources(
            search_question,
            tenant_id,
            top_k,
            settings=self._settings,
            clients=self._clients,
            ai=self._ai,
        )
        return PreparedTurn(
            tenant_id=tenant_id,
            session_id=session_id or new_session_id,
            is_new_session=session_id is None,
            question_id=question_id,
            answer_id=answer_id,
            question=question,
            sources=sources,
            prompt=answer_messages(search_question, sources) if sources else None,
            usage=usage,
            started_at=started_at,
        )

    async def answer(self, turn: PreparedTurn) -> TurnResult:
        if turn.prompt is None:
            return await self._save(turn, NO_ANSWER)
        return await self._save(turn, await self._ai.llm.complete(turn.prompt, turn.usage))

    async def answer_stream(self, turn: PreparedTurn) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Events: "start" (IDs), "token" (pieces of the answer), "done" (the full result)."""
        yield "start", {"session_id": str(turn.session_id), "message_id": str(turn.answer_id)}
        if turn.prompt is None:
            text = NO_ANSWER
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
        citations = (
            []
            if is_no_answer(text)
            else [
                Citation.from_source(number, turn.sources[number - 1])
                for number in cited_numbers(text, len(turn.sources))
            ]
        )
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
                    ),
                ]
            )
        return TurnResult(
            session_id=turn.session_id,
            message_id=turn.answer_id,
            answer=text,
            citations=citations,
            usage=turn.usage,
            latency_ms=latency_ms,
        )


async def _new_ids(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Three new uuidv7 IDs, smallest first, so the question sorts before its answer."""
    new_id = func.uuidv7(type_=Uuid())
    row = (await session.execute(select(new_id, new_id, new_id))).one()
    first, second, third = sorted(row)
    return first, second, third
