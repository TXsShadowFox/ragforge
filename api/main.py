"""FastAPI application factory.

Run it with: `python -m uvicorn api.main:create_app --factory`
(`make dev` and the Docker image do this for you).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from api.readiness import build_checks
from api.routes import system
from shared.clients import Clients
from shared.config import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the service clients at startup and close them at shutdown."""
    settings: Settings = app.state.settings
    clients = Clients.create(settings)
    app.state.clients = clients
    app.state.ready_checks = build_checks(clients, settings)
    try:
        yield
    finally:
        await clients.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings; normal runs read them from the env."""
    app = FastAPI(title="RAGForge API", version=version("ragforge"), lifespan=lifespan)
    app.state.settings = settings or get_settings()
    app.include_router(system.router)
    return app
