"""The evaluation report: eval/RESULTS.md (for people) and eval/results.json (the numbers)."""

import dataclasses
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from eval.data import CorpusFile, Question
from eval.harness import (
    DEFAULT_CHUNK_SIZE,
    TOP_K,
    AnswerResult,
    Evaluation,
    gate_at,
    summarize_answers,
)

# Printed under the answer table: why the p95 can be much higher than the p50.
LLM_TIME_NOTE = (
    'LLM times include the waits when Groq\'s free tier says "too many tokens per minute" '
    "(the client waits once, up to 10 s), so the p95 shows that limit more than the model's "
    "speed."
)

# MIN_RERANK_SCORE values compared in the report.
GATE_THRESHOLDS = (-4.0, -5.0, -6.0, -7.0, -8.0, -9.0, -10.0, -11.0)


@dataclass(frozen=True, slots=True)
class RunInfo:
    date: str
    embedding_model: str
    rerank_model: str
    llm_model: str
    judge_model: str | None
    min_rerank_score: float
    max_embedding_tokens: int
    seconds: float


def render_markdown(
    evaluation: Evaluation,
    questions: Sequence[Question],
    corpus: Sequence[CorpusFile],
    info: RunInfo,
) -> str:
    answerable = sum(question.answerable for question in questions)
    words = sum(len(page.text.split()) for file in corpus for page in file.pages())
    lines = [
        "# Evaluation results",
        "",
        f"Made by `make eval` on {info.date}, in {info.seconds / 60:.0f} minutes.",
        "",
        f"- **Test set:** {len(corpus)} documents of a made-up college ({words:,} words: "
        f"{', '.join(file.filename for file in corpus)}) and {len(questions)} questions: "
        f"{answerable} with an answer in the documents, {len(questions) - answerable} without.",
        f"- **Models:** embedder `{info.embedding_model}`, reranker `{info.rerank_model}`, "
        f"LLM `{info.llm_model}`" + (f", judge `{info.judge_model}`." if info.judge_model else "."),
        "",
        "## Search quality",
        "",
        "Is a chunk with the answer's evidence among the first k results? "
        f"({answerable} questions; MRR = mean of 1/place of the first such chunk, "
        "0 below place 10.)",
        "",
        "| Setting | Chunks | hit@1 | hit@3 | hit@5 | MRR@10 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for result in evaluation.search:
        scores = result.scores
        lines.append(
            f"| {result.setting.name} | {evaluation.chunks[result.setting.chunk_size]} "
            f"| {scores.hit_at_1:.0%} | {scores.hit_at_3:.0%} | {scores.hit_at_5:.0%} "
            f"| {scores.mrr:.3f} |"
        )
    lines += [
        "",
        f"The embedder reads at most {info.max_embedding_tokens} tokens, so a 1000-token chunk "
        "is embedded from its first half only (the keyword search and the reranker still "
        "read more of it).",
    ]
    default = evaluation.search[0]
    if default.gate is not None:
        gate = default.gate
        lines += [
            "",
            f'**The "I don\'t know" gate** (no chunk scores at least '
            f"MIN_RERANK_SCORE = {info.min_rerank_score:g}, so the LLM is not called): it "
            f"stopped {gate.stopped_unanswerable} of {gate.unanswerable} questions that are "
            f"not in the documents, and {gate.stopped_answerable} of {gate.answerable} "
            "that are.",
            "",
            "What other thresholds would do, with the reranker's best score per question:",
            "",
            "| MIN_RERANK_SCORE | Stops wrongly (the documents have the answer) "
            "| Stops rightly (they do not) |",
            "|---:|---:|---:|",
        ]
        for threshold in GATE_THRESHOLDS:
            option = gate_at(questions, default.best_scores, threshold)
            mark = " (the setting)" if threshold == info.min_rerank_score else ""
            lines.append(
                f"| {threshold:g}{mark} | {option.stopped_answerable} of {option.answerable} "
                f"| {option.stopped_unanswerable} of {option.unanswerable} |"
            )
        lowest_first = sorted(
            (score, question.id)
            for question in questions
            if (score := default.best_scores.get(question.id)) is not None
        )
        unanswerable = {question.id for question in questions if not question.answerable}
        lines += [
            "",
            "Best scores, lowest first (* = not in the documents): "
            + ", ".join(
                f"{qid}{'*' if qid in unanswerable else ''} {score:.1f}"
                for score, qid in lowest_first
            ),
        ]
    missed = [
        question
        for question in questions
        if question.answerable and (default.ranks.get(question.id) or 99) > TOP_K
    ]
    if missed:
        lines += [
            "",
            f"Questions whose evidence the API's setting did not rank in the first {TOP_K}:",
            "",
        ]
        lines += [
            f"- {question.id} ({question.kind}): {question.question} "
            f"(place: {default.ranks.get(question.id) or 'not in the first 10'})"
            for question in missed
        ]
    if evaluation.answers:
        lines += ["", *_answer_section(evaluation.answers)]
    return "\n".join(lines) + "\n"


def _answer_section(results: Sequence[AnswerResult]) -> list[str]:
    summary = summarize_answers(results)
    lines = [
        "## Answer quality",
        "",
        f"The API's setting ({DEFAULT_CHUNK_SIZE}-token chunks, hybrid search, reranker, "
        f"top {TOP_K} sources), without the answer cache. The judge reads the question, the "
        "reference answer, the sources and the answer.",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Faithfulness: every fact is in the sources (mean of the answers given) "
        f"| {_score(summary.faithfulness)} |",
        f'| Correctness: same facts as the reference ("I don\'t know" counts as 0) '
        f"| {_score(summary.correctness)} |",
        f"| Answered, of the questions the documents answer "
        f"| {summary.answered} of {summary.answerable} |",
        f'| Said "I don\'t know", of the questions the documents do not answer '
        f"| {summary.abstained_unanswerable} of {summary.unanswerable} |",
        f"| LLM answer time p50 / p95 | {_seconds(summary.llm_p50_seconds)} / "
        f"{_seconds(summary.llm_p95_seconds)} |",
        f"| Tokens per LLM answer (in / out) | {_number(summary.tokens_in)} / "
        f"{_number(summary.tokens_out)} |",
    ]
    if summary.judge_failures:
        lines.append(f"| Judge replies that could not be read | {summary.judge_failures} |")
    lines += [
        "",
        LLM_TIME_NOTE,
        "",
        "<details><summary>Every answer</summary>",
        "",
        "| ID | Question | Answer | Faithful | Correct | Judge's reason |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        verdict = result.verdict
        lines.append(
            f"| {result.question.id} | {_cell(result.question.question)} "
            f"| {_cell(result.answer)} "
            f"| {_score(verdict.faithful) if verdict else '-'} "
            f"| {_score(verdict.correct) if verdict else '-'} "
            f"| {_cell(verdict.reason) if verdict else '-'} |"
        )
    lines += ["", "</details>"]
    return lines


def render_json(evaluation: Evaluation, info: RunInfo) -> str:
    data: dict[str, Any] = {
        "run": dataclasses.asdict(info),
        "chunks": evaluation.chunks,
        "search": [
            {
                "setting": dataclasses.asdict(result.setting),
                "scores": dataclasses.asdict(result.scores),
                "gate": dataclasses.asdict(result.gate) if result.gate else None,
                "ranks": result.ranks,
                "best_scores": result.best_scores,
            }
            for result in evaluation.search
        ],
    }
    if evaluation.answers:
        data["answers"] = {
            "summary": dataclasses.asdict(summarize_answers(evaluation.answers)),
            "items": [
                {
                    "id": result.question.id,
                    "answer": result.answer,
                    "abstained": result.abstained,
                    "verdict": dataclasses.asdict(result.verdict) if result.verdict else None,
                    "llm_seconds": result.llm_seconds,
                    "tokens_in": result.usage.prompt_tokens,
                    "tokens_out": result.usage.completion_tokens,
                    "sources": result.sources,
                }
                for result in evaluation.answers
            ],
        }
    return json.dumps(data, indent=2) + "\n"


def _score(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} s"


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}"


def _cell(text: str) -> str:
    """Text for one markdown table cell: one line, no pipes, not too long."""
    flat = " ".join(text.split()).replace("|", "/")
    return flat if len(flat) <= 160 else flat[:157] + "..."
