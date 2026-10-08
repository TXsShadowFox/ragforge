"""Prometheus metrics of the API and the worker (Prometheus reads them every 15 s).

Names start with `ragforge_`. Labels only take a few fixed values (route patterns, not
raw paths, and never tenant IDs), so the number of time series stays small. The Grafana
dashboard (infra/grafana/dashboards/ragforge.json) uses these names; a test checks that.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

# Bucket limits in seconds: requests and search steps are fast, LLM calls and jobs are not.
FAST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
SLOW_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0, 120.0, 300.0)

# ------------------------------------------------------------------------- API ----
HTTP_REQUESTS = Counter(
    "ragforge_http_requests",
    "HTTP requests by route pattern and status code.",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "ragforge_http_request_duration_seconds",
    "Time to answer an HTTP request, until its last byte (a streamed answer too).",
    ["method", "route"],
    buckets=FAST_BUCKETS,
)
CHAT_ANSWERS = Counter(
    "ragforge_chat_answers",
    "Chat answers by source: llm, exact_cache, semantic_cache, or no_sources (I don't know).",
    ["source"],
)
LLM_DURATION = Histogram(
    "ragforge_llm_request_duration_seconds",
    "Time of one LLM call until its last token. operation: answer or rewrite.",
    ["operation"],
    buckets=SLOW_BUCKETS,
)
LLM_FIRST_TOKEN = Histogram(
    "ragforge_llm_first_token_seconds",
    "Time until the first piece of a streamed answer.",
    buckets=SLOW_BUCKETS,
)
LLM_TOKENS = Counter(
    "ragforge_llm_tokens",
    "Tokens the LLM read (direction=input) and wrote (direction=output).",
    ["operation", "direction"],
)
LLM_ERRORS = Counter(
    "ragforge_llm_errors",
    "LLM calls that failed. kind: busy (429) or error.",
    ["kind"],
)
LLM_COST = Counter("ragforge_llm_cost_usd", "The price of the LLM calls, in US dollars.")
RETRIEVAL_DURATION = Histogram(
    "ragforge_retrieval_step_duration_seconds",
    "Time of each search step: embed, vector_search, keyword_search, load_chunks, rerank.",
    ["step"],
    buckets=FAST_BUCKETS,
)
RATE_LIMITED = Counter(
    "ragforge_rate_limited",
    "Requests refused with 429. limit: requests, questions, visitor or login.",
    ["limit"],
)

# ---------------------------------------------------------------------- worker ----
JOBS = Counter(
    "ragforge_jobs",
    "Finished document jobs. outcome: done, bad_file (fails at once), retried, or dead.",
    ["type", "outcome"],
)
JOB_DURATION = Histogram(
    "ragforge_job_duration_seconds",
    "Time of one document job (one try).",
    ["type"],
    buckets=SLOW_BUCKETS,
)
INGESTED_CHUNKS = Counter("ragforge_ingested_chunks", "Chunks made from uploaded documents.")
OUTBOX_PENDING = Gauge(
    "ragforge_outbox_pending",
    "Jobs saved in the outbox but not sent to RabbitMQ yet (grows while RabbitMQ is down).",
)


def start_api_metrics() -> None:
    """Create the API's known label values at 0. Otherwise a series appears only with its
    first event: panels show "No data" instead of 0, and rate() misses that first event."""
    for source in ("llm", "exact_cache", "semantic_cache", "no_sources"):
        CHAT_ANSWERS.labels(source=source)
    for kind in ("busy", "error"):
        LLM_ERRORS.labels(kind=kind)
    for limit in ("requests", "questions", "visitor", "login"):
        RATE_LIMITED.labels(limit=limit)


def start_worker_metrics() -> None:
    """The worker's known label values at 0 (see start_api_metrics)."""
    for job_type in ("ingest", "delete"):
        for outcome in ("done", "bad_file", "retried", "dead"):
            JOBS.labels(type=job_type, outcome=outcome)


@contextmanager
def timer(histogram: Histogram, **labels: str) -> Iterator[None]:
    """Observe how long the `with` block takes, also when it fails."""
    started = time.perf_counter()
    try:
        yield
    finally:
        seconds = time.perf_counter() - started
        (histogram.labels(**labels) if labels else histogram).observe(seconds)
