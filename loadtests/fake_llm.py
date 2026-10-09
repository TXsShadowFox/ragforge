"""A fake LLM: an OpenAI-compatible `POST /v1/chat/completions` that answers after a fixed
delay. The load tests and the browser test in CI use it instead of Groq.

Groq's free tier allows ~30 requests a minute, so the real LLM cannot be load-tested; with
this one, a load test measures RAGForge itself. Its answers look like a careful LLM's: the
sentence of the sources that shares the most words with the question, with its source
number. A rewrite request (for a follow-up question) gets the last question back.

Run it: `uvicorn loadtests.fake_llm:app --port 8080` (docker-compose.fake-llm.yml does).
Delays: FAKE_LLM_FIRST_TOKEN_SECONDS (default 0.4) before the first word, then
FAKE_LLM_TOKENS_PER_SECOND (default 200): close to Groq's gpt-oss-20b in the evaluation.
"""

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from api.chat.prompts import NO_ANSWER, REWRITE_RULES

FIRST_TOKEN_SECONDS = float(os.environ.get("FAKE_LLM_FIRST_TOKEN_SECONDS", "0.4"))
TOKENS_PER_SECOND = float(os.environ.get("FAKE_LLM_TOKENS_PER_SECOND", "200"))

_SOURCE = re.compile(r'<source id="(\d+)"[^>]*>\n(.*?)\n</source>', re.DOTALL)
_WORD = re.compile(r"[a-z0-9]{3,}")
# Words that say nothing about the topic: they must not decide which sentence matches.
_COMMON_WORDS = frozenset(
    {
        "the", "and", "for", "are", "was", "were", "what", "when", "where", "which",
        "who", "how", "can", "does", "did", "with", "that", "this", "from", "have",
        "has", "you", "your", "our", "its", "not", "but", "all", "any",
    }
)  # fmt: skip
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_PIECE = re.compile(r"\S+\s*")  # a word and the spaces after it: the pieces join to the text


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    prompt_tokens: int

    @property
    def pieces(self) -> list[str]:
        return _PIECE.findall(self.text)


def reply_to(messages: list[dict[str, Any]]) -> Reply:
    """What the fake LLM says to these messages (OpenAI format)."""
    contents = [str(message.get("content") or "") for message in messages]
    prompt_tokens = sum(len(content) for content in contents) // 4  # ~4 characters a token
    last = contents[-1] if contents else ""
    if contents and contents[0] == REWRITE_RULES:
        return Reply(last.rsplit("Last question: ", 1)[-1].strip(), prompt_tokens)
    return Reply(_answer(last), prompt_tokens)


def _answer(prompt: str) -> str:
    """The source sentence with the most words of the question, cited; else "I don't know"."""
    question = set(_words(prompt.rsplit("Question: ", 1)[-1])) - _COMMON_WORDS
    best, best_shared = "", 0
    for number, text in _SOURCE.findall(prompt):
        for sentence in _SENTENCE_END.split(" ".join(text.split())):
            shared = len(question & set(_words(sentence)))
            if shared > best_shared:
                best, best_shared = f"{sentence} [{number}]", shared
    return best or NO_ANSWER


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


async def chat_completions(request: Request) -> Response:
    body = await request.json()
    reply = reply_to(body.get("messages") or [])
    model = str(body.get("model") or "fake-llm")
    if body.get("stream"):
        return StreamingResponse(_stream(reply, model), media_type="text/event-stream")
    await asyncio.sleep(FIRST_TOKEN_SECONDS + len(reply.pieces) / TOKENS_PER_SECOND)
    return JSONResponse(
        {
            "id": "fake",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply.text},
                    "finish_reason": "stop",
                }
            ],
            "usage": _usage(reply),
        }
    )


async def _stream(reply: Reply, model: str) -> AsyncIterator[str]:
    """Server-Sent Events like OpenAI's: one chunk per word, then the usage, then [DONE]."""
    await asyncio.sleep(FIRST_TOKEN_SECONDS)
    for number, piece in enumerate(reply.pieces):
        if number:
            await asyncio.sleep(1 / TOKENS_PER_SECOND)
        yield _event(model, [{"index": 0, "delta": {"content": piece}, "finish_reason": None}])
    yield _event(model, [{"index": 0, "delta": {}, "finish_reason": "stop"}])
    yield _event(model, [], usage=_usage(reply))
    yield "data: [DONE]\n\n"


def _event(model: str, choices: list[dict[str, Any]], **extra: Any) -> str:
    chunk = {"id": "fake", "object": "chat.completion.chunk", "model": model, "choices": choices}
    return f"data: {json.dumps({**chunk, **extra})}\n\n"


def _usage(reply: Reply) -> dict[str, int]:
    completion = len(reply.pieces)
    return {
        "prompt_tokens": reply.prompt_tokens,
        "completion_tokens": completion,
        "total_tokens": reply.prompt_tokens + completion,
    }


async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


app = Starlette(
    routes=[
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/health", health),
    ]
)
