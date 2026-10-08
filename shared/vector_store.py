"""The vector store (Qdrant): one collection for all tenants.

Every point has `tenant_id` in its payload, and every search and delete filters by it.
One collection with a tenant index scales better than one collection per tenant.
A point's ID is its chunk's ID, so Qdrant and Postgres always agree.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import batched

from qdrant_client import AsyncQdrantClient, models

UPSERT_BATCH_SIZE = 256


@dataclass(frozen=True, slots=True)
class ChunkVector:
    chunk_id: uuid.UUID
    chunk_index: int
    page_number: int | None
    vector: list[float]


async def ensure_collection(client: AsyncQdrantClient, name: str, dimension: int) -> None:
    """Create the chunks collection and its payload indexes if needed. Safe to call again."""
    await create_tenant_collection(client, name, dimension)
    await client.create_payload_index(name, "document_id", models.PayloadSchemaType.KEYWORD)


async def create_tenant_collection(client: AsyncQdrantClient, name: str, dimension: int) -> None:
    """A cosine collection with a `tenant_id` index, if it does not exist yet."""
    if not await client.collection_exists(name):
        await client.create_collection(
            name,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )
    info = await client.get_collection(name)
    vectors = info.config.params.vectors
    if isinstance(vectors, models.VectorParams) and vectors.size != dimension:
        raise RuntimeError(
            f"Qdrant collection {name!r} has vectors of size {vectors.size}, but the "
            f"embedding model makes {dimension}. Use a new collection name for a new model."
        )
    # `is_tenant`: Qdrant keeps each tenant's points together, so filtered searches are fast.
    await client.create_payload_index(
        name,
        "tenant_id",
        field_schema=models.KeywordIndexParams(
            type=models.KeywordIndexType.KEYWORD, is_tenant=True
        ),
    )


def tenant_condition(tenant_id: uuid.UUID) -> models.FieldCondition:
    return models.FieldCondition(key="tenant_id", match=models.MatchValue(value=str(tenant_id)))


async def save_document_vectors(
    client: AsyncQdrantClient,
    collection: str,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    chunks: Sequence[ChunkVector],
) -> None:
    """Write a document's vectors, replacing what an earlier run of the same job wrote."""
    for batch in batched(chunks, UPSERT_BATCH_SIZE):
        await client.upsert(
            collection,
            points=[_point(tenant_id, document_id, chunk) for chunk in batch],
            wait=True,
        )
    # A repeated job writes the same point IDs, so only extra old points need deleting.
    leftovers = _document_filter(
        tenant_id,
        document_id,
        models.FieldCondition(key="chunk_index", range=models.Range(gte=len(chunks))),
    )
    await client.delete(
        collection, points_selector=models.FilterSelector(filter=leftovers), wait=True
    )


async def delete_document_vectors(
    client: AsyncQdrantClient, collection: str, tenant_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    selector = models.FilterSelector(filter=_document_filter(tenant_id, document_id))
    await client.delete(collection, points_selector=selector, wait=True)


async def count_document_vectors(
    client: AsyncQdrantClient, collection: str, tenant_id: uuid.UUID, document_id: uuid.UUID
) -> int:
    result = await client.count(
        collection, count_filter=_document_filter(tenant_id, document_id), exact=True
    )
    return result.count


async def search_chunks(
    client: AsyncQdrantClient,
    collection: str,
    tenant_id: uuid.UUID,
    vector: list[float],
    limit: int,
) -> list[uuid.UUID]:
    """IDs of the tenant's chunks closest in meaning to `vector`, best first."""
    tenant_only = models.Filter(
        must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=str(tenant_id)))]
    )
    result = await client.query_points(
        collection, query=vector, query_filter=tenant_only, limit=limit, with_payload=False
    )
    return [uuid.UUID(str(point.id)) for point in result.points]


def _point(tenant_id: uuid.UUID, document_id: uuid.UUID, chunk: ChunkVector) -> models.PointStruct:
    return models.PointStruct(
        id=str(chunk.chunk_id),
        vector=chunk.vector,
        payload={
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "chunk_id": str(chunk.chunk_id),
            "chunk_index": chunk.chunk_index,
            "page": chunk.page_number,
        },
    )


def _document_filter(
    tenant_id: uuid.UUID, document_id: uuid.UUID, *more: models.FieldCondition
) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=str(tenant_id))),
            models.FieldCondition(
                key="document_id", match=models.MatchValue(value=str(document_id))
            ),
            *more,
        ]
    )
