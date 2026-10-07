"""The real embedding model (bge-small-en-v1.5 with fastembed).

The first run downloads the model (67 MB) into MODEL_CACHE_DIR; CI keeps it in its cache.
"""

import math
from collections.abc import Sequence

import pytest

from shared.config import Settings
from shared.embeddings import FastEmbedEmbedder

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def embedder() -> FastEmbedEmbedder:
    defaults = Settings.model_fields
    return FastEmbedEmbedder(
        defaults["embedding_model"].default, defaults["model_cache_dir"].default
    )


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_the_model_reads_512_tokens_and_makes_384_numbers(embedder: FastEmbedEmbedder) -> None:
    assert embedder.max_tokens == 512
    assert embedder.dimension == 384


def test_tokens_are_counted_without_the_special_tokens(embedder: FastEmbedEmbedder) -> None:
    assert embedder.count_tokens("hello world") == 2
    long_text = "word " * 2000
    assert embedder.count_tokens(long_text) == 2000  # no 512-token cut when counting


def test_texts_with_the_same_meaning_are_close(embedder: FastEmbedEmbedder) -> None:
    question, answer, unrelated = embedder.embed_documents(
        [
            "What is the attendance rule?",
            "Students must attend 75% of classes to sit the exam.",
            "The canteen opens at 9 am.",
        ]
    )

    assert len(question) == 384
    assert _cosine(question, answer) > _cosine(question, unrelated)
