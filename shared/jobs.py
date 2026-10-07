"""Document jobs over RabbitMQ: queue names, the message format and retries.

outbox table --relay--> exchange "ragforge" --> queue "document-jobs" --> worker
A failed job waits in "document-jobs.retry.<N>s" for N seconds, then comes back.
After the last retry it goes to "document-jobs.dead", and the document is "failed".
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange

from shared.db.models import JobType

EXCHANGE = "ragforge"
JOBS_QUEUE = "document-jobs"
DEAD_QUEUE = "document-jobs.dead"
ATTEMPT_HEADER = "x-attempt"  # how many times the job has failed so far
ERROR_HEADER = "x-error"  # why it failed the last time


def retry_queue(delay_seconds: int) -> str:
    """Named by its delay: changing the delays makes new queues instead of a conflict."""
    return f"{JOBS_QUEUE}.retry.{delay_seconds}s"


def next_retry_delay(failures: int, delays: Sequence[int]) -> int | None:
    """Seconds to wait after the job failed `failures` times, or None: no retries left."""
    return delays[failures - 1] if failures <= len(delays) else None


@dataclass(frozen=True, slots=True)
class Job:
    type: JobType
    tenant_id: uuid.UUID
    document_id: uuid.UUID

    def payload(self) -> dict[str, str]:
        """What we store in the outbox (the type has its own column)."""
        return {"tenant_id": str(self.tenant_id), "document_id": str(self.document_id)}

    @classmethod
    def from_outbox(cls, job_type: JobType, payload: Mapping[str, Any]) -> Self:
        return cls(
            type=job_type,
            tenant_id=uuid.UUID(payload["tenant_id"]),
            document_id=uuid.UUID(payload["document_id"]),
        )

    def to_message(
        self, *, message_id: str, failures: int = 0, error: str = ""
    ) -> aio_pika.Message:
        headers: dict[str, Any] = {ATTEMPT_HEADER: failures}
        if error:
            headers[ERROR_HEADER] = error[:1000]
        return aio_pika.Message(
            body=json.dumps({"type": self.type.value, **self.payload()}).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,  # survives a RabbitMQ restart
            message_id=message_id,
            headers=headers,
        )

    @classmethod
    def from_message_body(cls, body: bytes) -> Self:
        data = json.loads(body)
        return cls.from_outbox(JobType(data["type"]), data)


async def declare_topology(
    channel: AbstractChannel, retry_delays: Sequence[int]
) -> AbstractExchange:
    """Create the exchange and the queues if they do not exist yet. Safe to call many times."""
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True)
    jobs = await channel.declare_queue(JOBS_QUEUE, durable=True)
    await jobs.bind(exchange, routing_key=JOBS_QUEUE)
    for delay in sorted(set(retry_delays)):
        waiting = await channel.declare_queue(
            retry_queue(delay),
            durable=True,
            arguments={
                # After `delay` seconds the message "dies" and goes back to the jobs queue.
                "x-message-ttl": delay * 1000,
                "x-dead-letter-exchange": EXCHANGE,
                "x-dead-letter-routing-key": JOBS_QUEUE,
            },
        )
        await waiting.bind(exchange, routing_key=waiting.name)
    dead = await channel.declare_queue(DEAD_QUEUE, durable=True)
    await dead.bind(exchange, routing_key=DEAD_QUEUE)
    return exchange
