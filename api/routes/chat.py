"""Chat: ask a question about your documents and get an answer with citations.

With `"stream": true` the answer comes as Server-Sent Events (text/event-stream):
`start` (IDs), many `token` events (pieces of the answer), then `done` (the same JSON
as the normal response, with the citations). If something fails after the start,
an `error` event ends the stream.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from fastapi.sse import format_sse_event
from pydantic import BaseModel, Field, StringConstraints

from api.auth.principal import WidgetAccess, require_widget_access
from api.chat.service import ChatSessionNotFoundError
from api.dependencies import ChatServiceDep, release_db_connection
from api.errors import ApiError, not_found
from api.ratelimit import limit_questions, limit_requests
from shared.llm import LLMBusyError, LLMError

logger = logging.getLogger(__name__)
# The widget's endpoint: public keys work here, from their allowed websites (checked
# first). Each question counts twice: as a request, and against the questions limit.
# Then the login check's database connection goes back to the pool for the answer.
router = APIRouter(
    prefix="/v1",
    tags=["chat"],
    dependencies=[
        Depends(require_widget_access),
        Depends(limit_requests),
        Depends(limit_questions),
        Depends(release_db_connection),
    ],
)


class ChatRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    session_id: uuid.UUID | None = Field(
        default=None, description="Continue a conversation: send the session_id of an answer."
    )
    top_k: int = Field(default=5, ge=1, le=10, description="How many sources the LLM reads.")
    stream: bool = False


class CitationOut(BaseModel):
    number: int = Field(description="The [n] in the answer.")
    document_id: uuid.UUID
    filename: str
    page: int | None = Field(description="The PDF page of the source; null for other files.")
    snippet: str
    chunk_id: uuid.UUID


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int


class ChatResponse(BaseModel):
    message_id: uuid.UUID
    session_id: uuid.UUID
    answer: str
    citations: list[CitationOut]
    usage: UsageOut
    cache_hit: bool = Field(description="True if the answer came from the cache (no LLM call).")
    latency_ms: int


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        status.HTTP_200_OK: {
            "content": {"text/event-stream": {}},
            "description": "JSON, or an event stream when `stream` is true.",
        }
    },
)
async def chat(
    body: ChatRequest, request: Request, principal: WidgetAccess, service: ChatServiceDep
) -> ChatResponse | StreamingResponse:
    """Answer a question from your documents, with citations (document, page, snippet)."""
    try:
        turn = await service.prepare(
            principal.tenant_id, body.question, body.session_id, body.top_k
        )
        if body.stream:
            events = _encode(
                service.answer_stream(turn), getattr(request.state, "request_id", None)
            )
            return StreamingResponse(
                events,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        result = await service.answer(turn)
    except ChatSessionNotFoundError as exc:
        raise not_found("There is no chat session with this ID.") from exc
    except LLMError as exc:
        raise _llm_unavailable(exc) from exc
    return ChatResponse.model_validate(result.to_json())


async def _encode(
    events: AsyncIterator[tuple[str, dict[str, Any]]], request_id: str | None
) -> AsyncIterator[bytes]:
    """Turn the service's events into Server-Sent Events. Errors become an `error` event."""
    try:
        async for name, data in events:
            yield _sse(name, data)
    except LLMError as exc:
        logger.warning("The LLM failed during a streamed answer: %s", exc)
        error = _llm_unavailable(exc)
        yield _sse(
            "error", {"code": error.code, "message": error.message, "request_id": request_id}
        )
    except Exception:
        logger.exception("A streamed answer failed")
        yield _sse(
            "error",
            {
                "code": "internal_error",
                "message": "Something went wrong on our side.",
                "request_id": request_id,
            },
        )


def _sse(name: str, data: Mapping[str, Any]) -> bytes:
    return format_sse_event(event=name, data_str=json.dumps(data))


def _llm_unavailable(exc: LLMError) -> ApiError:
    logger.warning("LLM error: %s", exc)
    if isinstance(exc, LLMBusyError):
        retry_after = round(exc.retry_after_seconds or 10)
        return ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "llm_busy",
            f"The language model is busy. Please try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "llm_unavailable",
        "The language model is not available right now. Please try again later.",
    )
