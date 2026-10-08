"""The real models: the embedder (bge-small-en-v1.5) and the reranker (ms-marco-MiniLM-L-6-v2).

The first run downloads them (67 MB + 80 MB) into MODEL_CACHE_DIR; CI keeps them in its cache.
"""

import math
from collections.abc import Sequence

import pytest

from shared.config import Settings
from shared.embeddings import FastEmbedEmbedder
from shared.rerank import FastEmbedReranker

pytestmark = pytest.mark.integration

DEFAULTS = Settings.model_fields
PASSAGES = [
    "Students must attend at least 75 percent of classes to sit the final exam.",
    "Books can be borrowed for two weeks. A late fee of 5 rupees per day applies after that.",
    "The hostel gates close at 10 pm. Visitors are allowed only in the common room.",
]


@pytest.fixture(scope="module")
def embedder() -> FastEmbedEmbedder:
    return FastEmbedEmbedder(
        DEFAULTS["embedding_model"].default, DEFAULTS["model_cache_dir"].default
    )


@pytest.fixture(scope="module")
def reranker() -> FastEmbedReranker:
    return FastEmbedReranker(DEFAULTS["rerank_model"].default, DEFAULTS["model_cache_dir"].default)


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


def test_a_search_question_finds_its_passage(embedder: FastEmbedEmbedder) -> None:
    question = embedder.embed_query("When do the hostel gates close?")
    passages = embedder.embed_documents(PASSAGES)

    similarities = [_cosine(question, passage) for passage in passages]

    assert similarities.index(max(similarities)) == 2


@pytest.mark.parametrize(
    ("question", "best"),
    [
        ("What is the minimum attendance to write the exam?", 0),
        ("How long can I keep a library book?", 1),
        ("When do the hostel gates close?", 2),
    ],
)
def test_the_reranker_puts_the_right_passage_first_and_above_the_threshold(
    reranker: FastEmbedReranker, question: str, best: int
) -> None:
    scores = reranker.rerank(question, PASSAGES)

    assert scores.index(max(scores)) == best
    assert max(scores) >= DEFAULTS["min_rerank_score"].default


def test_an_unrelated_question_scores_below_the_threshold(reranker: FastEmbedReranker) -> None:
    scores = reranker.rerank("What is the capital of France?", PASSAGES)

    assert max(scores) < DEFAULTS["min_rerank_score"].default
