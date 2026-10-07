"""The outbox relay: sends jobs from the Postgres outbox table to RabbitMQ.

A job is deleted from the outbox only after RabbitMQ confirmed it. If sending fails, the
job stays and is sent on a later round, maybe twice; jobs are safe to run twice.
`SKIP LOCKED` lets several workers relay at the same time without sending a job twice.
"""

import asyncio
import logging

from aio_pika.abc import AbstractExchange
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db.models import OutboxMessage
from shared.jobs import JOBS_QUEUE, Job

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


async def relay_once(sessions: async_sessionmaker[AsyncSession], exchange: AbstractExchange) -> int:
    """Send one batch of waiting jobs. Returns how many were sent."""
    async with sessions() as session, session.begin():
        messages = list(
            await session.scalars(
                select(OutboxMessage)
                .order_by(OutboxMessage.id)
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
        )
        for message in messages:
            job = Job.from_outbox(message.job_type, message.payload)
            # With publisher confirms (aio-pika's default), this waits until RabbitMQ has it.
            # mandatory=True: fail if no queue takes it, instead of losing it.
            await exchange.publish(
                job.to_message(message_id=str(message.id)), routing_key=JOBS_QUEUE, mandatory=True
            )
        if messages:
            sent_ids = [message.id for message in messages]
            await session.execute(delete(OutboxMessage).where(OutboxMessage.id.in_(sent_ids)))
    if messages:
        logger.info("Sent %d job(s) to RabbitMQ", len(messages))
    return len(messages)


async def relay_forever(
    sessions: async_sessionmaker[AsyncSession], exchange: AbstractExchange, poll_seconds: float
) -> None:
    """Keep sending jobs until cancelled. A full batch means more may be waiting: no pause."""
    while True:
        try:
            sent = await relay_once(sessions, exchange)
        except Exception:
            logger.exception("Could not send outbox jobs; trying again soon")
            sent = 0
        if sent < BATCH_SIZE:
            await asyncio.sleep(poll_seconds)
