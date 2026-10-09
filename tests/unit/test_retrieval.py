"""Retrieval rules that the load test and the evaluation found:

- at most RERANK_CONCURRENCY reranker runs at the same time (each needs a lot of memory:
  10 users at once ran the API out of memory, and it was killed);
- only sources close to the best score go to the LLM (fewer tokens).
"""

import asyncio
import threading
import time
import uuid
from collections.abc import Sequence

import pytest

from api.ai import AIServices
from api.chat.retrieval import Source, pick_sources, rerank
from tests.fakes import FakeEmbedder, FakeLLM, FakeReranker

RUN_SECONDS = 0.05


class SlowReranker(FakeReranker):
    """Takes RUN_SECONDS, and counts how many runs happen at the same time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = 0
        self.most_at_once = 0

    def rerank(self, query: str, texts: Sequence[str]) -> list[float]:
        with self._lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        time.sleep(RUN_SECONDS)
        with self._lock:
            self.running -= 1
        return super().rerank(query, texts)


def _ai(reranker: FakeReranker, slots: int) -> AIServices:
    return AIServices(FakeEmbedder(), reranker, FakeLLM(), rerank_slots=asyncio.Semaphore(slots))


def _sources(*texts: str, scores: Sequence[float] = ()) -> list[Source]:
    return [
        Source(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            filename="rules.txt",
            page_number=None,
            text=text,
            score=scores[number] if scores else 0.0,
        )
        for number, text in enumerate(texts)
    ]


async def test_only_so_many_reranker_runs_happen_at_once() -> None:
    reranker = SlowReranker()
    ai = _ai(reranker, slots=2)

    results = await asyncio.gather(
        *(rerank("late fee", _sources("the gates", "late fee rules"), ai) for _ in range(6))
    )

    assert reranker.most_at_once == 2
    assert [result[0].text for result in results] == ["late fee rules"] * 6  # best first


async def test_a_cancelled_question_keeps_its_slot_until_its_run_ends() -> None:
    ai = _ai(SlowReranker(), slots=1)
    question = asyncio.create_task(rerank("late fee", _sources("late fee rules"), ai))
    await asyncio.sleep(RUN_SECONDS / 5)  # the run has started in its thread

    question.cancel()
    with pytest.raises(asyncio.CancelledError):
        await question

    assert ai.rerank_slots.locked()  # a thread cannot be stopped: the run goes on
    await asyncio.wait_for(ai.rerank_slots.acquire(), timeout=2)  # free when the run ended


def _scores(sources: Sequence[Source]) -> list[float]:
    return [source.score for source in sources]


def test_only_sources_close_to_the_best_one_are_kept() -> None:
    ranked = _sources("a", "b", "c", "d", scores=[4.0, 0.5, -0.9, -6.0])

    picked = pick_sources(ranked, 5, min_score=-10.0, margin=5.0)

    assert _scores(picked) == [4.0, 0.5, -0.9]  # -6.0 is 10 below the best


def test_no_source_reaches_the_minimum_score_so_nothing_is_kept() -> None:
    ranked = _sources("a", "b", scores=[-10.5, -11.0])

    assert pick_sources(ranked, 5, min_score=-10.0, margin=5.0) == []  # "I don't know"


def test_at_most_keep_sources_are_kept() -> None:
    ranked = _sources("a", "b", "c", scores=[1.0, 0.9, 0.8])

    assert _scores(pick_sources(ranked, 2, min_score=-10.0, margin=5.0)) == [1.0, 0.9]


def test_the_minimum_score_still_applies_within_the_margin() -> None:
    ranked = _sources("a", "b", scores=[-8.0, -10.5])

    assert _scores(pick_sources(ranked, 5, min_score=-10.0, margin=5.0)) == [-8.0]


def test_nothing_ranked_means_no_sources() -> None:
    assert pick_sources([], 5, min_score=-10.0, margin=5.0) == []
