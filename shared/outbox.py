"""The outbox: jobs saved in Postgres, in the same transaction as the change they belong to.

The API only writes here; the worker's relay (worker/relay.py) sends the jobs to RabbitMQ.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import OutboxMessage
from shared.jobs import Job


def add_job(session: AsyncSession, job: Job) -> None:
    """Save `job` in the outbox. It goes to RabbitMQ only if the transaction commits."""
    session.add(OutboxMessage(tenant_id=job.tenant_id, job_type=job.type, payload=job.payload()))
