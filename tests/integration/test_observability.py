"""Traces and metrics on the whole stack: one trace per request, also across the queue."""

from collections.abc import Iterable

import pytest
from httpx import AsyncClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from prometheus_client import REGISTRY

from shared.config import Settings
from tests.documents import make_pdf
from tests.fakes import FakeLLM
from tests.integration.helpers import ask, running_worker, sign_up, upload_and_wait, upload_handbook

pytestmark = pytest.mark.integration

CHAT_STEPS = {
    "chat.load_context",
    "retrieval.embed",
    "cache.lookup",
    "retrieval",
    "retrieval.vector_search",
    "retrieval.keyword_search",
    "retrieval.load_chunks",
    "retrieval.rerank",
    "llm.answer",
    "chat.save",
}
INGEST_STEPS = {
    "job.ingest",
    "ingest.download",
    "ingest.parse",
    "ingest.chunk",
    "ingest.embed",
    "ingest.store_vectors",
    "ingest.save_chunks",
}


def _value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _by_name(spans: Iterable[ReadableSpan]) -> dict[str, ReadableSpan]:
    return {span.name: span for span in spans}


async def test_a_chat_request_is_one_trace_with_every_step(
    chat_api: AsyncClient, chat_settings: Settings, spans: InMemorySpanExporter
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)
    spans.clear()

    await ask(chat_api, owner, "Who may enter zone37?")

    finished = spans.get_finished_spans()
    [request] = [s for s in finished if s.kind is SpanKind.SERVER]
    named = _by_name(finished)
    assert request.name == "POST /v1/chat"
    assert named.keys() >= CHAT_STEPS
    assert {s.context.trace_id for s in finished} == {request.context.trace_id}
    search = named["retrieval"].context.span_id
    for step in ("vector_search", "keyword_search", "load_chunks", "rerank"):
        parent = named[f"retrieval.{step}"].parent
        assert parent is not None
        assert parent.span_id == search
    assert named["retrieval"].attributes == {"retrieval.candidates": 20, "retrieval.relevant": 5}
    assert named["cache.lookup"].attributes == {"cache.result": "miss"}
    llm = named["llm.answer"].attributes
    assert llm is not None
    tokens = llm["llm.tokens.input"]
    assert isinstance(tokens, int)
    assert tokens > 0


async def test_an_upload_and_its_processing_in_the_worker_share_one_trace(
    docs_api: AsyncClient, clean_stack: Settings, spans: InMemorySpanExporter
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    spans.clear()

    async with running_worker(clean_stack):
        document = await upload_and_wait(docs_api, owner, "notes.txt", b"The gym opens at 6 am.")

    assert document["status"] == "ready"
    finished = spans.get_finished_spans()
    [upload] = [s for s in finished if s.name == "POST /v1/documents"]
    named = _by_name(s for s in finished if s.context.trace_id == upload.context.trace_id)
    assert named.keys() >= INGEST_STEPS  # the worker's job, in the upload's trace
    job = named["job.ingest"]
    assert job.kind is SpanKind.CONSUMER
    assert job.parent is not None
    assert named["ingest.parse"].parent is not None
    assert named["ingest.parse"].parent.span_id == job.context.span_id


async def test_metrics_count_answers_by_source_and_llm_calls(
    chat_api: AsyncClient, chat_settings: Settings, fake_llm: FakeLLM
) -> None:
    owner = await sign_up(chat_api, "owner@example.com")
    await upload_handbook(chat_api, owner, chat_settings)
    before = {
        "llm": _value("ragforge_chat_answers_total", source="llm"),
        "cache": _value("ragforge_chat_answers_total", source="exact_cache"),
        "none": _value("ragforge_chat_answers_total", source="no_sources"),
        "calls": _value("ragforge_llm_request_duration_seconds_count", operation="answer"),
        "tokens": _value("ragforge_llm_tokens_total", operation="answer", direction="input"),
        "chat": _value(
            "ragforge_http_requests_total", method="POST", route="/v1/chat", status="200"
        ),
    }

    await ask(chat_api, owner, "Who may enter zone37?")
    await ask(chat_api, owner, "Who may enter zone37?")  # from the cache
    await ask(chat_api, owner, "What is the capital of France?")  # nothing relevant

    assert _value("ragforge_chat_answers_total", source="llm") == before["llm"] + 1
    assert _value("ragforge_chat_answers_total", source="exact_cache") == before["cache"] + 1
    assert _value("ragforge_chat_answers_total", source="no_sources") == before["none"] + 1
    assert (
        _value("ragforge_llm_request_duration_seconds_count", operation="answer")
        == before["calls"] + 1
    )
    assert (
        _value("ragforge_llm_tokens_total", operation="answer", direction="input")
        > before["tokens"]
    )
    assert (
        _value("ragforge_http_requests_total", method="POST", route="/v1/chat", status="200")
        == before["chat"] + 3
    )
    assert len(fake_llm.calls) == 1


async def test_the_worker_counts_jobs_by_outcome(
    docs_api: AsyncClient, clean_stack: Settings
) -> None:
    owner = await sign_up(docs_api, "owner@example.com")
    done = _value("ragforge_jobs_total", type="ingest", outcome="done")
    bad = _value("ragforge_jobs_total", type="ingest", outcome="bad_file")
    timed = _value("ragforge_job_duration_seconds_count", type="ingest")

    async with running_worker(clean_stack):
        ready = await upload_and_wait(docs_api, owner, "notes.txt", b"The pool opens at 7 am.")
        broken = make_pdf(["Hello"])[:200]  # starts like a PDF, but is cut off
        failed = await upload_and_wait(docs_api, owner, "broken.pdf", broken)

    assert (ready["status"], failed["status"]) == ("ready", "failed")
    assert _value("ragforge_jobs_total", type="ingest", outcome="done") == done + 1
    assert _value("ragforge_jobs_total", type="ingest", outcome="bad_file") == bad + 1
    assert _value("ragforge_job_duration_seconds_count", type="ingest") == timed + 2


async def test_the_metrics_page_shows_our_metrics(chat_api: AsyncClient) -> None:
    response = await chat_api.get("/metrics")

    assert response.status_code == 200
    for name in ["ragforge_http_requests_total", "ragforge_chat_answers_total"]:
        assert name in response.text
