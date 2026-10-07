"""Prepare every data store once: `python -m shared.init`.

Runs the database migrations and creates the storage bucket, the Qdrant collection and
the RabbitMQ queues. Every step is safe to run again. The Docker `init` service and
`make dev` run it before the API and the worker start.
"""

import asyncio
import logging

import aio_pika

from shared.clients import Clients
from shared.clients.storage import ensure_bucket
from shared.config import Settings, get_settings
from shared.db.migrate import upgrade
from shared.embeddings import embedding_dimension
from shared.jobs import declare_topology
from shared.logging import configure_logging
from shared.vector_store import ensure_collection

logger = logging.getLogger(__name__)


async def init_stores(settings: Settings, *, vector_dimension: int | None = None) -> None:
    """`vector_dimension` is for tests with a fake embedder; normally the model decides it."""
    if vector_dimension is None:
        vector_dimension = embedding_dimension(settings.embedding_model)
    clients = Clients.create(settings)
    try:
        await upgrade(clients.db)
        await ensure_bucket(clients.s3, settings.s3_bucket)
        await ensure_collection(clients.qdrant, settings.qdrant_collection, vector_dimension)
        connection = await aio_pika.connect(str(settings.rabbitmq_url))
        async with connection:
            channel = await connection.channel()
            await declare_topology(channel, settings.ingest_retry_delays_seconds)
    finally:
        await clients.aclose()
    logger.info("The database, storage, vector store and queues are ready")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await init_stores(settings)


if __name__ == "__main__":
    asyncio.run(main())
