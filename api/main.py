"""FastAPI application factory.

uvicorn runs `create_app_from_env` (`make dev` and the Docker image do this for you):
`python -m uvicorn api.main:create_app_from_env --factory`.
Tests call `create_app` with their own settings.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from api.errors import install_error_handlers
from api.middleware import RequestContextMiddleware
from api.readiness import build_checks
from api.routes import api_keys, auth, me, system
from shared.clients import Clients
from shared.config import Settings, get_settings
from shared.logging import configure_logging


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


def create_app(settings: Settings) -> FastAPI:
    """Build the app with the given settings."""
    app = FastAPI(title="RAGForge API", version=version("ragforge"), lifespan=lifespan)
    app.state.settings = settings
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(api_keys.router)
    app.include_router(me.router)
    return app


def create_app_from_env() -> FastAPI:
    """Entry point for uvicorn: settings from the environment, JSON logs on stdout."""
    settings = get_settings()
    configure_logging(settings.log_level)
    return create_app(settings)
