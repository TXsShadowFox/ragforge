"""Find the chunks that answer a question: two searches, fusion, then reranking.

1. Two searches, both only in the caller's tenant:
   - by meaning: Qdrant compares the question's vector with the chunks' vectors
   - by words: Postgres full-text search (good for names, codes and numbers)
2. Merge the two ranked lists with Reciprocal Rank Fusion (RRF).
3. Keep chunks of "ready" documents, rerank the best RERANK_CANDIDATES of them, and keep
   the relevant ones (pick_sources). The reranker is the slowest step, so only
   RERANK_CONCURRENCY runs happen at the same time; other questions wait their turn.
"""

import asyncio
import dataclasses
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Collection, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Text, cast, func, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from api.ai import AIServices
from shared import metrics
from shared.clients import Clients
from shared.config import Settings
from shared.db.models import Chunk, Document, DocumentStatus
from shared.tracing import span
from shared.vector_store import search_chunks

logger = logging.getLogger(__name__)

# The usual RRF constant: it keeps the first places from counting too much.
RRF_K = 60


@dataclass(frozen=True, slots=True)
class Source:
    """A chunk the LLM may use to answer."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page_number: int | None
    text: str
    score: float = 0.0  # the reranker's score: higher is more relevant


def reciprocal_rank_fusion(rankings: Sequence[Sequence[uuid.UUID]]) -> list[uuid.UUID]:
    """Merge ranked lists: each list gives an item 1 / (RRF_K + its place). Best first.

    An item that both searches found ranks above one that only one search found.
    """
    scores: dict[uuid.UUID, float] = defaultdict(float)
    for ranking in rankings:
        for place, item in enumerate(ranking, start=1):
            scores[item] += 1 / (RRF_K + place)
    return sorted(scores, key=lambda item: scores[item], reverse=True)


SearchMethod = Literal["vector", "keyword"]


async def find_sources(
    query: str,
    vector: list[float],
    tenant_id: uuid.UUID,
    keep: int,
    *,
    settings: Settings,
    clients: Clients,
    ai: AIServices,
) -> list[Source]:
    """The `keep` most relevant chunks for `query` (whose vector is `vector`), best first.

    Empty: nothing relevant.
    """
    with span("retrieval") as current:
        candidates = await search_candidates(
            query, vector, tenant_id, settings=settings, clients=clients
        )
        if not candidates:
            logger.info("Search found no chunks")
            return []
        # The database connection is back in the pool before the slow reranking starts.
        ranked = await rerank(query, candidates[: settings.rerank_candidates], ai)
        relevant = pick_sources(
            ranked,
            keep,
            min_score=settings.min_rerank_score,
            margin=settings.source_score_margin,
        )
        current.set_attribute("retrieval.candidates", len(candidates))
        current.set_attribute("retrieval.relevant", len(relevant))
    logger.info(
        "Search found %d relevant chunks",
        len(relevant),
        extra={"candidates": len(candidates), "best_score": round(ranked[0].score, 2)},
    )
    return relevant


def pick_sources(
    ranked: Sequence[Source], keep: int, *, min_score: float, margin: float
) -> list[Source]:
    """The sources for the LLM, from the reranked chunks (best first): at most `keep`,
    each scoring at least `min_score` (MIN_RERANK_SCORE) and at most `margin` below the
    best one (SOURCE_SCORE_MARGIN). Empty: nothing is relevant ("I don't know").

    The margin keeps weak chunks out of the prompt (fewer tokens): in the evaluation the
    chunk with the answer always scored within 0.1 of the best one.
    """
    if not ranked:
        return []
    floor = max(min_score, ranked[0].score - margin)
    return [source for source in ranked[:keep] if source.score >= floor]


async def search_candidates(
    query: str,
    vector: list[float],
    tenant_id: uuid.UUID,
    *,
    settings: Settings,
    clients: Clients,
    methods: Collection[SearchMethod] = ("vector", "keyword"),
) -> list[Source]:
    """Steps 1 and 2: the searches, merged with RRF. Chunks of ready documents, best first.

    `methods` lets the evaluation (eval/) compare one search with both.
    """
    limit = settings.search_candidates
    async with clients.sessions() as session:
        searches: list[Awaitable[list[uuid.UUID]]] = []
        if "vector" in methods:
            searches.append(
                _measured(
                    "vector_search",
                    search_chunks(
                        clients.qdrant, settings.qdrant_collection, tenant_id, vector, limit
                    ),
                )
            )
        if "keyword" in methods:
            searches.append(
                _measured("keyword_search", keyword_search(session, tenant_id, query, limit))
            )
        rankings = await asyncio.gather(*searches)
        candidates = reciprocal_rank_fusion(rankings)[:limit]
        return await _measured("load_chunks", load_ready_chunks(session, tenant_id, candidates))


async def rerank(query: str, sources: Sequence[Source], ai: AIServices) -> list[Source]:
    """Step 3: the chunks sorted by the reranker's score (best first), with the score."""
    texts = [source.text for source in sources]
    with (
        span("retrieval.rerank_wait"),
        metrics.timer(metrics.RETRIEVAL_DURATION, step="rerank_wait"),
    ):
        await ai.rerank_slots.acquire()
    with (
        span("retrieval.rerank", chunks=len(sources)),
        metrics.timer(metrics.RETRIEVAL_DURATION, step="rerank"),
    ):
        scores = await _run_in_slot(ai, query, texts)
    ranked = sorted(zip(sources, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    return [dataclasses.replace(source, score=score) for source, score in ranked]


async def _run_in_slot(ai: AIServices, query: str, texts: list[str]) -> list[float]:
    """Run the reranker in a thread; give the (already taken) slot back when the thread is
    done. A thread cannot be stopped: if the request is cancelled, the run still finishes,
    and until then it keeps its slot, so the limit always holds."""
    run = asyncio.ensure_future(asyncio.to_thread(ai.reranker.rerank, query, texts))
    run.add_done_callback(lambda _: ai.rerank_slots.release())
    return await asyncio.shield(run)


async def _measured[T](step: str, work: Awaitable[T]) -> T:
    """One search step as a span, timed in the metrics."""
    with (
        span(f"retrieval.{step}"),
        metrics.timer(metrics.RETRIEVAL_DURATION, step=step),
    ):
        return await work


async def keyword_search(
    session: AsyncSession, tenant_id: uuid.UUID, query: str, limit: int
) -> list[uuid.UUID]:
    """IDs of the tenant's chunks of ready documents that contain words of `query`, best first.

    Words are joined with OR ("attendance | rule | exam"): with AND (the default of
    `plainto_tsquery`), a chunk would need every word of the question. Chunks with more
    of the words rank higher (`ts_rank_cd`).
    """
    any_word = cast(
        func.replace(cast(func.plainto_tsquery("english", query), Text), "&", "|"), TSQUERY
    )
    ids = await session.scalars(
        select(Chunk.id)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.tenant_id == tenant_id,
            Document.tenant_id == tenant_id,
            Document.status == DocumentStatus.READY,
            Chunk.tsv.bool_op("@@")(any_word),
        )
        .order_by(func.ts_rank_cd(Chunk.tsv, any_word).desc())
        .limit(limit)
    )
    return list(ids)


async def load_ready_chunks(
    session: AsyncSession, tenant_id: uuid.UUID, chunk_ids: Sequence[uuid.UUID]
) -> list[Source]:
    """The chunks, in the given order, without those of other tenants or of documents
    that are not ready (being processed, failed, or being deleted)."""
    if not chunk_ids:
        return []
    rows = await session.execute(
        select(Chunk.id, Chunk.document_id, Chunk.text, Chunk.page_number, Document.filename)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.id.in_(chunk_ids),
            Chunk.tenant_id == tenant_id,
            Document.tenant_id == tenant_id,
            Document.status == DocumentStatus.READY,
        )
    )
    by_id = {
        row.id: Source(
            chunk_id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            page_number=row.page_number,
            text=row.text,
        )
        for row in rows
    }
    return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]
