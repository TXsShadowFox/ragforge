"""Take jobs from RabbitMQ and run them, with retries and a dead-letter queue.

- A bad file (BadDocumentError) fails at once: retrying cannot fix it.
- Any other error is treated as temporary: the job goes to a retry queue and comes back
  after a delay (10 s, 1 min, 5 min by default). After the last retry it goes to the
  dead-letter queue, and the document is marked "failed".
"""

import asyncio
import logging
from collections.abc import Sequence

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractIncomingMessage

from shared.jobs import (
    ATTEMPT_HEADER,
    DEAD_QUEUE,
    ERROR_HEADER,
    Job,
    next_retry_delay,
    retry_queue,
)
from shared.logging import log_context
from worker.parsing import BadDocumentError
from worker.pipeline import JobContext, mark_failed, run_job

logger = logging.getLogger(__name__)

# If a job cannot even be finished (e.g. Postgres is down), wait this long before RabbitMQ
# gives it to us again, so we do not spin.
REQUEUE_PAUSE_SECONDS = 5.0


class JobConsumer:
    def __init__(
        self, context: JobContext, exchange: AbstractExchange, retry_delays: Sequence[int]
    ) -> None:
        self._context = context
        self._exchange = exchange  # for publishing retries and dead jobs
        self._retry_delays = retry_delays
        self._idle = asyncio.Event()
        self._idle.set()

    async def wait_until_idle(self) -> None:
        """Wait for the job that is running now (if any) to finish."""
        await self._idle.wait()

    async def handle(self, message: AbstractIncomingMessage) -> None:
        """Run one job. It always ends with the message acknowledged or given back."""
        self._idle.clear()
        try:
            with log_context(job_id=message.message_id or "?"):
                await self._handle(message)
        except Exception:
            logger.exception("Could not finish a job; RabbitMQ will deliver it again")
            await asyncio.sleep(REQUEUE_PAUSE_SECONDS)
            await message.nack(requeue=True)
        finally:
            self._idle.set()

    async def _handle(self, message: AbstractIncomingMessage) -> None:
        try:
            job = Job.from_message_body(message.body)
        except (ValueError, KeyError, TypeError):
            logger.exception("A job message is not valid; sending it to the dead-letter queue")
            invalid = aio_pika.Message(
                body=message.body,
                headers={ERROR_HEADER: "not a valid job message"},
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            await self._exchange.publish(invalid, routing_key=DEAD_QUEUE, mandatory=True)
            await message.ack()
            return

        attempt_header = message.headers.get(ATTEMPT_HEADER, 0)
        failures = attempt_header if isinstance(attempt_header, int) else 0
        with log_context(
            job_type=job.type.value,
            tenant_id=str(job.tenant_id),
            document_id=str(job.document_id),
            attempt=str(failures + 1),
        ):
            await self._run(job, message, failures)
        await message.ack()

    async def _run(self, job: Job, message: AbstractIncomingMessage, failures: int) -> None:
        try:
            await run_job(self._context, job)
        except BadDocumentError as exc:
            logger.warning("The file cannot be processed: %s", exc)
            await mark_failed(self._context, job, str(exc))
        except Exception as exc:
            failures += 1
            error = f"{type(exc).__name__}: {exc}"
            delay = next_retry_delay(failures, self._retry_delays)
            if delay is None:
                logger.exception("The job failed %d times; giving up", failures)
                await mark_failed(
                    self._context,
                    job,
                    f"Processing failed {failures} times. Please try again later.",
                )
                await self._publish(job, message, DEAD_QUEUE, failures, error)
            else:
                logger.warning("The job failed; trying again in %d s", delay, exc_info=True)
                await self._publish(job, message, retry_queue(delay), failures, error)

    async def _publish(
        self, job: Job, message: AbstractIncomingMessage, queue: str, failures: int, error: str
    ) -> None:
        retry = job.to_message(message_id=message.message_id or "", failures=failures, error=error)
        await self._exchange.publish(retry, routing_key=queue, mandatory=True)
