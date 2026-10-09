"""The evaluation's steps, on the stores and models the caller gives (eval/run.py starts
throw-away containers and loads the real models; the tests pass their own and fake models).

1. Index the sample documents once per chunk size, each size in its own tenant. This
   skips the worker's size check on purpose: 1000-token chunks are longer than the
   embedder reads (512 tokens), and the evaluation shows what that costs.
2. Search quality per setting: the place of the first chunk with each question's evidence.
3. Answer quality with the default setting: the same steps as POST /v1/chat (without the
   cache), then an LLM judge.
"""

import asyncio
import hashlib
import logging
import statistics
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from api.ai import AIServices
from api.chat.prompts import NO_ANSWER, answer_messages, is_no_answer, normalize_citations
from api.chat.retrieval import SearchMethod, find_sources, rerank, search_candidates
from eval.data import CorpusFile, Question
from eval.scoring import (
    SearchScores,
    Verdict,
    first_relevant_rank,
    judge_messages,
    parse_verdict,
    search_scores,
)
from shared.clients import Clients
from shared.config import Settings
from shared.db.models import Chunk, Document, DocumentStatus, Tenant
from shared.file_types import FileType
from shared.llm import LLM, ChatMessage, LLMBusyError, LLMError, Usage
from shared.vector_store import ChunkVector, save_document_vectors
from worker.chunking import chunk_document, chunk_id

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 500
TOP_K = 5  # sources per answer: the API's default
SEARCH_DEPTH = 10  # places looked at for MRR@10
LLM_ATTEMPTS = 10  # Groq's free tier often says "too many tokens per minute": wait, try again
MAX_WAIT_SECONDS = 90.0
ERROR_WAIT_SECONDS = 10.0  # after a timeout or a server error


@dataclass(frozen=True, slots=True)
class SearchSetting:
    name: str
    chunk_size: int = DEFAULT_CHUNK_SIZE
    methods: tuple[SearchMethod, ...] = ("vector", "keyword")
    use_reranker: bool = True


# The settings compared. The first one is what the API does.
SEARCH_SETTINGS = (
    SearchSetting("Hybrid search + reranker (the API's setting)"),
    SearchSetting("Hybrid search, no reranker", use_reranker=False),
    SearchSetting("Vector search only + reranker", methods=("vector",)),
    SearchSetting("Keyword search only + reranker", methods=("keyword",)),
    SearchSetting("Vector search only, no reranker", methods=("vector",), use_reranker=False),
    SearchSetting("Chunks of 300 tokens (hybrid + reranker)", chunk_size=300),
    SearchSetting("Chunks of 1000 tokens (hybrid + reranker)", chunk_size=1000),
)


@dataclass(frozen=True, slots=True)
class GateScores:
    """The "I don't know" gate: no chunk scores at least MIN_RERANK_SCORE, so no LLM call."""

    stopped_unanswerable: int  # right: the documents do not have the answer
    unanswerable: int
    stopped_answerable: int  # wrong: the documents have the answer
    answerable: int


@dataclass(frozen=True, slots=True)
class SearchResult:
    setting: SearchSetting
    scores: SearchScores
    ranks: dict[str, int | None]  # question ID -> place of the first chunk with the evidence
    best_scores: dict[str, float | None]  # question ID -> the reranker's best score (or None)
    gate: GateScores | None  # only with the reranker (its score decides)


@dataclass(frozen=True, slots=True)
class AnswerResult:
    question: Question
    answer: str
    abstained: bool  # "I don't know based on the documents."
    verdict: Verdict | None  # None: abstained, no answer expected, or the judge failed
    judge_failed: bool
    llm_seconds: float | None  # None: no LLM call (no relevant sources)
    usage: Usage
    sources: int
    followed_injection: bool | None = None  # None: no instructions planted for it


@dataclass(frozen=True, slots=True)
class Evaluation:
    chunks: dict[int, int]  # chunk size -> chunks made from the documents
    search: list[SearchResult]
    answers: list[AnswerResult]  # empty: answers were not evaluated


async def run_evaluation(
    questions: Sequence[Question],
    corpus: Sequence[CorpusFile],
    *,
    settings: Settings,
    clients: Clients,
    ai: AIServices,
    judge: LLM | None,
    search_settings: Sequence[SearchSetting] = SEARCH_SETTINGS,
) -> Evaluation:
    """Everything. `judge=None`: search quality only (no LLM calls)."""
    sizes = sorted({setting.chunk_size for setting in search_settings} | {DEFAULT_CHUNK_SIZE})
    tenants: dict[int, uuid.UUID] = {}
    chunks: dict[int, int] = {}
    for size in sizes:
        tenants[size], chunks[size] = await index_corpus(
            corpus, size, settings=settings, clients=clients, ai=ai
        )
        logger.info("Indexed the documents in chunks of %d tokens: %d chunks", size, chunks[size])
    vectors = {
        question.id: await asyncio.to_thread(ai.embedder.embed_query, question.question)
        for question in questions
    }
    search = []
    for setting in search_settings:
        result = await evaluate_search(
            questions, setting, tenants[setting.chunk_size], vectors, settings, clients, ai
        )
        logger.info(
            "%s: hit@5 %.2f, MRR %.2f", setting.name, result.scores.hit_at_5, result.scores.mrr
        )
        search.append(result)
    answers = (
        await evaluate_answers(
            questions, tenants[DEFAULT_CHUNK_SIZE], vectors, settings, clients, ai, judge
        )
        if judge is not None
        else []
    )
    return Evaluation(chunks=chunks, search=search, answers=answers)


async def index_corpus(
    corpus: Sequence[CorpusFile],
    chunk_size: int,
    *,
    settings: Settings,
    clients: Clients,
    ai: AIServices,
) -> tuple[uuid.UUID, int]:
    """A new tenant with the documents, ready to search. Returns it and the chunk count."""
    async with clients.sessions() as session, session.begin():
        tenant = Tenant(name=f"Evaluation, chunks of {chunk_size} tokens")
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
    total = 0
    for file in corpus:
        total += await _index_file(file, tenant_id, chunk_size, settings, clients, ai)
    return tenant_id, total


async def _index_file(
    file: CorpusFile,
    tenant_id: uuid.UUID,
    chunk_size: int,
    settings: Settings,
    clients: Clients,
    ai: AIServices,
) -> int:
    pages = await asyncio.to_thread(file.pages)
    chunks = await asyncio.to_thread(
        chunk_document, pages, ai.embedder.count_tokens, chunk_size, chunk_size // 10
    )
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), settings.embedding_batch_size):
        texts = [chunk.text for chunk in chunks[start : start + settings.embedding_batch_size]]
        vectors.extend(await asyncio.to_thread(ai.embedder.embed_documents, texts))
    document_id = uuid.uuid4()
    async with clients.sessions() as session, session.begin():
        session.add(
            Document(
                id=document_id,
                tenant_id=tenant_id,
                filename=file.filename,
                mime_type=file.file_type.value,
                size_bytes=len(file.data),
                sha256=hashlib.sha256(file.data).hexdigest(),
                storage_key=f"eval/{file.filename}",
                status=DocumentStatus.READY,
                chunk_count=len(chunks),
                page_count=len(pages) if file.file_type is FileType.PDF else None,
            )
        )
        await session.flush()
        session.add_all(
            Chunk(
                id=chunk_id(document_id, chunk.index),
                tenant_id=tenant_id,
                document_id=document_id,
                chunk_index=chunk.index,
                text=chunk.text,
                page_number=chunk.page_number,
                token_count=chunk.token_count,
            )
            for chunk in chunks
        )
    await save_document_vectors(
        clients.qdrant,
        settings.qdrant_collection,
        tenant_id,
        document_id,
        [
            ChunkVector(
                chunk_id=chunk_id(document_id, chunk.index),
                chunk_index=chunk.index,
                page_number=chunk.page_number,
                vector=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ],
    )
    return len(chunks)


async def evaluate_search(
    questions: Sequence[Question],
    setting: SearchSetting,
    tenant_id: uuid.UUID,
    vectors: dict[str, list[float]],
    settings: Settings,
    clients: Clients,
    ai: AIServices,
) -> SearchResult:
    ranks: dict[str, int | None] = {}
    best: dict[str, float | None] = {}
    for question in questions:
        ranked = await search_candidates(
            question.question,
            vectors[question.id],
            tenant_id,
            settings=settings,
            clients=clients,
            methods=setting.methods,
        )
        if setting.use_reranker and ranked:  # like the API: only the best candidates
            ranked = await rerank(question.question, ranked[: settings.rerank_candidates], ai)
        if question.answerable:
            ranks[question.id] = first_relevant_rank(ranked[:SEARCH_DEPTH], question)
        best[question.id] = ranked[0].score if setting.use_reranker and ranked else None
    gate = gate_at(questions, best, settings.min_rerank_score) if setting.use_reranker else None
    return SearchResult(setting, search_scores(list(ranks.values())), ranks, best, gate)


def gate_at(
    questions: Sequence[Question], best_scores: Mapping[str, float | None], threshold: float
) -> GateScores:
    """What the "I don't know" gate does with this MIN_RERANK_SCORE: a question stops
    (no LLM call) when no chunk scores at least `threshold`."""

    def stops(question: Question) -> bool:
        score = best_scores.get(question.id)
        return score is None or score < threshold

    answerable = [q for q in questions if q.answerable]
    unanswerable = [q for q in questions if not q.answerable]
    return GateScores(
        stopped_unanswerable=sum(stops(q) for q in unanswerable),
        unanswerable=len(unanswerable),
        stopped_answerable=sum(stops(q) for q in answerable),
        answerable=len(answerable),
    )


async def evaluate_answers(
    questions: Sequence[Question],
    tenant_id: uuid.UUID,
    vectors: dict[str, list[float]],
    settings: Settings,
    clients: Clients,
    ai: AIServices,
    judge: LLM,
) -> list[AnswerResult]:
    """Answer like POST /v1/chat (without the cache), then let the judge score the answers."""
    results = []
    for number, question in enumerate(questions, start=1):
        sources = await find_sources(
            question.question,
            vectors[question.id],
            tenant_id,
            TOP_K,
            settings=settings,
            clients=clients,
            ai=ai,
        )
        usage = Usage()
        seconds: float | None = None
        answer = NO_ANSWER
        if sources:
            reply, seconds = await ask_with_retries(
                ai.llm, answer_messages(question.question, sources), usage
            )
            answer = normalize_citations(reply.strip()) or NO_ANSWER  # as the API saves it
        abstained = is_no_answer(answer)
        verdict: Verdict | None = None
        judge_failed = False
        if question.answerable and not abstained:
            reply, _ = await ask_with_retries(
                judge, judge_messages(question, sources, answer), Usage()
            )
            verdict = parse_verdict(reply)
            judge_failed = verdict is None
        results.append(
            AnswerResult(
                question=question,
                answer=answer,
                abstained=abstained,
                verdict=verdict,
                judge_failed=judge_failed,
                llm_seconds=seconds,
                usage=usage,
                sources=len(sources),
                followed_injection=followed_injection(question, answer),
            )
        )
        logger.info("Answered %d of %d (%s)", number, len(questions), question.id)
    return results


def followed_injection(question: Question, answer: str) -> bool | None:
    """Did the answer follow instructions planted in its document? None: none were planted."""
    return question.forbidden.lower() in answer.lower() if question.forbidden else None


async def ask_with_retries(
    llm: LLM, messages: list[ChatMessage], usage: Usage
) -> tuple[str, float]:
    """One LLM answer and how long the successful call took. On "busy" (HTTP 429: the free
    tier's tokens per minute are used up), wait as long as the LLM asks; on another failure
    (a timeout, a server error), wait a little. Then try again: one slow reply must not end
    a 10-minute run."""
    for _ in range(LLM_ATTEMPTS):
        started = time.perf_counter()
        try:
            text = await llm.complete(messages, usage)
        except LLMBusyError as busy:
            wait = min(MAX_WAIT_SECONDS, max(5.0, busy.retry_after_seconds or 20.0))
            logger.info("The LLM is busy; waiting %.0f s", wait)
            await asyncio.sleep(wait)
            continue
        except LLMError as error:
            logger.warning("The LLM failed (%s); trying again in %.0f s", error, ERROR_WAIT_SECONDS)
            await asyncio.sleep(ERROR_WAIT_SECONDS)
            continue
        return text, time.perf_counter() - started
    raise RuntimeError(f"The LLM still failed after {LLM_ATTEMPTS} tries.")


@dataclass(frozen=True, slots=True)
class AnswerSummary:
    answerable: int
    answered: int  # did not say "I don't know"
    faithfulness: float | None  # mean judge score of the answers given
    correctness: float | None  # mean judge score of all answerable questions (abstain = 0)
    unanswerable: int
    abstained_unanswerable: int  # said "I don't know", as it should
    judge_failures: int
    llm_p50_seconds: float | None
    llm_p95_seconds: float | None
    tokens_in: float | None  # mean per LLM answer
    tokens_out: float | None
    sources: float | None  # mean sources per LLM answer
    injection_questions: int  # their document has planted instructions
    injection_followed: int  # answers that followed them


def summarize_answers(results: Sequence[AnswerResult]) -> AnswerSummary:
    answerable = [r for r in results if r.question.answerable]
    unanswerable = [r for r in results if not r.question.answerable]
    judged = [r.verdict for r in answerable if r.verdict is not None]
    correctness: list[float] = []  # judge failures are left out
    for result in answerable:
        if result.abstained:
            correctness.append(0.0)
        elif result.verdict is not None:
            correctness.append(result.verdict.correct)
    timed = sorted(r.llm_seconds for r in results if r.llm_seconds is not None)
    calls = [r for r in results if r.llm_seconds is not None]
    return AnswerSummary(
        answerable=len(answerable),
        answered=sum(not r.abstained for r in answerable),
        faithfulness=statistics.fmean(v.faithful for v in judged) if judged else None,
        correctness=statistics.fmean(correctness) if correctness else None,
        unanswerable=len(unanswerable),
        abstained_unanswerable=sum(r.abstained for r in unanswerable),
        judge_failures=sum(r.judge_failed for r in results),
        llm_p50_seconds=_percentile(timed, 0.5),
        llm_p95_seconds=_percentile(timed, 0.95),
        tokens_in=statistics.fmean(r.usage.prompt_tokens for r in calls) if calls else None,
        tokens_out=statistics.fmean(r.usage.completion_tokens for r in calls) if calls else None,
        sources=statistics.fmean(r.sources for r in calls) if calls else None,
        injection_questions=sum(r.followed_injection is not None for r in results),
        injection_followed=sum(r.followed_injection is True for r in results),
    )


def _percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between the closest ranks (like Postgres percentile_cont)."""
    if not values:
        return None
    position = q * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)
