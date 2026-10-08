"""Documents on the whole stack: upload -> outbox -> RabbitMQ -> worker -> Qdrant + Postgres.

The worker uses the fake embedder (fast, no model download); a "token" is a word.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from qdrant_client import models
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from api.main import create_app
from shared.clients import Clients
from shared.clients.storage import file_exists
from shared.config import Settings
from shared.db.models import Chunk, OutboxMessage
from shared.vector_store import count_document_vectors
from tests.documents import make_docx, make_pdf
from tests.fakes import fake_ai
from tests.integration.helpers import (
    running_worker,
    sign_up,
    upload,
    upload_and_wait,
    wait_for_status,
    wait_until_deleted,
)
from worker.pipeline import NO_TEXT_ERROR

pytestmark = pytest.mark.integration

RULES = (
    b"Attendance rule. Students must attend 75 percent of classes to sit the final exam.\n\n"
    b"Library rule. Books can be borrowed for two weeks."
)


def _page_text(page: int) -> str:
    """About 120 words that name their page, so we can check page numbers later."""
    return "\n".join(
        f"Rule {page}-{line}: on page {page} students follow rule {line} of this handbook."
        for line in range(10)
    )


@pytest.mark.usefixtures("worker")
async def test_a_text_file_becomes_searchable(
    docs_api: AsyncClient, stack_clients: Clients, clean_stack: Settings, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")

    response = await upload(docs_api, owner, "rules.txt", RULES)
    document = await wait_for_status(docs_api, owner, response.json()["document"]["id"])

    assert response.status_code == 202
    assert response.json()["duplicate"] is False
    assert document["status"] == "ready", document["error"]
    assert document["chunk_count"] == 1
    assert document["mime_type"] == "text/plain"
    document_id = uuid.UUID(document["id"])
    # The chunk is in Postgres, with keyword search ready (tsvector)...
    async with db_engine.connect() as connection:
        found = await connection.scalar(
            text("SELECT count(*) FROM chunks WHERE tsv @@ plainto_tsquery('english', 'attend')")
        )
    assert found == 1
    # ...and its vector is in Qdrant, labelled with the tenant and the document.
    points, _ = await stack_clients.qdrant.scroll(clean_stack.qdrant_collection, limit=10)
    assert len(points) == 1
    assert points[0].payload is not None
    assert points[0].payload["tenant_id"] == str(owner.tenant_id)
    assert points[0].payload["document_id"] == str(document_id)


@pytest.mark.usefixtures("worker")
async def test_a_50_page_pdf_becomes_ready_with_page_numbers(
    docs_api: AsyncClient, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    pdf = make_pdf([_page_text(page) for page in range(1, 51)])

    document = await upload_and_wait(docs_api, owner, "handbook.pdf", pdf)

    assert document["status"] == "ready", document["error"]
    assert document["page_count"] == 50
    async with db_engine.connect() as connection:
        rows = (
            await connection.execute(
                select(Chunk.chunk_index, Chunk.page_number, Chunk.text, Chunk.token_count)
                .where(Chunk.document_id == uuid.UUID(document["id"]))
                .order_by(Chunk.chunk_index)
            )
        ).all()
    assert len(rows) == document["chunk_count"] > 50
    assert {row.page_number for row in rows} == set(range(1, 51))  # every page is cited
    pages_in_order = [row.page_number for row in rows]
    assert pages_in_order == sorted(pages_in_order)
    for row in rows:  # a chunk's text starts on the page it names
        assert f"Rule {row.page_number}-" in row.text.split(":", 1)[0]
        assert row.token_count <= 100


@pytest.mark.usefixtures("worker")
async def test_a_docx_file_becomes_ready(docs_api: AsyncClient) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    data = make_docx(["Hostel rules", "Lights out at 11 pm."], table=[["Room", "Fee"], ["A", "9"]])

    document = await upload_and_wait(docs_api, owner, "hostel.docx", data)

    assert document["status"] == "ready", document["error"]
    assert document["page_count"] is None


@pytest.mark.usefixtures("worker")
async def test_a_broken_pdf_fails_with_a_clear_error(
    docs_api: AsyncClient, stack_clients: Clients, clean_stack: Settings
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    broken = make_pdf(["Hello"])[:200]  # starts like a PDF, but is cut off

    document = await upload_and_wait(docs_api, owner, "broken.pdf", broken)

    assert document["status"] == "failed"
    assert document["error"].startswith("This PDF is broken")
    vectors = await count_document_vectors(
        stack_clients.qdrant,
        clean_stack.qdrant_collection,
        owner.tenant_id,
        uuid.UUID(document["id"]),
    )
    assert vectors == 0


@pytest.mark.usefixtures("worker")
async def test_a_pdf_without_text_fails_with_a_clear_error(docs_api: AsyncClient) -> None:
    owner = await sign_up(docs_api, "owner@example.com")

    document = await upload_and_wait(docs_api, owner, "scan.pdf", make_pdf(["", ""]))

    assert document["status"] == "failed"
    assert document["error"] == NO_TEXT_ERROR


async def test_an_upload_is_kept_even_when_no_worker_runs(
    docs_api: AsyncClient, clean_stack: Settings, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")

    response = await upload(docs_api, owner, "rules.txt", RULES)
    document_id = response.json()["document"]["id"]

    # The job waits safely in the outbox (Postgres) until a worker sends it on.
    assert response.status_code == 202
    async with db_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(OutboxMessage)) == 1
    async with running_worker(clean_stack):
        document = await wait_for_status(docs_api, owner, document_id)
    assert document["status"] == "ready"
    async with db_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(OutboxMessage)) == 0


async def test_the_same_file_twice_is_one_document(docs_api: AsyncClient) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    other_tenant = await sign_up(docs_api, "other@example.com")

    first = await upload(docs_api, owner, "rules.txt", RULES)
    second = await upload(docs_api, owner, "rules-copy.txt", RULES)
    other = await upload(docs_api, other_tenant, "rules.txt", RULES)

    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json()["duplicate"] is True
    assert second.json()["document"]["id"] == first.json()["document"]["id"]
    assert other.status_code == 202  # another tenant gets its own copy
    assert other.json()["document"]["id"] != first.json()["document"]["id"]


@pytest.mark.parametrize(
    ("filename", "content", "status_code", "code"),
    [
        ("virus.exe", b"MZ\x90\x00", 415, "unsupported_file"),
        ("fake.pdf", b"<html>not a pdf</html>", 415, "unsupported_file"),
        ("empty.txt", b"", 400, "empty_file"),
    ],
)
async def test_bad_uploads_are_refused(
    docs_api: AsyncClient, filename: str, content: bytes, status_code: int, code: str
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")

    response = await upload(docs_api, owner, filename, content)

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


async def test_a_file_over_the_size_limit_is_refused(clean_stack: Settings) -> None:
    app = create_app(clean_stack.model_copy(update={"max_upload_mb": 1}), ai=fake_ai())
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        owner = await sign_up(client, "owner@example.com")

        response = await upload(client, owner, "big.txt", b"x" * (1024 * 1024 + 1))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


async def test_documents_are_listed_newest_first_page_by_page(docs_api: AsyncClient) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    ids = []
    for number in range(3):
        response = await upload(docs_api, owner, f"note{number}.txt", f"Note {number}.".encode())
        ids.append(response.json()["document"]["id"])

    first_page = (await docs_api.get("/v1/documents?limit=2", headers=owner.headers)).json()
    second_page = (
        await docs_api.get(
            f"/v1/documents?limit=2&cursor={first_page['next_cursor']}", headers=owner.headers
        )
    ).json()

    assert [item["id"] for item in first_page["items"]] == [ids[2], ids[1]]
    assert [item["id"] for item in second_page["items"]] == [ids[0]]
    assert second_page["next_cursor"] is None


@pytest.mark.usefixtures("worker")
async def test_delete_removes_the_document_from_every_store(
    docs_api: AsyncClient, stack_clients: Clients, clean_stack: Settings, db_engine: AsyncEngine
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    document = await upload_and_wait(docs_api, owner, "rules.txt", RULES)
    document_id = uuid.UUID(document["id"])
    async with stack_clients.sessions() as session:
        storage_key = await session.scalar(
            text("SELECT storage_key FROM documents WHERE id = :id"), {"id": document_id}
        )

    response = await docs_api.delete(f"/v1/documents/{document_id}", headers=owner.headers)
    await wait_until_deleted(docs_api, owner, str(document_id))

    assert response.status_code == 202
    assert response.json()["status"] == "deleting"
    async with db_engine.connect() as connection:
        chunks = await connection.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
        )
    assert chunks == 0
    vectors = await count_document_vectors(
        stack_clients.qdrant, clean_stack.qdrant_collection, owner.tenant_id, document_id
    )
    assert vectors == 0
    assert not await file_exists(stack_clients.s3, clean_stack.s3_bucket, storage_key)


@pytest.mark.usefixtures("worker")
async def test_tenants_cannot_see_or_delete_each_others_documents(
    docs_api: AsyncClient, stack_clients: Clients, clean_stack: Settings
) -> None:
    alice = await sign_up(docs_api, "alice@example.com", tenant_name="Alice College")
    bob = await sign_up(docs_api, "bob@example.com", tenant_name="Bob School")
    document = await upload_and_wait(docs_api, alice, "rules.txt", RULES)
    path = f"/v1/documents/{document['id']}"

    bob_get = await docs_api.get(path, headers=bob.headers)
    bob_delete = await docs_api.delete(path, headers=bob.headers)
    bob_list = await docs_api.get("/v1/documents", headers=bob.headers)
    alice_get = await docs_api.get(path, headers=alice.headers)

    assert bob_get.status_code == bob_delete.status_code == 404
    assert bob_list.json()["items"] == []
    assert alice_get.json()["status"] == "ready"
    # In Qdrant, every point of the document carries Alice's tenant ID.
    points, _ = await stack_clients.qdrant.scroll(
        clean_stack.qdrant_collection,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=document["id"])
                )
            ]
        ),
    )
    assert points
    assert {point.payload["tenant_id"] for point in points if point.payload} == {
        str(alice.tenant_id)
    }
