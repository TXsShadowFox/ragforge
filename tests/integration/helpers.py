"""Helpers for the API tests: sign up, log in, keys, uploads, chat, and a background worker."""

import asyncio
import json
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from httpx import ASGITransport, AsyncClient, Response

from api.main import create_app
from shared.config import Settings
from shared.embeddings import Embedder
from tests.documents import handbook_page, make_pdf
from tests.fakes import FakeEmbedder, FakeLLM, fake_ai
from worker.runner import run_worker

PASSWORD = "correct-horse-battery-staple"
WAIT_SECONDS = 30.0


@dataclass(frozen=True)
class Account:
    """A tenant owner who signed up and logged in."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return bearer(self.token)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def sign_up(client: AsyncClient, email: str, tenant_name: str = "Acme") -> Account:
    response = await client.post(
        "/v1/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    ids = response.json()
    return Account(
        tenant_id=uuid.UUID(ids["tenant_id"]),
        user_id=uuid.UUID(ids["user_id"]),
        token=await log_in(client, email),
    )


async def log_in(client: AsyncClient, email: str, password: str = PASSWORD) -> str:
    response = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


async def create_key(client: AsyncClient, account: Account, **body: Any) -> dict[str, Any]:
    response = await client.post(
        "/v1/api-keys", json={"name": "test key", **body}, headers=account.headers
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def upload(client: AsyncClient, account: Account, filename: str, content: bytes) -> Response:
    return await client.post(
        "/v1/documents", files={"file": (filename, content)}, headers=account.headers
    )


async def upload_and_wait(
    client: AsyncClient, account: Account, filename: str, content: bytes
) -> dict[str, Any]:
    """Upload a file and wait until the worker is done with it (ready or failed)."""
    response = await upload(client, account, filename, content)
    assert response.status_code == 202, response.text
    return await wait_for_status(client, account, response.json()["document"]["id"])


async def wait_for_status(
    client: AsyncClient,
    account: Account,
    document_id: str,
    statuses: tuple[str, ...] = ("ready", "failed"),
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + WAIT_SECONDS
    while True:
        response = await client.get(f"/v1/documents/{document_id}", headers=account.headers)
        assert response.status_code == 200, response.text
        document: dict[str, Any] = response.json()
        if document["status"] in statuses:
            return document
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"The document is still {document['status']!r}")
        await asyncio.sleep(0.1)


async def wait_until_deleted(client: AsyncClient, account: Account, document_id: str) -> None:
    deadline = asyncio.get_running_loop().time() + WAIT_SECONDS
    while True:
        response = await client.get(f"/v1/documents/{document_id}", headers=account.headers)
        if response.status_code == 404:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"The document was not deleted: {response.text}")
        await asyncio.sleep(0.1)


@asynccontextmanager
async def running_worker(
    settings: Settings, embedder: Embedder | None = None
) -> AsyncIterator[None]:
    """Run the worker in the background while the block runs, then stop it."""
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(settings, embedder=embedder or FakeEmbedder(), stop=stop))
    try:
        yield
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=WAIT_SECONDS)


@asynccontextmanager
async def chat_client(settings: Settings, llm: FakeLLM | None = None) -> AsyncIterator[AsyncClient]:
    """An HTTP client for an app that uses the fake models (and the given fake LLM)."""
    app = create_app(settings, ai=fake_ai(llm))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


async def upload_handbook(client: AsyncClient, account: Account, settings: Settings) -> None:
    """The 50-page handbook: only page N mentions "zoneN" (see tests/documents.py)."""
    pdf = make_pdf([handbook_page(page) for page in range(1, 51)])
    async with running_worker(settings):
        document = await upload_and_wait(client, account, "handbook.pdf", pdf)
    assert document["status"] == "ready", document["error"]


async def ask(
    client: AsyncClient, account: Account, question: str, **options: Any
) -> dict[str, Any]:
    response = await client.post(
        "/v1/chat", json={"question": question, **options}, headers=account.headers
    )
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    return answer


def sse_events(stream_text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse a Server-Sent Events body into (event name, data) pairs."""
    events = []
    for block in stream_text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def with_redis_down(settings: Settings) -> Settings:
    """The same settings, but Redis is "down": nothing listens at its address."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))  # a free port; closed again when the block ends
        port = probe.getsockname()[1]
    return settings.model_copy(update={"redis_url": f"redis://127.0.0.1:{port}/0"})
