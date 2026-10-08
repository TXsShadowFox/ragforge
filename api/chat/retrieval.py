"""Find the chunks that answer a question: two searches, fusion, then reranking.

1. Two searches, both only in the caller's tenant:
   - by meaning: Qdrant compares the question's vector with the chunks' vectors
   - by words: Postgres full-text search (good for names, codes and numbers)
2. Merge the two ranked lists with Reciprocal Rank Fusion (RRF).
3. Keep chunks of "ready" documents, rerank them, and drop the ones that are not relevant.
"""

import asyncio
import dataclasses
import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Text, cast, func, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from api.ai import AIServices
from shared.clients import Clients
from shared.config import Settings
from shared.db.models import Chunk, Document, DocumentStatus
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
    limit = settings.search_candidates
    async with clients.sessions() as session:
        by_meaning, by_words = await asyncio.gather(
            search_chunks(clients.qdrant, settings.qdrant_collection, tenant_id, vector, limit),
            keyword_search(session, tenant_id, query, limit),
        )
        candidates = reciprocal_rank_fusion([by_meaning, by_words])[:limit]
        sources = await load_ready_chunks(session, tenant_id, candidates)
    if not sources:
        logger.info("Search found no chunks")
        return []
    # The database connection is back in the pool before the slow reranking starts.
    scores = await asyncio.to_thread(ai.reranker.rerank, query, [s.text for s in sources])
    ranked = sorted(zip(sources, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    relevant = [
        dataclasses.replace(source, score=score)
        for source, score in ranked[:keep]
        if score >= settings.min_rerank_score
    ]
    logger.info(
        "Search found %d relevant chunks",
        len(relevant),
        extra={"candidates": len(sources), "best_score": round(ranked[0][1], 2)},
    )
    return relevant


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
