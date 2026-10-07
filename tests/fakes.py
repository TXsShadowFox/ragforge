"""Fakes for tests: fast stand-ins for slow or external things."""

import re
import zlib
from collections.abc import Sequence


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
