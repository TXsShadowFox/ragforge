"""Tests for the job message format, retry delays and the embedding model list."""

import uuid

import pytest

from shared.db.models import JobType
from shared.embeddings import embedding_dimension
from shared.jobs import ATTEMPT_HEADER, ERROR_HEADER, Job, next_retry_delay, retry_queue


def test_a_job_survives_the_trip_through_a_message() -> None:
    job = Job(JobType.INGEST, tenant_id=uuid.uuid4(), document_id=uuid.uuid4())

    message = job.to_message(message_id="m1", failures=2, error="boom")

    assert Job.from_message_body(message.body) == job
    assert message.headers == {ATTEMPT_HEADER: 2, ERROR_HEADER: "boom"}
    assert message.message_id == "m1"


def test_a_job_survives_the_trip_through_the_outbox() -> None:
    job = Job(JobType.DELETE, tenant_id=uuid.uuid4(), document_id=uuid.uuid4())

    assert Job.from_outbox(JobType.DELETE, job.payload()) == job


@pytest.mark.parametrize(
    ("failures", "delay"),
    [(1, 10), (2, 60), (3, 300), (4, None)],  # 3 retries, then the dead-letter queue
)
def test_retry_delays_grow_and_then_stop(failures: int, delay: int | None) -> None:
    assert next_retry_delay(failures, [10, 60, 300]) == delay


def test_retry_queues_are_named_by_their_delay() -> None:
    assert retry_queue(60) == "document-jobs.retry.60s"


def test_the_default_model_makes_384_numbers_per_vector() -> None:
    # Read from fastembed's model list: nothing is downloaded.
    assert embedding_dimension("BAAI/bge-small-en-v1.5") == 384


def test_an_unknown_model_is_an_error() -> None:
    with pytest.raises(ValueError, match="Unknown embedding model"):
        embedding_dimension("no/such-model")
