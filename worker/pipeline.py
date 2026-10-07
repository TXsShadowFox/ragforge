"""The two document jobs.

ingest: download -> read the text -> clean -> chunk -> embed -> Qdrant + Postgres -> ready
delete: Qdrant points -> stored file -> Postgres rows (chunks go with the document)

Both are safe to run twice: RabbitMQ and the outbox may deliver a job more than once.
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, delete, select, update

from shared.clients import Clients
from shared.clients.storage import delete_file, download_file
from shared.config import Settings
from shared.db.models import Chunk, Document, DocumentStatus, JobType
from shared.embeddings import Embedder
from shared.file_types import FileType
from shared.jobs import Job
from shared.vector_store import ChunkVector, delete_document_vectors, save_document_vectors
from worker.chunking import TextChunk, chunk_document, chunk_id
from worker.cleaning import clean_text
from worker.parsing import BadDocumentError, Page, parse_document

logger = logging.getLogger(__name__)

NO_TEXT_ERROR = (
    "No text was found in this file. Scanned pages are pictures of text, "
    "and we cannot read those yet."
)
# An ingest job may (re)start from these states. "processing": an earlier try stopped
# half-way (e.g. the worker crashed). "failed": a repeated message for a failed document.
_CAN_START = (DocumentStatus.UPLOADED, DocumentStatus.PROCESSING, DocumentStatus.FAILED)


@dataclass(frozen=True, slots=True)
class JobContext:
    """Everything a job needs."""

    settings: Settings
    clients: Clients
    embedder: Embedder


async def run_job(context: JobContext, job: Job) -> None:
    if job.type is JobType.INGEST:
        await ingest_document(context, job)
    else:
        await delete_document(context, job)


async def ingest_document(context: JobContext, job: Job) -> None:
    settings, clients = context.settings, context.clients
    document = await _start_processing(context, job)
    if document is None:
        logger.info("Nothing to do: the document is gone, being deleted, or already ready")
        return

    file_type = FileType(document.mime_type)
    data = await download_file(clients.s3, settings.s3_bucket, document.storage_key)
    pages = await asyncio.to_thread(_read_pages, data, file_type)
    chunks = await asyncio.to_thread(
        chunk_document,
        pages,
        context.embedder.count_tokens,
        settings.chunk_size_tokens,
        settings.chunk_overlap_tokens,
    )
    if not chunks:
        raise BadDocumentError(NO_TEXT_ERROR)
    vectors = await _embed(context, job.document_id, chunks)

    await save_document_vectors(
        clients.qdrant, settings.qdrant_collection, job.tenant_id, job.document_id, vectors
    )
    page_count = len(pages) if file_type is FileType.PDF else None
    if not await _save_chunks(context, job, chunks, page_count):
        # The document was deleted while we worked: remove the vectors we just wrote.
        await delete_document_vectors(
            clients.qdrant, settings.qdrant_collection, job.tenant_id, job.document_id
        )
        logger.info("The document was deleted while it was processed")
        return
    logger.info("Document is ready", extra={"chunk_count": len(chunks), "page_count": page_count})


async def delete_document(context: JobContext, job: Job) -> None:
    settings, clients = context.settings, context.clients
    async with clients.sessions() as session:
        document = await session.scalar(_owned_document(job))
    if document is None or document.status is not DocumentStatus.DELETING:
        logger.info("Nothing to delete: the document is already gone")
        return
    await delete_document_vectors(
        clients.qdrant, settings.qdrant_collection, job.tenant_id, job.document_id
    )
    await delete_file(clients.s3, settings.s3_bucket, document.storage_key)
    async with clients.sessions() as session, session.begin():
        # Its chunks are deleted with it (ON DELETE CASCADE).
        await session.execute(
            delete(Document).where(
                Document.id == job.document_id, Document.tenant_id == job.tenant_id
            )
        )
    logger.info("Document deleted")


async def mark_failed(context: JobContext, job: Job, error: str) -> None:
    """Save why a job failed for good. Users see `error`, so keep internal details out."""
    # A failed ingest must not undo a delete that started in the meantime.
    allowed = (
        Document.status == DocumentStatus.DELETING
        if job.type is JobType.DELETE
        else Document.status != DocumentStatus.DELETING
    )
    async with context.clients.sessions() as session, session.begin():
        await session.execute(
            update(Document)
            .where(Document.id == job.document_id, Document.tenant_id == job.tenant_id, allowed)
            .values(status=DocumentStatus.FAILED, error=error)
        )


def _owned_document(job: Job) -> Select[Document]:
    return select(Document).where(
        Document.id == job.document_id, Document.tenant_id == job.tenant_id
    )


async def _start_processing(context: JobContext, job: Job) -> Document | None:
    """Set the status to "processing". None if the document should not be processed."""
    async with context.clients.sessions() as session, session.begin():
        document: Document | None = await session.scalar(
            update(Document)
            .where(
                Document.id == job.document_id,
                Document.tenant_id == job.tenant_id,
                Document.status.in_(_CAN_START),
            )
            .values(status=DocumentStatus.PROCESSING, error=None)
            .returning(Document)
        )
    return document


def _read_pages(data: bytes, file_type: FileType) -> list[Page]:
    """Read and clean the text. Uses the CPU for a while: runs in a thread."""
    return [Page(page.number, clean_text(page.text)) for page in parse_document(data, file_type)]


async def _embed(
    context: JobContext, document_id: uuid.UUID, chunks: Sequence[TextChunk]
) -> list[ChunkVector]:
    batch_size = context.settings.embedding_batch_size
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), batch_size):
        texts = [chunk.text for chunk in chunks[start : start + batch_size]]
        # In a thread: the event loop stays free, e.g. to answer RabbitMQ heartbeats.
        vectors.extend(await asyncio.to_thread(context.embedder.embed_documents, texts))
    return [
        ChunkVector(
            chunk_id=chunk_id(document_id, chunk.index),
            chunk_index=chunk.index,
            page_number=chunk.page_number,
            vector=vector,
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]


async def _save_chunks(
    context: JobContext, job: Job, chunks: Sequence[TextChunk], page_count: int | None
) -> bool:
    """Replace the document's chunks and mark it ready, in one transaction.

    False if the document is no longer being processed (it was deleted meanwhile).
    """
    async with context.clients.sessions() as session, session.begin():
        # Lock the row: a delete request waits until we are done, and we see it if it came first.
        status = await session.scalar(
            select(Document.status)
            .where(Document.id == job.document_id, Document.tenant_id == job.tenant_id)
            .with_for_update()
        )
        if status is not DocumentStatus.PROCESSING:
            return False
        await session.execute(delete(Chunk).where(Chunk.document_id == job.document_id))
        session.add_all(
            Chunk(
                id=chunk_id(job.document_id, chunk.index),
                tenant_id=job.tenant_id,
                document_id=job.document_id,
                chunk_index=chunk.index,
                text=chunk.text,
                page_number=chunk.page_number,
                token_count=chunk.token_count,
            )
            for chunk in chunks
        )
        await session.execute(
            update(Document)
            .where(Document.id == job.document_id)
            .values(
                status=DocumentStatus.READY,
                chunk_count=len(chunks),
                page_count=page_count,
                error=None,
            )
        )
    return True
