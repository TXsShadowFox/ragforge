"""Run the ingestion worker: `python -m worker` (or `make worker`)."""

import asyncio

from prometheus_client import start_http_server

from shared.config import get_settings
from shared.logging import configure_logging
from shared.tracing import setup_tracing
from worker.runner import run_worker


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Prometheus reads the worker's metrics here (the API serves its own at /metrics).
    start_http_server(settings.worker_metrics_port)
    tracing = setup_tracing(settings, "ragforge-worker")
    try:
        asyncio.run(run_worker(settings, handle_signals=True))
    finally:
        if tracing is not None:
            tracing.shutdown()  # sends the last spans


if __name__ == "__main__":
    main()
