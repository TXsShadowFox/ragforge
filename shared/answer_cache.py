"""The answer cache: skip the search and the LLM for a question that was answered before.

- Exact (Redis): the same question, maybe written a bit differently (upper/lower case,
  spaces, a final "?"), gets the stored answer. Entries expire after CACHE_TTL_SECONDS.
- Semantic (Qdrant): a question that means the same (vectors at least
  SEMANTIC_CACHE_THRESHOLD similar) gets the stored answer.

Answers are stored with the tenant's `docs_version`. When the documents change, the version
goes up, and answers made with older documents never match again.
A cache problem never breaks a chat: errors are logged and count as "not in the cache".
"""

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from qdrant_client import AsyncQdrantClient, models
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import Settings
from shared.db.models import Tenant
from shared.vector_store import create_tenant_collection, tenant_condition

logger = logging.getLogger(__name__)

# Fixed namespace for the cache's point IDs (uuid5 needs one).
_POINT_NAMESPACE = uuid.UUID("5f0c1a9e-3a5e-4c6f-9d0e-7b2a4c8e1f30")


def normalize_question(question: str) -> str:
    """'  What is the FEE?? ' -> 'what is the fee': same meaning, same cache key."""
    return " ".join(question.lower().split()).rstrip(" ?!.")


def answer_setup(settings: Settings, top_k: int, llm_model: str) -> str:
    """A cache key's `setup`: what changes an answer besides the documents and the question.
    After a change (like a new MIN_RERANK_SCORE), old answers are not reused."""
    return (
        f"top{top_k}:{llm_model}:{settings.rerank_model}:{settings.rerank_candidates}"
        f":{settings.min_rerank_score:g}:{settings.source_score_margin:g}"
    )


@dataclass(frozen=True, slots=True)
class CacheKey:
    tenant_id: uuid.UUID
    docs_version: int
    question: str  # already normalized
    setup: str  # anything else that changes the answer, like top_k and the LLM model

    @property
    def redis_key(self) -> str:
        digest = hashlib.sha256(f"{self.setup}\n{self.question}".encode()).hexdigest()
        return f"answer:{self.tenant_id}:{self.docs_version}:{digest}"

    @property
    def point_id(self) -> uuid.UUID:
        """The same question always gets the same point: storing it again replaces it."""
        name = f"{self.tenant_id}:{self.docs_version}:{self.setup}:{self.question}"
        return uuid.uuid5(_POINT_NAMESPACE, name)


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    answer: str
    citations: list[dict[str, Any]]
    kind: Literal["exact", "semantic"]


class AnswerCache:
    def __init__(self, redis: Redis, qdrant: AsyncQdrantClient, settings: Settings) -> None:
        self._redis = redis
        self._qdrant = qdrant
        self._collection = settings.answer_cache_collection
        self._ttl_seconds = settings.cache_ttl_seconds
        self._threshold = settings.semantic_cache_threshold

    async def get(self, key: CacheKey, vector: list[float]) -> CachedAnswer | None:
        """The stored answer for this question (exact first, then semantic), or None."""
        try:
            stored = await self._redis.get(key.redis_key)
        except Exception:
            logger.warning("The exact answer cache failed; skipping it", exc_info=True)
            stored = None
        if stored is not None:
            data = json.loads(stored)
            return CachedAnswer(data["answer"], data["citations"], kind="exact")
        return await self._get_semantic(key, vector)

    async def put(
        self, key: CacheKey, vector: list[float], answer: str, citations: list[dict[str, Any]]
    ) -> None:
        """Store an answer in both caches. Failures are only logged."""
        data = {"answer": answer, "citations": citations}
        try:
            await self._redis.set(key.redis_key, json.dumps(data), ex=self._ttl_seconds)
        except Exception:
            logger.warning("Could not store an answer in the exact cache", exc_info=True)
        payload = {
            **data,
            "tenant_id": str(key.tenant_id),
            "docs_version": key.docs_version,
            "setup": key.setup,
            "question": key.question,
            "expires_at": time.time() + self._ttl_seconds,
        }
        try:
            await self._qdrant.upsert(
                self._collection,
                points=[models.PointStruct(id=str(key.point_id), vector=vector, payload=payload)],
            )
        except Exception:
            logger.warning("Could not store an answer in the semantic cache", exc_info=True)

    async def _get_semantic(self, key: CacheKey, vector: list[float]) -> CachedAnswer | None:
        same_setup = models.Filter(
            must=[
                tenant_condition(key.tenant_id),
                models.FieldCondition(
                    key="docs_version", match=models.MatchValue(value=key.docs_version)
                ),
                models.FieldCondition(key="setup", match=models.MatchValue(value=key.setup)),
                models.FieldCondition(key="expires_at", range=models.Range(gt=time.time())),
            ]
        )
        try:
            result = await self._qdrant.query_points(
                self._collection,
                query=vector,
                query_filter=same_setup,
                limit=1,
                score_threshold=self._threshold,
                with_payload=True,
            )
        except Exception:
            logger.warning("The semantic answer cache failed; skipping it", exc_info=True)
            return None
        if not result.points or result.points[0].payload is None:
            return None
        payload = result.points[0].payload
        return CachedAnswer(payload["answer"], payload["citations"], kind="semantic")


async def ensure_answer_collection(client: AsyncQdrantClient, name: str, dimension: int) -> None:
    """Create the semantic cache's collection and payload indexes. Safe to call again."""
    await create_tenant_collection(client, name, dimension)
    await client.create_payload_index(name, "docs_version", models.PayloadSchemaType.INTEGER)
    await client.create_payload_index(name, "setup", models.PayloadSchemaType.KEYWORD)
    await client.create_payload_index(name, "expires_at", models.PayloadSchemaType.FLOAT)


async def bump_docs_version(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """The tenant's searchable documents changed: add 1 to its `docs_version` (in the
    caller's transaction) and return the new version. Older cached answers never match again."""
    new_version = await session.scalar(
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(docs_version=Tenant.docs_version + 1)
        .returning(Tenant.docs_version)
    )
    return int(new_version or 0)


async def forget_old_answers(
    client: AsyncQdrantClient, collection: str, tenant_id: uuid.UUID, docs_version: int
) -> None:
    """Delete the tenant's cached answers made with older documents (they can never match
    again). Best effort: the Redis entries simply expire."""
    older = models.Filter(
        must=[
            tenant_condition(tenant_id),
            models.FieldCondition(key="docs_version", range=models.Range(lt=docs_version)),
        ]
    )
    try:
        await client.delete(collection, points_selector=models.FilterSelector(filter=older))
    except Exception:
        logger.warning("Could not delete old cached answers", exc_info=True)


async def forget_expired_answers(client: AsyncQdrantClient, collection: str) -> None:
    """Delete expired cached answers of all tenants (Qdrant has no automatic expiry)."""
    expired = models.Filter(
        must=[models.FieldCondition(key="expires_at", range=models.Range(lt=time.time()))]
    )
    await client.delete(collection, points_selector=models.FilterSelector(filter=expired))
