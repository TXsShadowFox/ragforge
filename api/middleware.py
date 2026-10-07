"""Request context: a request ID for every request, one access log line, and a safe 500.

This is a plain ASGI middleware, not Starlette's `BaseHTTPMiddleware`: that one runs the
endpoint in another task, and values the endpoint adds to the log context (like the tenant)
would not reach our access log line.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.errors import error_response
from shared.logging import log_context

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
# We keep the caller's request ID only if it is short and simple, so it is safe in logs.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
# Docker and Prometheus call these every few seconds: log them only at DEBUG level.
_QUIET_PATHS = frozenset({"/health", "/metrics"})


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from(scope)
        scope.setdefault("state", {})["request_id"] = request_id  # read by api/errors.py
        started_at = time.perf_counter()
        response_started = False
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                logger.exception("Unhandled error")
                if response_started:
                    raise  # too late to send an error response
                response = error_response(
                    request_id, 500, "internal_error", "Something went wrong on our side."
                )
                await response(scope, receive, send_with_request_id)
            finally:
                _log_request(scope, status_code, started_at)


def _request_id_from(scope: Scope) -> str:
    """The caller's X-Request-ID if it is safe, otherwise a new random one."""
    incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if incoming and _SAFE_REQUEST_ID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


def _log_request(scope: Scope, status_code: int, started_at: float) -> None:
    method, path = scope["method"], scope["path"]
    duration_ms = round((time.perf_counter() - started_at) * 1000, 1)
    level = logging.DEBUG if path in _QUIET_PATHS else logging.INFO
    logger.log(
        level,
        "%s %s %s",
        method,
        path,
        status_code,
        extra={"method": method, "path": path, "status": status_code, "duration_ms": duration_ms},
    )
