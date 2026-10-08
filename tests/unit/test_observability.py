"""Metrics, spans, the trace context of jobs, and the Grafana dashboard. No Docker needed."""

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from prometheus_client import REGISTRY, Counter, Gauge, Histogram

from api.middleware import RequestContextMiddleware
from shared import metrics
from shared.config import Settings
from shared.db.models import JobType, OutboxMessage
from shared.jobs import Job
from shared.logging import JsonFormatter
from shared.outbox import add_job
from shared.tracing import current_traceparent, job_span, setup_tracing, span

REPO = Path(__file__).resolve().parents[2]
DASHBOARD = REPO / "infra" / "grafana" / "dashboards" / "ragforge.json"
DATASOURCES = REPO / "infra" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
# version-trace ID-parent span ID-flags (01: sampled; newer SDKs also set 02: random ID)
TRACEPARENT = re.compile(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}")


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        if item_id == 500:
            raise RuntimeError("broken")
        if item_id == 404:
            raise HTTPException(404)
        return {"id": item_id}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def _get(path: str) -> int:
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return (await client.get(path)).status_code


def _count(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_requests_are_counted_by_route_pattern_not_by_path() -> None:
    labels = {"method": "GET", "route": "/items/{item_id}", "status": "200"}
    before = _count("ragforge_http_requests_total", **labels)
    timed_before = _count(
        "ragforge_http_request_duration_seconds_count", method="GET", route="/items/{item_id}"
    )

    await _get("/items/1")
    await _get("/items/2")

    assert _count("ragforge_http_requests_total", **labels) == before + 2
    assert (
        _count(
            "ragforge_http_request_duration_seconds_count", method="GET", route="/items/{item_id}"
        )
        == timed_before + 2
    )


async def test_unknown_paths_share_one_label_and_health_checks_are_not_counted() -> None:
    unmatched = {"method": "GET", "route": "unmatched", "status": "404"}
    before = _count("ragforge_http_requests_total", **unmatched)
    health_before = _count(
        "ragforge_http_requests_total", method="GET", route="/health", status="200"
    )

    await _get("/random/path/123")
    await _get("/health")

    assert _count("ragforge_http_requests_total", **unmatched) == before + 1
    assert (
        _count("ragforge_http_requests_total", method="GET", route="/health", status="200")
        == health_before
    )


class _JsonLines(logging.Handler):
    """Log lines as our JSON formatter writes them (with the log context of that moment)."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(JsonFormatter())
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


async def test_each_request_is_one_trace_with_fastapis_spans(
    client: AsyncClient, spans: InMemorySpanExporter
) -> None:
    middleware_logger = logging.getLogger("api.middleware")
    lines = _JsonLines()
    middleware_logger.addHandler(lines)
    level = middleware_logger.level
    middleware_logger.setLevel(logging.INFO)
    try:
        response = await client.get("/widget.js")
    finally:
        middleware_logger.removeHandler(lines)
        middleware_logger.setLevel(level)

    finished = spans.get_finished_spans()
    [request] = [s for s in finished if s.kind is SpanKind.SERVER]
    assert response.status_code == 200
    assert request.name == "GET /widget.js"
    assert request.attributes is not None
    assert request.attributes["http.response.status_code"] == 200
    # FastAPI also times the dependencies, the endpoint and the response, inside it.
    assert {s.context.trace_id for s in finished} == {request.context.trace_id}
    assert any(s.name == "fastapi.endpoint" for s in finished)
    # The access log line names the trace, so logs lead to traces.
    [access] = lines.lines
    assert access["trace_id"] == format(request.context.trace_id, "032x")


async def test_health_checks_and_metrics_make_no_spans(
    client: AsyncClient, spans: InMemorySpanExporter
) -> None:
    await client.get("/health")
    await client.get("/metrics")

    assert spans.get_finished_spans() == ()


def test_without_an_endpoint_tracing_stays_off(settings: Settings) -> None:
    assert setup_tracing(settings, "ragforge-api") is None


def test_a_job_continues_the_trace_of_the_request_that_made_it(
    spans: InMemorySpanExporter,
) -> None:
    with span("POST /v1/documents"):
        traceparent = current_traceparent()
    with job_span("job.ingest", traceparent), span("ingest.parse"):
        pass

    request, parse, job = spans.get_finished_spans()
    assert traceparent is not None
    assert TRACEPARENT.fullmatch(traceparent)
    assert job.context.trace_id == parse.context.trace_id == request.context.trace_id
    assert job.parent is not None
    assert job.parent.span_id == request.context.span_id
    assert job.kind is SpanKind.CONSUMER


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, item: Any) -> None:
        self.added.append(item)


def test_a_job_saved_in_the_outbox_takes_the_trace_along(spans: InMemorySpanExporter) -> None:
    session = _FakeSession()
    job = Job(JobType.INGEST, uuid.uuid4(), uuid.uuid4())

    with span("POST /v1/documents"):
        add_job(session, job)  # type: ignore[arg-type]

    [message] = session.added
    assert isinstance(message, OutboxMessage)
    assert TRACEPARENT.fullmatch(message.payload["traceparent"])
    again = Job.from_message_body(
        Job.from_outbox(JobType.INGEST, message.payload).to_message(message_id="1").body
    )
    assert again.traceparent == message.payload["traceparent"]


def test_jobs_without_tracing_have_no_trace_context() -> None:
    job = Job(JobType.DELETE, uuid.uuid4(), uuid.uuid4())

    assert "traceparent" not in job.payload()
    assert Job.from_outbox(JobType.DELETE, job.payload()).traceparent is None


# ------------------------------------------------------------------ dashboard ----


def _defined_metrics() -> set[str]:
    names: set[str] = set()
    for value in vars(metrics).values():
        if isinstance(value, Counter | Gauge | Histogram):
            names.update(family.name for family in value.describe())
    return names


def _expressions(dashboard: dict[str, Any]) -> list[str]:
    return [target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", [])]


def test_every_ragforge_metric_in_the_dashboard_exists() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    used = {
        re.sub(r"_(total|bucket|count|sum)$", "", name)
        for expression in _expressions(dashboard)
        for name in re.findall(r"\bragforge_[a-z_]+\b", expression)
    }

    assert used  # the dashboard does use our metrics
    assert used - _defined_metrics() == set()


def test_the_dashboard_uses_only_provisioned_data_sources() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    provisioned = set(re.findall(r"^\s+uid: (\S+)$", DATASOURCES.read_text(), flags=re.M))
    used = {panel["datasource"]["uid"] for panel in dashboard["panels"] if "datasource" in panel}
    ids = [panel["id"] for panel in dashboard["panels"]]

    assert used <= provisioned
    assert len(ids) == len(set(ids))
    assert dashboard["uid"] == "ragforge"


async def test_known_label_values_start_at_zero(client: AsyncClient) -> None:
    """So Grafana shows 0 (not "No data") before the first LLM error or 429."""
    response = await client.get("/metrics")

    for line in [
        'ragforge_llm_errors_total{kind="busy"}',
        'ragforge_rate_limited_total{limit="login"}',
        'ragforge_chat_answers_total{source="semantic_cache"}',
    ]:
        assert line in response.text
