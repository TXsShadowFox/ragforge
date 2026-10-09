"""Documents: upload, list, show and delete. The worker does the heavy work in the background.

Upload saves the file to storage, then the document row and an "ingest" job (in the outbox)
in one transaction, and answers 202 right away. Poll GET /v1/documents/{id} for the status.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Self

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import Uuid, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.principal import PrivateAccess
from api.dependencies import ClientsDep, SessionDep, SettingsDep
from api.errors import ApiError, not_found
from api.ratelimit import limit_requests
from shared.answer_cache import bump_docs_version, forget_old_answers
from shared.clients.storage import delete_file, upload_file
from shared.db.models import Document, DocumentStatus, JobType
from shared.file_types import SNIFF_BYTES, UnsupportedFileError, clean_filename, detect_file_type
from shared.jobs import Job
from shared.outbox import add_job

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/v1/documents", tags=["documents"], dependencies=[Depends(limit_requests)]
)

READ_CHUNK_BYTES = 1024 * 1024


class DocumentInfo(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: DocumentStatus
    error: str | None = Field(description="Why processing failed (only when status is failed).")
    chunk_count: int
    page_count: int | None = Field(description="Pages, for PDFs.")
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, document: Document) -> Self:
        return cls(
            id=document.id,
            filename=document.filename,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            status=document.status,
            error=document.error,
            chunk_count=document.chunk_count,
            page_count=document.page_count,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class UploadResponse(BaseModel):
    document: DocumentInfo
    duplicate: bool = Field(description="True if you uploaded this file before: nothing new.")


class DocumentList(BaseModel):
    items: list[DocumentInfo]
    next_cursor: uuid.UUID | None = Field(description="Send it as `cursor` for the next page.")


@dataclass(frozen=True, slots=True)
class _ReadUpload:
    size: int
    sha256: str
    head: bytes  # the first bytes, to check the file type


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    responses={status.HTTP_200_OK: {"model": UploadResponse, "description": "A duplicate."}},
)
async def upload_document(
    file: UploadFile,
    response: Response,
    principal: PrivateAccess,
    session: SessionDep,
    clients: ClientsDep,
    settings: SettingsDep,
) -> UploadResponse:
    """Upload a PDF, DOCX, TXT, MD or HTML file. It is processed in the background."""
    upload = await _read_upload(file, settings.max_upload_bytes)
    filename = clean_filename(file.filename or "")
    try:
        file_type = detect_file_type(filename, upload.head)
    except UnsupportedFileError as exc:
        raise ApiError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "unsupported_file", str(exc)
        ) from exc

    existing = await _find_by_hash(session, principal.tenant_id, upload.sha256)
    if existing is not None:
        return _duplicate(existing, response)
    await _check_document_limit(session, principal.tenant_id, settings.max_documents_per_tenant)

    # The ID comes first: it names the file in storage. (Postgres 18 makes uuidv7 IDs.)
    new_id = await session.execute(select(func.uuidv7(type_=Uuid())))
    document_id: uuid.UUID = new_id.scalar_one()
    storage_key = f"tenants/{principal.tenant_id}/documents/{document_id}"
    await file.seek(0)
    try:
        await upload_file(clients.s3, settings.s3_bucket, storage_key, file.file, file_type.value)
    except (BotoCoreError, ClientError) as exc:
        logger.exception("Could not store an uploaded file")
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "storage_unavailable",
            "We could not store the file right now. Please try again.",
        ) from exc

    document = Document(
        id=document_id,
        tenant_id=principal.tenant_id,
        filename=filename,
        mime_type=file_type.value,
        size_bytes=upload.size,
        sha256=upload.sha256,
        storage_key=storage_key,
    )
    session.add(document)
    add_job(session, Job(JobType.INGEST, principal.tenant_id, document_id))  # same transaction
    try:
        await session.commit()
    except IntegrityError:
        # The same file was uploaded at the same moment, and the other upload won.
        await session.rollback()
        try:
            await delete_file(clients.s3, settings.s3_bucket, storage_key)
        except (BotoCoreError, ClientError):
            logger.warning("Could not delete the extra copy %s", storage_key, exc_info=True)
        existing = await _find_by_hash(session, principal.tenant_id, upload.sha256)
        if existing is None:
            raise
        return _duplicate(existing, response)
    return UploadResponse(document=DocumentInfo.from_row(document), duplicate=False)


@router.get("")
async def list_documents(
    principal: PrivateAccess,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: uuid.UUID | None = None,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
) -> DocumentList:
    """Your documents, newest first. Send `next_cursor` back as `cursor` for the next page."""
    query = select(Document).where(Document.tenant_id == principal.tenant_id)
    if status_filter is not None:
        query = query.where(Document.status == status_filter)
    if cursor is not None:
        query = query.where(Document.id < cursor)  # uuidv7 IDs are ordered by time
    rows = list(await session.scalars(query.order_by(Document.id.desc()).limit(limit + 1)))
    page = rows[:limit]
    has_more = len(rows) > limit
    return DocumentList(
        items=[DocumentInfo.from_row(row) for row in page],
        next_cursor=page[-1].id if has_more else None,
    )


@router.get("/{document_id}")
async def get_document(
    document_id: uuid.UUID, principal: PrivateAccess, session: SessionDep
) -> DocumentInfo:
    return DocumentInfo.from_row(await _get_owned(session, principal.tenant_id, document_id))


@router.delete("/{document_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_document(
    document_id: uuid.UUID,
    principal: PrivateAccess,
    session: SessionDep,
    clients: ClientsDep,
    settings: SettingsDep,
) -> DocumentInfo:
    """Delete a document from Postgres, Qdrant and storage. The worker does it soon."""
    document = await _get_owned(session, principal.tenant_id, document_id, for_update=True)
    if document.status is DocumentStatus.DELETING:
        return DocumentInfo.from_row(document)
    was_searchable = document.status is DocumentStatus.READY
    document.status = DocumentStatus.DELETING  # search stops using it at once
    add_job(session, Job(JobType.DELETE, principal.tenant_id, document.id))
    new_version = await bump_docs_version(session, principal.tenant_id) if was_searchable else None
    await session.commit()
    if new_version is not None:  # cached answers may cite this document
        await forget_old_answers(
            clients.qdrant, settings.answer_cache_collection, principal.tenant_id, new_version
        )
    return DocumentInfo.from_row(document)


async def _read_upload(file: UploadFile, max_bytes: int) -> _ReadUpload:
    """Read the file once: its size, its SHA-256, and its first bytes."""
    digest = hashlib.sha256()
    size = 0
    head = b""
    while chunk := await file.read(READ_CHUNK_BYTES):
        size += len(chunk)
        if size > max_bytes:
            raise ApiError(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "file_too_large",
                f"The file is bigger than the limit of {max_bytes // (1024 * 1024)} MB.",
            )
        if len(head) < SNIFF_BYTES:
            head += chunk[: SNIFF_BYTES - len(head)]
        digest.update(chunk)
    if size == 0:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "empty_file", "The file is empty.")
    return _ReadUpload(size=size, sha256=digest.hexdigest(), head=head)


async def _check_document_limit(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int | None
) -> None:
    """403 when the tenant already keeps `limit` documents (those being deleted do not
    count). Two uploads at the same moment can both pass: a soft limit, enough to keep a
    public demo's disk from filling up."""
    if limit is None:
        return
    count = await session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.tenant_id == tenant_id, Document.status != DocumentStatus.DELETING)
    )
    if (count or 0) >= limit:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "document_limit_reached",
            f"This account can keep at most {limit} documents. Delete one to upload another.",
        )


async def _find_by_hash(
    session: AsyncSession, tenant_id: uuid.UUID, sha256: str
) -> Document | None:
    document: Document | None = await session.scalar(
        select(Document).where(Document.tenant_id == tenant_id, Document.sha256 == sha256)
    )
    return document


def _duplicate(existing: Document, response: Response) -> UploadResponse:
    if existing.status is DocumentStatus.DELETING:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "document_being_deleted",
            "This file is being deleted right now. Please upload it again in a moment.",
        )
    response.status_code = status.HTTP_200_OK
    return UploadResponse(document=DocumentInfo.from_row(existing), duplicate=True)


async def _get_owned(
    session: AsyncSession, tenant_id: uuid.UUID, document_id: uuid.UUID, *, for_update: bool = False
) -> Document:
    query = select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
    if for_update:
        query = query.with_for_update()
    document = await session.scalar(query)
    if document is None:  # also for another tenant's document: we do not say it exists
        raise not_found("There is no document with this ID.")
    return document
