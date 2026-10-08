"""Reranking: score how well each chunk answers the question.

A cross-encoder model reads the question and the chunk together, so it judges relevance
much better than comparing two separate vectors. It is also slower, so we use it only on
the ~20 best candidates of the first search.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from fastembed.rerank.cross_encoder import TextCrossEncoder


class Reranker(Protocol):
    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        """One score per text; higher means more relevant. Slow: use `asyncio.to_thread`."""
        ...


class FastEmbedReranker:
    """Runs a cross-encoder locally with fastembed (default: ms-marco-MiniLM-L-6-v2, 80 MB).

    Creating it loads the model, and downloads it the first time.
    """

    def __init__(self, model_name: str, cache_dir: Path, batch_size: int = 8) -> None:
        self._model = TextCrossEncoder(model_name=model_name, cache_dir=str(cache_dir))
        self._batch_size = batch_size

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        scores = self._model.rerank(query, list(texts), batch_size=self._batch_size)
        return [float(score) for score in scores]
