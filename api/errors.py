"""One JSON format for every error response:

    {"error": {"code": "invalid_api_key", "message": "...", "request_id": "..."}}

`code` is stable, so programs can check it; `message` is for people. Validation errors (422)
also have `details`: which fields are wrong (never their values, which may be passwords).
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """Raise this in routes and dependencies to send an error in our format."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = dict(headers or {})


class RequestTooLargeError(StarletteHTTPException):
    """The request body grew past its limit while it was read (api/middleware.py).

    An HTTPException on purpose: FastAPI passes those on while it reads a body, so the
    client gets our 413 (any other error there becomes a 400).
    """

    def __init__(self, limit_bytes: int) -> None:
        super().__init__(status.HTTP_413_CONTENT_TOO_LARGE, detail=too_large_message(limit_bytes))


def too_large_message(limit_bytes: int) -> str:
    size = (
        f"{limit_bytes // (1024 * 1024)} MB"
        if limit_bytes >= 1024 * 1024
        else f"{limit_bytes // 1024} KB"
    )
    return f"The request is bigger than the limit of {size}."


def unauthorized(code: str, message: str) -> ApiError:
    """401: we do not know who is calling. The header tells clients to send a Bearer token."""
    return ApiError(
        status.HTTP_401_UNAUTHORIZED, code, message, headers={"WWW-Authenticate": "Bearer"}
    )


def forbidden(code: str, message: str) -> ApiError:
    """403: we know who is calling, but they may not do this."""
    return ApiError(status.HTTP_403_FORBIDDEN, code, message)


def not_found(message: str) -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "not_found", message)


def error_response(
    request_id: str | None,
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[dict[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status_code, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    """Send every error in our format. (Unexpected errors: see api/middleware.py.)"""
    # The decorator form accepts handlers for our exact exception types.
    app.exception_handler(ApiError)(_handle_api_error)
    app.exception_handler(RequestTooLargeError)(_handle_request_too_large)
    app.exception_handler(StarletteHTTPException)(_handle_http_exception)
    app.exception_handler(RequestValidationError)(_handle_validation_error)


def _request_id(request: Request) -> str | None:
    request_id: str | None = getattr(request.state, "request_id", None)
    return request_id


async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(
        _request_id(request), exc.status_code, exc.code, exc.message, headers=exc.headers
    )


async def _handle_request_too_large(request: Request, exc: RequestTooLargeError) -> JSONResponse:
    return request_too_large(_request_id(request), str(exc.detail))


def request_too_large(request_id: str | None, message: str) -> JSONResponse:
    """413. `Connection: close`: we stop reading, so the connection cannot be used again."""
    return error_response(
        request_id,
        status.HTTP_413_CONTENT_TOO_LARGE,
        "request_too_large",
        message,
        headers={"Connection": "close"},
    )


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Errors raised by FastAPI itself, like 404 for an unknown URL or 405 for a wrong method."""
    message = exc.detail if isinstance(exc.detail, str) else _phrase(exc.status_code)
    return error_response(
        _request_id(request),
        exc.status_code,
        _code_for(exc.status_code),
        message,
        headers=exc.headers,
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"loc": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return error_response(
        _request_id(request),
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "Some fields are missing or not valid. See details.",
        details=details,
    )


def _code_for(status_code: int) -> str:
    """404 -> "not_found", 405 -> "method_not_allowed"."""
    return _phrase(status_code).lower().replace(" ", "_").replace("-", "_")


def _phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP error"
