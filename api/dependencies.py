"""FastAPI dependencies: shared objects that routes receive as parameters.

Tests replace them with `app.dependency_overrides`.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.ai import AIServices
from api.chat.service import ChatService
from api.readiness import DependencyCheck
from shared.answer_cache import AnswerCache
from shared.clients import Clients
from shared.config import Settings


def get_app_settings(request: Request) -> Settings:
    """The settings the app was created with."""
    settings: Settings = request.app.state.settings
    return settings


def get_ready_checks(request: Request) -> list[DependencyCheck]:
    """The readiness probes, built at startup (see `api.main.lifespan`)."""
    checks: list[DependencyCheck] = request.app.state.ready_checks
    return checks


def get_clients(request: Request) -> Clients:
    """The service clients, created at startup (see `api.main.lifespan`)."""
    clients: Clients = request.app.state.clients
    return clients


def get_ai(request: Request) -> AIServices:
    """The embedder, the reranker and the LLM, loaded at startup."""
    ai: AIServices = request.app.state.ai
    return ai


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One database session per request. Routes commit their own changes."""
    async with get_clients(request).sessions() as session:
        yield session


def get_answer_cache(request: Request) -> AnswerCache:
    """The answer cache (Redis + Qdrant), created at startup."""
    cache: AnswerCache = request.app.state.answer_cache
    return cache


def get_chat_service(request: Request) -> ChatService:
    return ChatService(
        get_app_settings(request), get_clients(request), get_ai(request), get_answer_cache(request)
    )


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ClientsDep = Annotated[Clients, Depends(get_clients)]
# scope="function": the session closes when the route function returns, before the response
# is sent. So a streamed answer does not keep a database connection while the LLM writes.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


async def release_db_connection(session: SessionDep) -> None:
    """Give the request session's connection back to the pool (the login check used it).

    For routes that work for seconds without it, like a chat answer (which opens short
    sessions of its own). The load test found why: each chat request kept this connection
    and needed a second one, so 15 of them held the whole pool and waited for each other
    until the pool timeout (50 users: 92% errors). Not for every route: two checkouts cost
    short database routes like uploads 10-18% of their throughput.
    """
    await session.close()
