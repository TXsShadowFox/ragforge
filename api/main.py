"""FastAPI application factory.

uvicorn runs `create_app_from_env` (`make dev` and the Docker image do this for you):
`python -m uvicorn api.main:create_app_from_env --factory`.
Tests call `create_app` with their own settings (and fake AI models).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from api.ai import AIServices, load_ai_services
from api.errors import install_error_handlers
from api.middleware import RequestContextMiddleware
from api.ratelimit import RateLimiter
from api.readiness import build_checks
from api.routes import analytics, api_keys, auth, chat, documents, feedback, me, system
from shared.answer_cache import AnswerCache
from shared.clients import Clients
from shared.config import Settings, get_settings
from shared.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the service clients and load the AI models at startup; close them at shutdown."""
    settings: Settings = app.state.settings
    ai: AIServices | None = app.state.ai_override
    if ai is None:
        logger.info("Loading the embedding and reranking models")
        ai = await load_ai_services(settings)
    clients = Clients.create(settings)
    app.state.ai = ai
    app.state.clients = clients
    app.state.ready_checks = build_checks(clients, settings)
    app.state.answer_cache = AnswerCache(clients.redis, clients.qdrant, settings)
    app.state.rate_limiter = RateLimiter(clients.redis)
    try:
        yield
    finally:
        await ai.llm.aclose()
        await clients.aclose()


def create_app(settings: Settings, *, ai: AIServices | None = None) -> FastAPI:
    """Build the app. `ai`: use these models instead of loading the real ones (tests)."""
    app = FastAPI(title="RAGForge API", version=version("ragforge"), lifespan=lifespan)
    app.state.settings = settings
    app.state.ai_override = ai
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(api_keys.router)
    app.include_router(me.router)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(feedback.router)
    app.include_router(analytics.router)
    return app


def create_app_from_env() -> FastAPI:
    """Entry point for uvicorn: settings from the environment, JSON logs on stdout."""
    settings = get_settings()
    configure_logging(settings.log_level)
    return create_app(settings)
