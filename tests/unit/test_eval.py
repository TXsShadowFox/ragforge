"""The evaluation's parts: the test set, the search scores, the judge's reply, the summary."""

import uuid

import pytest

from api.chat.prompts import NO_ANSWER
from api.chat.retrieval import Source
from eval.data import CorpusFile, Question, check_test_set, load_corpus, load_questions
from eval.harness import AnswerResult, GateScores, gate_at, summarize_answers
from eval.scoring import (
    Verdict,
    first_relevant_rank,
    is_relevant,
    judge_messages,
    parse_verdict,
    search_scores,
)
from shared.llm import Usage

QUESTION = Question(
    id="q1",
    kind="lookup",
    question="What is the fine for a late book?",
    answer="5 rupees per day.",
    document="library-guide.md",
    evidence="A book returned after its due date costs 5 rupees per day",
)


def _source(text: str, filename: str = "library-guide.md") -> Source:
    return Source(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename=filename,
        page_number=None,
        text=text,
    )


def test_the_committed_test_set_is_consistent() -> None:
    questions, corpus = load_questions(), load_corpus()

    assert check_test_set(questions, corpus) == []
    assert len(questions) >= 30
    assert sum(not question.answerable for question in questions) >= 5
    kinds = {file.file_type.name for file in corpus}
    assert {"PDF", "MARKDOWN", "HTML", "TEXT"} <= kinds


def test_a_broken_test_set_is_reported() -> None:
    corpus = [CorpusFile("notes.txt", b"The gym opens at 6 am.")]
    questions = [
        Question(id="a", kind="lookup", question="?", document="notes.txt", evidence="opens at 7"),
        Question(id="a", kind="lookup", question="?", document="gone.md", evidence="x"),
    ]

    problems = check_test_set(questions, corpus)

    assert "Question IDs are not unique." in problems
    assert any("not in notes.txt" in problem for problem in problems)
    assert any("no document named 'gone.md'" in problem for problem in problems)


def test_a_chunk_is_relevant_when_it_holds_the_evidence() -> None:
    wrapped = "Fines.\nA book returned after\nits due date COSTS 5 rupees per day. More text."

    assert is_relevant(_source(wrapped), QUESTION)  # line breaks and case do not matter
    assert not is_relevant(_source(wrapped, filename="other.md"), QUESTION)
    assert not is_relevant(_source("A laptop returned late costs 50 rupees."), QUESTION)


def test_the_first_relevant_place_is_found() -> None:
    sources = [_source("Opening hours."), _source(f"Fines. {QUESTION.evidence}.")]

    assert first_relevant_rank(sources, QUESTION) == 2
    assert first_relevant_rank(sources[:1], QUESTION) is None


def test_hit_rates_and_mrr() -> None:
    scores = search_scores([1, 2, None, 6, 12])

    assert scores.questions == 5
    assert scores.hit_at_1 == pytest.approx(1 / 5)
    assert scores.hit_at_3 == pytest.approx(2 / 5)
    assert scores.hit_at_5 == pytest.approx(2 / 5)
    # 1/1 + 1/2 + 1/6; place 12 is below the MRR@10 depth and counts as 0.
    assert scores.mrr == pytest.approx((1 + 1 / 2 + 1 / 6) / 5)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"faithful": "yes", "correct": "partly", "reason": "Misses the cap."}', (1.0, 0.5)),
        ('Here it is:\n```json\n{"faithful": "No", "correct": "no"}\n```', (0.0, 0.0)),
    ],
)
def test_the_judges_reply_is_read(reply: str, expected: tuple[float, float]) -> None:
    verdict = parse_verdict(reply)

    assert verdict is not None
    assert (verdict.faithful, verdict.correct) == expected


@pytest.mark.parametrize(
    "reply",
    ["It looks fine to me.", '{"faithful": "maybe", "correct": "yes"}', '{"correct": "yes"}'],
)
def test_a_reply_that_is_not_a_verdict_gives_none(reply: str) -> None:
    assert parse_verdict(reply) is None


def test_the_judge_sees_the_question_the_reference_the_sources_and_the_answer() -> None:
    sources = [_source(f"Fines. {QUESTION.evidence}.")]

    system, task = judge_messages(QUESTION, sources, "It costs 5 rupees a day [1].")

    assert "JSON" in system.content
    for part in [QUESTION.question, "5 rupees per day.", "[1] library-guide.md", "a day [1]."]:
        assert part in task.content


def test_the_gate_stops_questions_whose_best_score_is_too_low() -> None:
    unanswerable = Question(id="u1", kind="not-in-documents", question="Who is the principal?")
    questions = [QUESTION, unanswerable]
    best = {QUESTION.id: -6.0, unanswerable.id: -9.5}

    assert gate_at(questions, best, -5.0) == GateScores(
        stopped_unanswerable=1, unanswerable=1, stopped_answerable=1, answerable=1
    )
    lower = gate_at(questions, best, -8.0)
    assert (lower.stopped_answerable, lower.stopped_unanswerable) == (0, 1)
    assert gate_at(questions, best, -9.5).stopped_unanswerable == 0  # "at least" the threshold
    # No chunk at all: always stopped.
    assert gate_at([QUESTION], {QUESTION.id: None}, -100.0).stopped_answerable == 1


def _result(
    question: Question, answer: str, verdict: Verdict | None, seconds: float | None
) -> AnswerResult:
    return AnswerResult(
        question=question,
        answer=answer,
        abstained=answer == NO_ANSWER,
        verdict=verdict,
        judge_failed=False,
        llm_seconds=seconds,
        usage=Usage(prompt_tokens=100, completion_tokens=20) if seconds else Usage(),
        sources=5 if seconds else 0,
    )


def test_the_answer_summary() -> None:
    unanswerable = Question(id="u1", kind="not-in-documents", question="Who is the principal?")
    results = [
        _result(QUESTION, "5 rupees a day [1].", Verdict(1.0, 1.0, "ok"), 1.0),
        _result(QUESTION, "About 5 rupees [1].", Verdict(1.0, 0.5, "vague"), 3.0),
        _result(QUESTION, NO_ANSWER, None, None),  # a wrong "I don't know"
        _result(unanswerable, NO_ANSWER, None, None),  # a right one
    ]

    summary = summarize_answers(results)

    assert (summary.answerable, summary.answered) == (3, 2)
    assert summary.faithfulness == pytest.approx(1.0)
    assert summary.correctness == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert (summary.unanswerable, summary.abstained_unanswerable) == (1, 1)
    assert summary.llm_p50_seconds == pytest.approx(2.0)
    assert summary.llm_p95_seconds == pytest.approx(2.9)
    assert summary.tokens_in == pytest.approx(100)
