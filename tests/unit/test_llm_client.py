"""Tests for the OpenAI-compatible LLM client, with a fake HTTP server (no network)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from shared.config import Settings
from shared.llm import ChatMessage, LLMBusyError, LLMError, OpenAICompatibleLLM, Usage

QUESTION = [ChatMessage("system", "Be brief."), ChatMessage("user", "Hi?")]


def _llm(
    settings: Settings, handler: Callable[[httpx.Request], httpx.Response], **changes: Any
) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        settings.model_copy(update=changes), transport=httpx.MockTransport(handler)
    )


def _stream_body(*chunks: dict[str, Any]) -> bytes:
    """An event stream like the one OpenAI-compatible APIs send."""
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def _delta(text: str) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": {"content": text}}]}


async def test_complete_returns_the_answer_and_adds_the_usage(settings: Settings) -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    usage = Usage(prompt_tokens=5)
    llm = _llm(settings, handler, llm_model="openai/gpt-oss-20b", llm_reasoning_effort="low")

    assert await llm.complete(QUESTION, usage, max_tokens=50) == "Hello!"
    assert (usage.prompt_tokens, usage.completion_tokens) == (17, 3)  # added to what was there
    [body] = sent
    assert body["model"] == "openai/gpt-oss-20b"
    assert body["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Hi?"},
    ]
    assert (body["max_tokens"], body["stream"], body["reasoning_effort"]) == (50, False, "low")
    await llm.aclose()


async def test_stream_yields_the_pieces_and_reads_the_usage_at_the_end(settings: Settings) -> None:
    body = _stream_body(
        _delta("Hel"),
        {"choices": [{"index": 0, "delta": {"reasoning": "thinking..."}}]},  # not part of it
        _delta("lo!"),
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}},
    )
    llm = _llm(settings, lambda _: httpx.Response(200, content=body))
    usage = Usage()

    pieces = [piece async for piece in llm.stream(QUESTION, usage)]

    assert pieces == ["Hel", "lo!"]
    assert (usage.prompt_tokens, usage.completion_tokens) == (9, 2)
    await llm.aclose()


async def test_stream_reads_groq_style_usage(settings: Settings) -> None:
    final = {"choices": [], "x_groq": {"usage": {"prompt_tokens": 4, "completion_tokens": 1}}}
    llm = _llm(settings, lambda _: httpx.Response(200, content=_stream_body(_delta("Hi"), final)))
    usage = Usage()

    assert [piece async for piece in llm.stream(QUESTION, usage)] == ["Hi"]
    assert (usage.prompt_tokens, usage.completion_tokens) == (4, 1)
    await llm.aclose()


async def test_usage_reported_twice_in_a_stream_is_counted_once(settings: Settings) -> None:
    # What Groq really sends: the usage in "x_groq" on the last piece, and again at the end.
    usage_data = {"prompt_tokens": 90, "completion_tokens": 17}
    body = _stream_body(
        _delta("Blue."),
        {"choices": [{"index": 0, "delta": {}}], "x_groq": {"usage": usage_data}},
        {"choices": [], "usage": usage_data},
    )
    llm = _llm(settings, lambda _: httpx.Response(200, content=body))
    usage = Usage(prompt_tokens=10, completion_tokens=5)  # e.g. from the rewrite call

    [_ async for _ in llm.stream(QUESTION, usage)]

    assert (usage.prompt_tokens, usage.completion_tokens) == (100, 22)
    await llm.aclose()


async def test_the_api_key_is_sent_and_stream_asks_for_the_usage(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_stream_body(_delta("ok")))

    llm = _llm(settings, handler, llm_api_key=SecretStr("gsk_test"), llm_reasoning_effort="none")

    [_ async for _ in llm.stream(QUESTION, Usage())]

    assert seen[0].headers["authorization"] == "Bearer gsk_test"
    assert seen[0].url.path.endswith("/chat/completions")
    body = json.loads(seen[0].content)
    assert body["stream_options"] == {"include_usage": True}
    assert "reasoning_effort" not in body  # "none": not sent (e.g. for Ollama models)
    await llm.aclose()


async def test_a_short_rate_limit_is_waited_out_once(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("shared.llm.asyncio.sleep", fake_sleep)
    answers = iter(
        [
            httpx.Response(429, headers={"retry-after": "2"}, json={"error": "slow down"}),
            httpx.Response(200, json={"choices": [{"message": {"content": "Done."}}]}),
        ]
    )
    llm = _llm(settings, lambda _: next(answers))

    assert await llm.complete(QUESTION, Usage()) == "Done."
    assert waits == [2.0]
    await llm.aclose()


async def test_a_long_rate_limit_becomes_a_busy_error(settings: Settings) -> None:
    llm = _llm(
        settings,
        lambda _: httpx.Response(429, headers={"retry-after": "60"}, json={"error": "limit"}),
    )

    with pytest.raises(LLMBusyError) as caught:
        await llm.complete(QUESTION, Usage())

    assert caught.value.retry_after_seconds == 60.0
    await llm.aclose()


async def test_a_server_error_becomes_an_llm_error(settings: Settings) -> None:
    llm = _llm(settings, lambda _: httpx.Response(500, text="Internal error"))

    with pytest.raises(LLMError, match="HTTP 500"):
        await llm.complete(QUESTION, Usage())
    await llm.aclose()


async def test_a_connection_failure_becomes_an_llm_error(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    llm = _llm(settings, handler)

    with pytest.raises(LLMError, match="Could not reach the LLM"):
        [_ async for _ in llm.stream(QUESTION, Usage())]
    await llm.aclose()
