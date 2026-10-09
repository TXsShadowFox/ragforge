"""FastAPI application factory.

uvicorn runs `create_app_from_env` (`make dev` and the Docker image do this for you):
`python -m uvicorn api.main:create_app_from_env --factory`.
Tests call `create_app` with their own settings (and fake AI models).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.telemetry import TelemetryConfig

from api.ai import AIServices, load_ai_services
from api.errors import install_error_handlers
from api.limiter import RateLimiter
from api.middleware import (
    QUIET_PATHS,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
)
from api.readiness import build_checks, run_checks
from api.routes import (
    analytics,
    api_keys,
    auth,
    chat,
    documents,
    feedback,
    me,
    system,
    widget,
)
from shared import metrics
from shared.answer_cache import AnswerCache
from shared.clients import Clients
from shared.config import Settings, get_settings
from shared.logging import configure_logging
from shared.tracing import setup_tracing

logger = logging.getLogger(__name__)

# FastAPI's own OpenTelemetry spans: one per request (named like "POST /v1/chat"), with
# child spans for the dependencies, the endpoint and the response. They are exported only
# when shared/tracing.py installs a tracer (OTLP_TRACES_ENDPOINT). Metrics and logs have
# their own tools here (Prometheus, JSON logs), and exporters come only from our settings.
TELEMETRY: TelemetryConfig = {
    "tracing": True,
    "operation_spans": True,
    "metrics": False,
    "logs": False,
    "auto_configure": False,
    "exclude": lambda scope: scope.get("path") in QUIET_PATHS,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the service clients and load the AI models at startup; close them at shutdown."""
    settings: Settings = app.state.settings
    tracing = setup_tracing(settings, "ragforge-api")  # None: no OTLP endpoint, no tracing
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
    # Open a connection to each service now (the /ready probes), so the first user does
    # not wait for them. A service that is down only logs a warning here.
    await run_checks(app.state.ready_checks, settings.ready_check_timeout_seconds)
    try:
        yield
    finally:
        await ai.llm.aclose()
        await clients.aclose()
        if tracing is not None:
            await asyncio.to_thread(tracing.shutdown)  # sends the last spans


def create_app(settings: Settings, *, ai: AIServices | None = None) -> FastAPI:
    """Build the app. `ai`: use these models instead of loading the real ones (tests)."""
    app = FastAPI(
        title="RAGForge API", version=version("ragforge"), lifespan=lifespan, telemetry=TELEMETRY
    )
    app.state.settings = settings
    metrics.start_api_metrics()
    app.state.ai_override = ai
    install_error_handlers(app)
    # Added first, so it runs inside RequestContextMiddleware: a 413 also gets a request ID.
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=settings.max_request_kb * 1024,
        max_upload_bytes=settings.max_upload_bytes,
    )
    app.add_middleware(RequestContextMiddleware)
    # Added last, so it runs first (even before a crash is turned into a 500). Browsers on
    # other websites (the chat widget) may call the API. We use no cookies, so this
    # exposes nothing: the login token or key is in a header. Public keys still check
    # the website (api/auth/principal.py).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=[
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            REQUEST_ID_HEADER,
        ],
        max_age=600,
    )
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(api_keys.router)
    app.include_router(me.router)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(feedback.router)
    app.include_router(analytics.router)
    app.include_router(widget.router)
    return app


def create_app_from_env() -> FastAPI:
    """Entry point for uvicorn: settings from the environment, JSON logs on stdout."""
    settings = get_settings()
    configure_logging(settings.log_level)
    return create_app(settings)
