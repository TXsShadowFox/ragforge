"""Tracing with OpenTelemetry: how long each step of a request or a job takes.

A trace is a tree of spans: "POST /v1/chat" contains "retrieval.rerank", "llm.answer", ...
FastAPI makes each request's span (api/main.py); this module adds ours inside it.
Spans go to OTLP_TRACES_ENDPOINT (Jaeger in Docker). Without that setting, no tracer is
installed and every span is a cheap no-op.

The trace context travels with document jobs (in the outbox payload and the RabbitMQ
message), so one trace shows an upload and then the worker processing the file.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio
from opentelemetry.trace import Span, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import AttributeValue

from shared.config import Settings

# Our spans' instrumentation name. Spans are no-ops until a tracer provider is installed.
tracer = trace.get_tracer("ragforge")
_propagator = TraceContextTextMapPropagator()


def setup_tracing(settings: Settings, service_name: str) -> TracerProvider | None:
    """Send this process's spans to the OTLP endpoint. None (and no tracing) without one.

    Call it once per process, at startup; call `shutdown()` on the result at the end, so
    the last spans are sent.
    """
    if not settings.otlp_traces_endpoint:
        return None
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": service_name, "deployment.environment.name": settings.app_env}
        ),
        sampler=ParentBasedTraceIdRatio(settings.trace_sample_ratio),
    )
    # A background thread sends spans in batches: requests never wait for the exporter.
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_traces_endpoint))
    )
    trace.set_tracer_provider(provider)
    return provider


@contextmanager
def span(name: str, **attributes: AttributeValue) -> Iterator[Span]:
    """A child span of the current one. An exception marks it as failed (and goes on)."""
    with tracer.start_as_current_span(name, attributes=attributes) as current:
        yield current


@contextmanager
def job_span(name: str, traceparent: str | None, **attributes: AttributeValue) -> Iterator[Span]:
    """The span of a background job, inside the trace of the request that created it."""
    parent = _propagator.extract({"traceparent": traceparent} if traceparent else {})
    with tracer.start_as_current_span(
        name, context=parent, kind=SpanKind.CONSUMER, attributes=attributes
    ) as current:
        yield current


def trace_id_fields() -> dict[str, str]:
    """{"trace_id": ...} of the current span, for the log context (empty without a trace),
    so a log line leads to its trace."""
    span_context = trace.get_current_span().get_span_context()
    return {"trace_id": format(span_context.trace_id, "032x")} if span_context.is_valid else {}


def current_traceparent() -> str | None:
    """The current trace context as a W3C `traceparent` value, to send with a job."""
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier.get("traceparent")
