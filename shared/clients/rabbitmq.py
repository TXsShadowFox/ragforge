"""RabbitMQ connection: the job queue between the API and the ingestion workers.

Phase 2 adds a long-lived connection for publishing and consuming jobs.
For now we only need a health probe.
"""

import aio_pika

from shared.config import Settings


async def ping(settings: Settings) -> None:
    """Open and close a connection. Raises if RabbitMQ cannot be reached."""
    connection = await aio_pika.connect(
        str(settings.rabbitmq_url), timeout=settings.ready_check_timeout_seconds
    )
    await connection.close()
