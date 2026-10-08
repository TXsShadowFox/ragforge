"""The evaluation end to end on the test containers, with fake models and a fake judge.

The real run (`make eval`) needs the real models and Groq; this keeps its code working.
"""

import json
from collections.abc import Sequence

import pytest

from eval.data import load_corpus, load_questions
from eval.harness import SEARCH_SETTINGS, run_evaluation
from eval.report import RunInfo, render_json, render_markdown
from shared.clients import Clients
from shared.config import Settings
from shared.llm import ChatMessage, Usage
from tests.fakes import FakeLLM, fake_ai

pytestmark = pytest.mark.integration


class FakeJudge(FakeLLM):
    """Always the same verdict, in the judge's JSON format."""

    model = "fake-judge"

    async def complete(
        self, messages: Sequence[ChatMessage], usage: Usage, *, max_tokens: int | None = None
    ) -> str:
        self.calls.append(list(messages))
        return 'Verdict: {"faithful": "yes", "correct": "partly", "reason": "Close."}'


async def test_the_evaluation_measures_every_setting_and_writes_a_report(
    clean_stack: Settings, stack_clients: Clients
) -> None:
    questions = load_questions()
    picked = [q for q in questions if q.answerable][:4] + [
        q for q in questions if not q.answerable
    ][:2]
    corpus = load_corpus()
    judge = FakeJudge()

    evaluation = await run_evaluation(
        picked, corpus, settings=clean_stack, clients=stack_clients, ai=fake_ai(), judge=judge
    )

    assert set(evaluation.chunks) == {300, 500, 1000}
    assert evaluation.chunks[300] > evaluation.chunks[1000] > 0  # smaller chunks: more of them
    assert [result.setting for result in evaluation.search] == list(SEARCH_SETTINGS)
    for result in evaluation.search:
        assert result.scores.questions == 4
        assert 0 <= result.scores.mrr <= 1
    gate = evaluation.search[0].gate
    assert gate is not None
    assert (gate.answerable, gate.unanswerable) == (4, 2)
    assert evaluation.search[1].gate is None  # without the reranker there is no score
    assert set(evaluation.search[0].best_scores) == {q.id for q in picked}
    assert set(evaluation.search[1].best_scores.values()) == {None}
    assert [answer.question.id for answer in evaluation.answers] == [q.id for q in picked]
    judged = [answer.verdict for answer in evaluation.answers if answer.verdict]
    assert len(judged) == len(judge.calls) == 4  # only questions with an answer are judged
    assert {verdict.correct for verdict in judged} == {0.5}

    info = RunInfo(
        date="2026-10-08",
        embedding_model="fake",
        rerank_model="fake",
        llm_model="fake-llm",
        judge_model="fake-judge",
        min_rerank_score=clean_stack.min_rerank_score,
        max_embedding_tokens=512,
        seconds=60.0,
    )
    markdown = render_markdown(evaluation, picked, corpus, info)
    for heading in ["# Evaluation results", "## Search quality", "## Answer quality"]:
        assert heading in markdown
    assert f"| {clean_stack.min_rerank_score:g} (the setting) |" in markdown  # threshold table
    data = json.loads(render_json(evaluation, info))
    assert set(data["search"][0]["best_scores"]) == {q.id for q in picked}
    assert data["answers"]["summary"]["answerable"] == 4
    assert data["answers"]["summary"]["correctness"] == pytest.approx(0.5)
