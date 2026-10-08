"""The worker process: load the model, relay the outbox, and run jobs until told to stop."""

import asyncio
import contextlib
import logging
import signal

import aio_pika

from shared import metrics
from shared.answer_cache import forget_expired_answers
from shared.clients import Clients
from shared.config import Settings
from shared.embeddings import Embedder, FastEmbedEmbedder
from shared.jobs import JOBS_QUEUE, declare_topology
from worker.consumer import JobConsumer
from worker.pipeline import JobContext
from worker.relay import relay_forever

logger = logging.getLogger(__name__)

# The model adds a start token and an end token to every chunk.
SPECIAL_TOKENS = 2
CACHE_CLEANUP_SECONDS = 60 * 60


async def run_worker(
    settings: Settings,
    *,
    embedder: Embedder | None = None,
    stop: asyncio.Event | None = None,
    handle_signals: bool = False,
) -> None:
    """Run until `stop` is set (or, with `handle_signals`, until SIGTERM / Ctrl+C)."""
    stop = stop or asyncio.Event()
    metrics.start_worker_metrics()
    if handle_signals:
        _stop_on_signals(stop)
    if embedder is None:
        logger.info("Loading the embedding model %s", settings.embedding_model)
        embedder = await asyncio.to_thread(
            FastEmbedEmbedder,
            settings.embedding_model,
            settings.model_cache_dir,
            settings.embedding_batch_size,
        )
    _check_chunk_size(settings, embedder)

    clients = Clients.create(settings)
    connection = await aio_pika.connect_robust(str(settings.rabbitmq_url))
    try:
        # Separate channels for publishing and consuming (a RabbitMQ best practice).
        publish_channel = await connection.channel()
        exchange = await declare_topology(publish_channel, settings.ingest_retry_delays_seconds)
        consume_channel = await connection.channel()
        await consume_channel.set_qos(prefetch_count=1)  # one job at a time per worker
        queue = await consume_channel.get_queue(JOBS_QUEUE)

        context = JobContext(settings=settings, clients=clients, embedder=embedder)
        consumer = JobConsumer(context, exchange, settings.ingest_retry_delays_seconds)
        consumer_tag = await queue.consume(consumer.handle)
        background = [
            asyncio.create_task(
                relay_forever(clients.sessions, exchange, settings.outbox_poll_seconds)
            ),
            asyncio.create_task(_clean_answer_cache_forever(clients, settings)),
        ]
        logger.info("The worker is ready")

        await stop.wait()
        logger.info("Stopping: the current job (if any) will finish first")
        await queue.cancel(consumer_tag)
        await consumer.wait_until_idle()
        for task in background:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    finally:
        await connection.close()
        await clients.aclose()
    logger.info("The worker stopped")


async def _clean_answer_cache_forever(clients: Clients, settings: Settings) -> None:
    """Delete expired cached answers every hour (Qdrant has no automatic expiry)."""
    while True:
        try:
            await forget_expired_answers(clients.qdrant, settings.answer_cache_collection)
        except Exception:
            logger.warning("Could not clean the answer cache; trying again later", exc_info=True)
        await asyncio.sleep(CACHE_CLEANUP_SECONDS)


def _check_chunk_size(settings: Settings, embedder: Embedder) -> None:
    if settings.chunk_size_tokens + SPECIAL_TOKENS > embedder.max_tokens:
        raise ValueError(
            f"CHUNK_SIZE_TOKENS={settings.chunk_size_tokens} is too big: the model reads at "
            f"most {embedder.max_tokens - SPECIAL_TOKENS} tokens of text."
        )


def _stop_on_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        # Not available on Windows; there Ctrl+C stops the program directly.
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(stop_signal, stop.set)
