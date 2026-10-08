"""Fakes for tests: fast stand-ins for slow or external things (models, the LLM)."""

import re
import zlib
from collections.abc import AsyncIterator, Sequence

from api.ai import AIServices
from api.chat.prompts import REWRITE_RULES
from shared.llm import ChatMessage, LLMError, Usage

_LONG_WORDS = re.compile(r"\w{4,}")


class FakeEmbedder:
    """Fast vectors that are always the same for the same text, without a model.

    Each word adds 1 to one of 64 slots, so texts that share words get similar vectors
    (like a real model, only much simpler). A "token" is one word.
    """

    dimension = 64
    max_tokens = 10_000

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in re.findall(r"\w+", text.lower()):
            vector[zlib.crc32(word.encode()) % self.dimension] += 1.0
        if not any(vector):
            vector[0] = 1.0  # cosine distance needs a vector that is not all zeros
        return vector


class FailingEmbedder(FakeEmbedder):
    """Fails the first `failures` calls, like a service that is down for a while."""

    def __init__(self, failures: int) -> None:
        self.failures_left = failures
        self.calls = 0

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.failures_left > 0:
            self.failures_left -= 1
            raise ConnectionError("the embedding service is down")
        return super().embed_documents(texts)


class FakeReranker:
    """Score = how many words (4+ letters) of the question are in the text.

    So with MIN_RERANK_SCORE=1, a chunk needs at least one shared word to be "relevant".
    """

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        words = set(_LONG_WORDS.findall(query.lower()))
        return [float(len(words & set(_LONG_WORDS.findall(text.lower())))) for text in texts]


class FakeLLM:
    """A predictable LLM. It records every call in `calls`.

    - A rewrite request gets: the conversation's first question + the last question.
    - A question gets an answer that quotes source [1] and cites it.
    """

    model = "fake-llm"

    def __init__(self) -> None:
        self.calls: list[list[ChatMessage]] = []

    async def complete(
        self, messages: Sequence[ChatMessage], usage: Usage, *, max_tokens: int | None = None
    ) -> str:
        self.calls.append(list(messages))
        reply = _rewrite(messages) if messages[0].content == REWRITE_RULES else _answer(messages)
        usage.prompt_tokens += sum(len(message.content.split()) for message in messages)
        usage.completion_tokens += len(reply.split())
        return reply

    async def stream(self, messages: Sequence[ChatMessage], usage: Usage) -> AsyncIterator[str]:
        reply = await self.complete(messages, usage)
        for piece in re.split(r"(?<= )", reply):  # word by word; the pieces join to the reply
            yield piece

    async def aclose(self) -> None:
        pass

    @property
    def prompts(self) -> str:
        """Everything that was sent to the LLM, as one text (to search in tests)."""
        return "\n".join(message.content for call in self.calls for message in call)


class FailingLLM(FakeLLM):
    """An LLM service that is down."""

    async def complete(
        self, messages: Sequence[ChatMessage], usage: Usage, *, max_tokens: int | None = None
    ) -> str:
        self.calls.append(list(messages))
        raise LLMError("the LLM service is down")


def fake_ai(llm: FakeLLM | None = None) -> AIServices:
    return AIServices(embedder=FakeEmbedder(), reranker=FakeReranker(), llm=llm or FakeLLM())


def _rewrite(messages: Sequence[ChatMessage]) -> str:
    text = messages[-1].content
    first_question = re.search(r"^User: (.*)$", text, flags=re.MULTILINE)
    last_question = text.rsplit("Last question: ", 1)[-1]
    return f"{first_question.group(1) if first_question else ''} {last_question}".strip()


def _answer(messages: Sequence[ChatMessage]) -> str:
    source = re.search(r"^\[1\] .*\n(.*)$", messages[-1].content, flags=re.MULTILINE)
    if source is None:
        return "I don't know based on the documents."
    return f"From the documents: {' '.join(source.group(1).split()[:8])} [1]."
