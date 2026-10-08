"""The language model (LLM): one client for any OpenAI-compatible chat API.

Groq, Ollama, OpenAI, vLLM and LM Studio all accept the same request at
`<base_url>/chat/completions`, so switching is only three settings:
LLM_BASE_URL, LLM_MODEL and LLM_API_KEY.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from shared.config import Settings

logger = logging.getLogger(__name__)

# On "too many requests", wait and try once more, but only if the wait is this short.
MAX_RATE_LIMIT_WAIT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(slots=True)
class Usage:
    """Tokens the LLM read (the prompt) and wrote (the answer). Each call adds to it."""

    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMError(Exception):
    """The LLM service failed, timed out, or refused the request."""


class LLMBusyError(LLMError):
    """The LLM service limits how often we may call it (HTTP 429)."""

    def __init__(self, message: str, retry_after_seconds: float | None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class LLM(Protocol):
    @property
    def model(self) -> str: ...

    async def complete(
        self, messages: Sequence[ChatMessage], usage: Usage, *, max_tokens: int | None = None
    ) -> str:
        """The whole answer at once."""
        ...

    def stream(self, messages: Sequence[ChatMessage], usage: Usage) -> AsyncIterator[str]:
        """The answer piece by piece. `usage` is filled in at the end."""
        ...

    async def aclose(self) -> None: ...


class OpenAICompatibleLLM:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """`transport`: tests pass a fake one (httpx.MockTransport) instead of the network."""
        headers = {}
        if settings.llm_api_key is not None:
            headers["Authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"
        self._http = httpx.AsyncClient(
            base_url=settings.llm_base_url.rstrip("/") + "/",
            headers=headers,
            timeout=settings.llm_timeout_seconds,
            transport=transport,
        )
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._max_tokens = settings.llm_max_output_tokens
        self._reasoning_effort = settings.llm_reasoning_effort

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self, messages: Sequence[ChatMessage], usage: Usage, *, max_tokens: int | None = None
    ) -> str:
        async with self._post(
            self._body(messages, stream=False, max_tokens=max_tokens)
        ) as response:
            data = json.loads(await response.aread())
        _add_usage(usage, data.get("usage"))
        content = data["choices"][0]["message"].get("content")
        return str(content or "")

    async def stream(self, messages: Sequence[ChatMessage], usage: Usage) -> AsyncIterator[str]:
        # The usage of the whole answer comes near the end, and Groq reports it twice
        # ("x_groq" and the standard "usage"). Keep the last report and add it once.
        reported: Any = None
        async with self._post(self._body(messages, stream=True)) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue  # empty lines and comments in the event stream
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    raise LLMError(f"The LLM stopped with an error: {chunk['error']}")
                reported = (
                    chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage") or reported
                )
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        yield piece
        _add_usage(usage, reported)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _body(
        self, messages: Sequence[ChatMessage], *, stream: bool, max_tokens: int | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "temperature": self._temperature,
            "max_tokens": max_tokens or self._max_tokens,
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        if self._reasoning_effort != "none":
            body["reasoning_effort"] = self._reasoning_effort
        return body

    @asynccontextmanager
    async def _post(self, body: dict[str, Any]) -> AsyncIterator[httpx.Response]:
        response = await self._send_with_one_retry(body)
        try:
            yield response
        except httpx.TimeoutException as exc:
            raise LLMError("The LLM stopped answering (timeout).") from exc
        except httpx.TransportError as exc:
            raise LLMError(f"The connection to the LLM broke ({type(exc).__name__}).") from exc
        finally:
            await response.aclose()

    async def _send_with_one_retry(self, body: dict[str, Any]) -> httpx.Response:
        response = await self._send(body)
        wait = _retry_after_seconds(response) if response.status_code == 429 else None
        if wait is not None and wait <= MAX_RATE_LIMIT_WAIT_SECONDS:
            await response.aclose()
            logger.warning("The LLM limits our requests; trying again in %.1f s", wait)
            await asyncio.sleep(wait)
            response = await self._send(body)
        if response.is_error:
            await _raise_error(response)
        return response

    async def _send(self, body: dict[str, Any]) -> httpx.Response:
        request = self._http.build_request("POST", "chat/completions", json=body)
        try:
            return await self._http.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise LLMError("The LLM did not answer in time.") from exc
        except httpx.TransportError as exc:
            raise LLMError(f"Could not reach the LLM ({type(exc).__name__}).") from exc


def _add_usage(usage: Usage, data: Any) -> None:
    if isinstance(data, dict):
        usage.prompt_tokens += int(data.get("prompt_tokens") or 0)
        usage.completion_tokens += int(data.get("completion_tokens") or 0)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


async def _raise_error(response: httpx.Response) -> None:
    detail = (await response.aread()).decode(errors="replace")[:500]
    await response.aclose()
    if response.status_code == 429:
        raise LLMBusyError(f"The LLM is busy (HTTP 429): {detail}", _retry_after_seconds(response))
    raise LLMError(f"The LLM answered with HTTP {response.status_code}: {detail}")
