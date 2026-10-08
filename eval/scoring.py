"""Scores. Search: where does the first chunk with the evidence rank (hit rate@k, MRR)?
Answers: an LLM judge says if an answer is faithful to its sources and correct."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from api.chat.retrieval import Source
from eval.data import Question, normalize
from shared.llm import ChatMessage

MRR_DEPTH = 10  # MRR@10: a first relevant chunk below place 10 counts as not found


def is_relevant(source: Source, question: Question) -> bool:
    """The chunk is from the right document and contains the evidence phrase."""
    return (
        question.evidence is not None
        and source.filename == question.document
        and normalize(question.evidence) in normalize(source.text)
    )


def first_relevant_rank(sources: Sequence[Source], question: Question) -> int | None:
    """The place (1 = first) of the first relevant chunk, or None."""
    for place, source in enumerate(sources, start=1):
        if is_relevant(source, question):
            return place
    return None


@dataclass(frozen=True, slots=True)
class SearchScores:
    questions: int
    hit_at_1: float
    hit_at_3: float
    hit_at_5: float
    mrr: float  # MRR@10


def search_scores(ranks: Sequence[int | None]) -> SearchScores:
    """hit rate@k: share of questions with a relevant chunk in the first k.
    MRR: the mean of 1/place of the first relevant chunk (0 if not in the first 10)."""
    count = len(ranks)

    def hit_rate(k: int) -> float:
        return sum(1 for rank in ranks if rank is not None and rank <= k) / count

    reciprocal = sum(1 / rank for rank in ranks if rank is not None and rank <= MRR_DEPTH)
    return SearchScores(
        questions=count,
        hit_at_1=hit_rate(1),
        hit_at_3=hit_rate(3),
        hit_at_5=hit_rate(5),
        mrr=reciprocal / count,
    )


# ------------------------------------------------------------------- judge ----

JUDGE_RULES = """You check the answers of a question-answering system. The system must answer
only from the sources it was given, and cite them like [1].

Reply with one JSON object and nothing else:
{"faithful": "yes" | "partly" | "no", "correct": "yes" | "partly" | "no",
 "reason": "<one short sentence>"}

faithful: is every fact in the answer supported by the sources? "yes": all of them;
"partly": the main fact is, but some details are not; "no": the main fact is not supported.
correct: does the answer give the same facts as the reference answer? "yes": the key facts
match; "partly": some of them; "no": they do not match, or the answer gives no answer."""

SCORES = {"yes": 1.0, "partly": 0.5, "no": 0.0}


@dataclass(frozen=True, slots=True)
class Verdict:
    faithful: float  # 1, 0.5 or 0
    correct: float
    reason: str


def judge_messages(question: Question, sources: Sequence[Source], answer: str) -> list[ChatMessage]:
    numbered = "\n\n".join(
        f"[{number}] {source.filename}"
        + (f", page {source.page_number}" if source.page_number else "")
        + f"\n{source.text}"
        for number, source in enumerate(sources, start=1)
    )
    task = (
        f"Question: {question.question}\n\n"
        f"Reference answer: {question.answer}\n\n"
        f"Sources:\n{numbered}\n\n"
        f"Answer to check:\n{answer}"
    )
    return [ChatMessage("system", JUDGE_RULES), ChatMessage("user", task)]


def parse_verdict(reply: str) -> Verdict | None:
    """The judge's JSON (also inside other text), or None if it cannot be read."""
    match = re.search(r"\{.*\}", reply, flags=re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
        return Verdict(
            faithful=SCORES[str(data["faithful"]).strip().lower()],
            correct=SCORES[str(data["correct"]).strip().lower()],
            reason=str(data.get("reason", "")).strip(),
        )
    except (ValueError, KeyError, TypeError):
        return None
