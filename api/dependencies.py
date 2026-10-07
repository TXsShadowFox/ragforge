"""FastAPI dependencies: shared objects that routes receive as parameters.

Tests replace them with `app.dependency_overrides`.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.readiness import DependencyCheck
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


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One database session per request. Routes commit their own changes."""
    async with get_clients(request).sessions() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
ClientsDep = Annotated[Clients, Depends(get_clients)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
